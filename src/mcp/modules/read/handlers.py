"""
读取模块处理器
实现read_file, list_directory, stat_path, exists接口
"""

import os
import stat
import asyncio
from pathlib import Path
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple, Union
import aiofiles
import chardet
import structlog

from src.mcp.core.config import MCPConfig, get_config
from src.mcp.core.security import SecurityManager
from src.mcp.core.cache import CacheManager, get_cache_manager
from src.mcp.core.exceptions import (
    MCPError,
    FileNotFoundError as MCPFileNotFoundError,
    PermissionDeniedError,
    SizeLimitExceededError,
    InvalidParameterError,
    EncodingError,
    TimeoutError as MCPTimeoutError,
)

logger = structlog.get_logger(__name__)


class ReadHandler:
    """读取模块处理器"""
    
    def __init__(
        self, 
        config: Optional[MCPConfig] = None,
        security: Optional[SecurityManager] = None,
        cache: Optional[CacheManager] = None
    ):
        self.config = config or get_config()
        self.security = security or SecurityManager(self.config)
        self.cache = cache or get_cache_manager()
    
    async def read_file(
        self,
        path: str,
        encoding: str = "utf-8",
        range: Optional[Tuple[int, int]] = None,
        max_size: int = 1048576,  # 1MB
        timeout: int = 30000
    ) -> Dict[str, Any]:
        """
        读取文件内容
        
        Args:
            path: 文件路径
            encoding: 文件编码
            range: 读取的字节范围 [start, end]
            max_size: 最大读取字节数
            timeout: 超时时间(毫秒)
            
        Returns:
            包含文件内容的响应字典
        """
        start_time = datetime.now(timezone.utc)
        
        try:
            # 验证路径安全性
            validated_path = self.security.validate_path(path)
            
            # 检查文件是否存在
            if not validated_path.exists():
                raise MCPFileNotFoundError(path=str(validated_path))
            
            if not validated_path.is_file():
                raise InvalidParameterError(
                    parameter="path",
                    value=path,
                    reason="路径不是文件"
                )
            
            # 检查读取权限
            self.security.check_file_permission(validated_path, "read")
            
            # 获取文件大小
            file_size = validated_path.stat().st_size
            
            # 检查文件大小限制
            max_allowed = min(max_size, self.config.get_max_file_size_bytes())
            if file_size > max_allowed and range is None:
                raise SizeLimitExceededError(
                    path=str(validated_path),
                    size=file_size,
                    limit=max_allowed
                )
            
            # 计算读取范围
            start_pos = 0
            end_pos = min(file_size, max_allowed)
            
            if range:
                if len(range) != 2:
                    raise InvalidParameterError(
                        parameter="range",
                        value=range,
                        reason="range必须是[start, end]格式"
                    )
                start_pos, end_pos = range
                if start_pos < 0 or end_pos < start_pos:
                    raise InvalidParameterError(
                        parameter="range",
                        value=range,
                        reason="无效的范围值"
                    )
                end_pos = min(end_pos, file_size)
            
            # 验证编码
            validated_encoding = self.security.validate_encoding(encoding)
            
            # 读取文件内容
            async def do_read():
                async with aiofiles.open(validated_path, 'rb') as f:
                    if start_pos > 0:
                        await f.seek(start_pos)
                    content_bytes = await f.read(end_pos - start_pos)
                return content_bytes
            
            try:
                content_bytes = await asyncio.wait_for(
                    do_read(),
                    timeout=timeout / 1000
                )
            except asyncio.TimeoutError:
                raise MCPTimeoutError(operation="read_file", timeout_ms=timeout)
            
            # UTF-8 边界对齐：range 读取时可能切在多字节字符中间
            if range and validated_encoding.lower().replace('-', '') in ('utf8', 'utf'):
                # 修剪开头的 continuation bytes (0x80-0xBF)
                trim_start = 0
                while trim_start < len(content_bytes) and (content_bytes[trim_start] & 0xC0) == 0x80:
                    trim_start += 1
                # 修剪结尾的不完整多字节序列
                trim_end = len(content_bytes)
                if trim_end > 0:
                    # 从末尾向前找最后一个起始字节
                    i = trim_end - 1
                    while i >= trim_start and (content_bytes[i] & 0xC0) == 0x80:
                        i -= 1
                    if i >= trim_start:
                        byte = content_bytes[i]
                        if byte < 0x80:
                            char_len = 1
                        elif (byte & 0xE0) == 0xC0:
                            char_len = 2
                        elif (byte & 0xF0) == 0xE0:
                            char_len = 3
                        elif (byte & 0xF8) == 0xF0:
                            char_len = 4
                        else:
                            char_len = 1
                        # 如果最后一个字符不完整，截掉
                        if i + char_len > trim_end:
                            trim_end = i
                content_bytes = content_bytes[trim_start:trim_end]
            
            # 尝试解码
            try:
                # 如果编码为auto，自动检测
                if validated_encoding.lower() == "auto":
                    detected = chardet.detect(content_bytes)
                    validated_encoding = detected.get('encoding', 'utf-8') or 'utf-8'
                
                content = content_bytes.decode(validated_encoding)
            except UnicodeDecodeError as e:
                # 尝试自动检测编码
                detected = chardet.detect(content_bytes)
                detected_encoding = detected.get('encoding')
                
                if detected_encoding and detected_encoding != validated_encoding:
                    try:
                        content = content_bytes.decode(detected_encoding)
                        validated_encoding = detected_encoding
                    except UnicodeDecodeError:
                        raise EncodingError(
                            path=str(validated_path),
                            encoding=validated_encoding
                        )
                else:
                    raise EncodingError(
                        path=str(validated_path),
                        encoding=validated_encoding
                    )
            
            # 计算校验和
            checksum = self.security.compute_checksum(content_bytes)
            
            # 计算耗时
            duration = (datetime.now(timezone.utc) - start_time).total_seconds() * 1000
            
            # 审计日志
            self.security.log_audit(
                "read_file",
                path=str(validated_path),
                details={"size": len(content_bytes), "encoding": validated_encoding}
            )
            
            return {
                "status": "success",
                "data": {
                    "content": content,
                    "encoding": validated_encoding,
                    "size": len(content_bytes),
                    "path": str(validated_path),
                    "read_range": [start_pos, start_pos + len(content_bytes)],
                    "checksum": checksum,
                },
                "metadata": {
                    "operation": "read_file",
                    "timestamp": start_time.isoformat(),
                    "duration_ms": round(duration, 2),
                }
            }
            
        except MCPError:
            raise
        except Exception as e:
            logger.error("读取文件失败", path=path, error=str(e))
            raise MCPError(f"读取文件失败: {str(e)}", cause=e)
    
    async def list_directory(
        self,
        path: str,
        limit: int = 100,
        offset: int = 0,
        pattern: Optional[str] = None,
        recursive: bool = False,
        max_depth: int = 3,
        include_hidden: bool = False,
        sort_by: str = "name",
        sort_order: str = "asc"
    ) -> Dict[str, Any]:
        """
        列出目录内容
        
        Args:
            path: 目录路径
            limit: 最大返回条目数
            offset: 分页偏移量
            pattern: 文件名匹配模式
            recursive: 是否递归列出
            max_depth: 最大递归深度
            include_hidden: 是否包含隐藏文件
            sort_by: 排序字段
            sort_order: 排序顺序
            
        Returns:
            包含目录内容的响应字典
        """
        start_time = datetime.now(timezone.utc)
        
        try:
            # 验证参数
            if limit < 1 or limit > self.config.performance.max_list_items:
                limit = min(max(1, limit), self.config.performance.max_list_items)
            
            if offset < 0:
                offset = 0
            
            if sort_by not in ["name", "size", "modified", "created"]:
                sort_by = "name"
            
            if sort_order not in ["asc", "desc"]:
                sort_order = "asc"
            
            if max_depth < 1 or max_depth > self.config.workspace.max_depth:
                max_depth = min(max(1, max_depth), self.config.workspace.max_depth)
            
            # 验证路径安全性
            validated_path = self.security.validate_path(path)
            
            # 检查目录是否存在
            if not validated_path.exists():
                raise MCPFileNotFoundError(path=str(validated_path))
            
            if not validated_path.is_dir():
                raise InvalidParameterError(
                    parameter="path",
                    value=path,
                    reason="路径不是目录"
                )
            
            # 检查读取权限
            self.security.check_file_permission(validated_path, "read")
            
            # 尝试从缓存获取
            cached = self.cache.get_directory(
                str(validated_path), 
                pattern=pattern,
                recursive=recursive,
                offset=offset,
                limit=limit
            )
            if cached:
                return cached
            
            # 收集目录项
            all_items: List[Dict[str, Any]] = []
            
            async def scan_directory(dir_path: Path, current_depth: int = 0):
                """递归扫描目录"""
                if current_depth > max_depth:
                    return
                
                try:
                    entries = list(dir_path.iterdir())
                except PermissionError:
                    return
                
                for entry in entries:
                    try:
                        # 检查隐藏文件
                        if not include_hidden and self._is_hidden(entry):
                            continue
                        
                        # 应用模式匹配
                        if pattern and not self._match_pattern(entry.name, pattern):
                            if not (recursive and entry.is_dir()):
                                continue
                        
                        # 获取文件信息
                        item_info = await self._get_item_info(entry, validated_path)
                        
                        if pattern is None or self._match_pattern(entry.name, pattern):
                            all_items.append(item_info)
                        
                        # 递归处理子目录
                        if recursive and entry.is_dir() and current_depth < max_depth:
                            await scan_directory(entry, current_depth + 1)
                    
                    except (PermissionError, OSError) as e:
                        logger.debug("跳过无法访问的项", path=str(entry), error=str(e))
                        continue
            
            await scan_directory(validated_path)
            
            # 排序
            reverse = sort_order == "desc"
            if sort_by == "name":
                all_items.sort(key=lambda x: x["name"].lower(), reverse=reverse)
            elif sort_by == "size":
                all_items.sort(key=lambda x: x["size"], reverse=reverse)
            elif sort_by == "modified":
                all_items.sort(key=lambda x: x["modified"], reverse=reverse)
            elif sort_by == "created":
                all_items.sort(key=lambda x: x["created"], reverse=reverse)
            
            # 分页
            total = len(all_items)
            paginated_items = all_items[offset:offset + limit]
            
            # 计算耗时
            duration = (datetime.now(timezone.utc) - start_time).total_seconds() * 1000
            
            result = {
                "status": "success",
                "data": {
                    "path": str(validated_path),
                    "items": paginated_items,
                    "pagination": {
                        "total": total,
                        "limit": limit,
                        "offset": offset,
                        "has_more": offset + limit < total,
                    }
                },
                "metadata": {
                    "operation": "list_directory",
                    "timestamp": start_time.isoformat(),
                    "duration_ms": round(duration, 2),
                }
            }
            
            # 缓存结果
            self.cache.set_directory(
                str(validated_path),
                result,
                pattern=pattern,
                recursive=recursive,
                offset=offset,
                limit=limit
            )
            
            return result
            
        except MCPError:
            raise
        except Exception as e:
            logger.error("列出目录失败", path=path, error=str(e))
            raise MCPError(f"列出目录失败: {str(e)}", cause=e)
    
    async def stat_path(
        self,
        path: str,
        follow_symlinks: bool = True
    ) -> Dict[str, Any]:
        """
        获取路径状态信息
        
        Args:
            path: 文件或目录路径
            follow_symlinks: 是否跟随符号链接
            
        Returns:
            包含路径状态的响应字典
        """
        start_time = datetime.now(timezone.utc)
        
        try:
            # 验证路径安全性
            validated_path = self.security.validate_path(path, follow_symlinks)
            
            # 检查是否存在
            if not validated_path.exists():
                return {
                    "status": "success",
                    "data": {
                        "path": str(validated_path),
                        "exists": False,
                    },
                    "metadata": {
                        "operation": "stat_path",
                        "timestamp": start_time.isoformat(),
                        "duration_ms": 0,
                    }
                }
            
            # 获取stat信息
            if follow_symlinks:
                stat_info = validated_path.stat()
            else:
                stat_info = validated_path.lstat()
            
            # 确定类型
            if validated_path.is_file():
                path_type = "file"
            elif validated_path.is_dir():
                path_type = "directory"
            elif validated_path.is_symlink():
                path_type = "symlink"
            else:
                path_type = "other"
            
            # 获取权限信息
            permissions = self._parse_permissions(stat_info.st_mode)
            
            # Windows特定属性
            is_hidden = self._is_hidden(validated_path)
            is_readonly = not os.access(validated_path, os.W_OK)
            is_system = bool(stat_info.st_file_attributes & stat.FILE_ATTRIBUTE_SYSTEM) if hasattr(stat_info, 'st_file_attributes') else False
            is_archive = bool(stat_info.st_file_attributes & stat.FILE_ATTRIBUTE_ARCHIVE) if hasattr(stat_info, 'st_file_attributes') else False
            
            # 计算耗时
            duration = (datetime.now(timezone.utc) - start_time).total_seconds() * 1000
            
            result = {
                "status": "success",
                "data": {
                    "path": str(validated_path),
                    "exists": True,
                    "type": path_type,
                    "size": stat_info.st_size,
                    "created": datetime.fromtimestamp(stat_info.st_ctime, timezone.utc).isoformat(),
                    "modified": datetime.fromtimestamp(stat_info.st_mtime, timezone.utc).isoformat(),
                    "accessed": datetime.fromtimestamp(stat_info.st_atime, timezone.utc).isoformat(),
                    "permissions": permissions,
                    "is_hidden": is_hidden,
                    "is_readonly": is_readonly,
                    "is_system": is_system,
                    "is_archive": is_archive,
                },
                "metadata": {
                    "operation": "stat_path",
                    "timestamp": start_time.isoformat(),
                    "duration_ms": round(duration, 2),
                }
            }
            
            # 缓存元数据
            self.cache.set_metadata(str(validated_path), result["data"])
            
            return result
            
        except MCPError:
            raise
        except Exception as e:
            logger.error("获取路径状态失败", path=path, error=str(e))
            raise MCPError(f"获取路径状态失败: {str(e)}", cause=e)
    
    async def exists(self, path: str) -> Dict[str, Any]:
        """
        检查路径是否存在
        
        Args:
            path: 要检查的路径
            
        Returns:
            包含存在性检查结果的响应字典
        """
        start_time = datetime.now(timezone.utc)
        
        try:
            # 验证路径安全性
            validated_path = self.security.validate_path(path)
            
            exists = validated_path.exists()
            
            return {
                "status": "success",
                "data": {
                    "exists": exists,
                    "path": str(validated_path),
                    "checked_at": start_time.isoformat(),
                },
                "metadata": {
                    "operation": "exists",
                    "timestamp": start_time.isoformat(),
                    "duration_ms": 0,
                }
            }
            
        except MCPError:
            raise
        except Exception as e:
            logger.error("检查路径存在性失败", path=path, error=str(e))
            raise MCPError(f"检查路径存在性失败: {str(e)}", cause=e)
    
    def _is_hidden(self, path: Path) -> bool:
        """检查文件是否隐藏"""
        # Windows隐藏文件检查
        try:
            attrs = path.stat().st_file_attributes
            return bool(attrs & stat.FILE_ATTRIBUTE_HIDDEN)
        except (AttributeError, OSError):
            # 回退到名称检查
            return path.name.startswith('.')
    
    def _match_pattern(self, name: str, pattern: str) -> bool:
        """匹配文件名模式"""
        import fnmatch
        return fnmatch.fnmatch(name.lower(), pattern.lower())
    
    async def _get_item_info(self, entry: Path, base_path: Path) -> Dict[str, Any]:
        """获取目录项信息"""
        try:
            stat_info = entry.stat()
            
            # 计算相对于 base_path 的路径
            try:
                rel_path = str(entry.relative_to(base_path)).replace('\\', '/')
            except ValueError:
                rel_path = entry.name
            
            item = {
                "name": entry.name,
                "path": rel_path,
                "type": "directory" if entry.is_dir() else "file",
                "size": stat_info.st_size,
                "modified": datetime.fromtimestamp(stat_info.st_mtime, timezone.utc).isoformat(),
                "created": datetime.fromtimestamp(stat_info.st_ctime, timezone.utc).isoformat(),
            }
            
            if entry.is_file():
                item["extension"] = entry.suffix
                item["permissions"] = self._format_permissions(stat_info.st_mode)
            else:
                # 对于目录，尝试获取子项数量
                try:
                    item["item_count"] = len(list(entry.iterdir()))
                except PermissionError:
                    item["item_count"] = -1
                item["permissions"] = self._format_permissions(stat_info.st_mode)
            
            return item
            
        except (PermissionError, OSError) as e:
            return {
                "name": entry.name,
                "type": "unknown",
                "size": 0,
                "error": str(e),
            }
    
    def _parse_permissions(self, mode: int) -> Dict[str, Dict[str, bool]]:
        """解析权限模式"""
        return {
            "owner": {
                "read": bool(mode & stat.S_IRUSR),
                "write": bool(mode & stat.S_IWUSR),
                "execute": bool(mode & stat.S_IXUSR),
            },
            "group": {
                "read": bool(mode & stat.S_IRGRP),
                "write": bool(mode & stat.S_IWGRP),
                "execute": bool(mode & stat.S_IXGRP),
            },
            "others": {
                "read": bool(mode & stat.S_IROTH),
                "write": bool(mode & stat.S_IWOTH),
                "execute": bool(mode & stat.S_IXOTH),
            }
        }
    
    def _format_permissions(self, mode: int) -> str:
        """格式化权限为字符串"""
        chars = []
        
        # Owner
        chars.append('r' if mode & stat.S_IRUSR else '-')
        chars.append('w' if mode & stat.S_IWUSR else '-')
        chars.append('x' if mode & stat.S_IXUSR else '-')
        
        # Group
        chars.append('r' if mode & stat.S_IRGRP else '-')
        chars.append('w' if mode & stat.S_IWGRP else '-')
        chars.append('x' if mode & stat.S_IXGRP else '-')
        
        # Others
        chars.append('r' if mode & stat.S_IROTH else '-')
        chars.append('w' if mode & stat.S_IWOTH else '-')
        chars.append('x' if mode & stat.S_IXOTH else '-')
        
        return ''.join(chars)


# 模块级便捷函数
_handler: Optional[ReadHandler] = None


def get_handler() -> ReadHandler:
    """获取处理器实例"""
    global _handler
    if _handler is None:
        _handler = ReadHandler()
    return _handler


def reset_handler() -> None:
    """重置处理器实例（用于运行时配置更新）"""
    global _handler
    _handler = None


async def read_file(
    path: str,
    encoding: str = "utf-8",
    range: Optional[Tuple[int, int]] = None,
    max_size: int = 1048576,
    timeout: int = 30000
) -> Dict[str, Any]:
    """读取文件内容"""
    return await get_handler().read_file(path, encoding, range, max_size, timeout)


async def list_directory(
    path: str,
    limit: int = 100,
    offset: int = 0,
    pattern: Optional[str] = None,
    recursive: bool = False,
    max_depth: int = 3,
    include_hidden: bool = False,
    sort_by: str = "name",
    sort_order: str = "asc"
) -> Dict[str, Any]:
    """列出目录内容"""
    return await get_handler().list_directory(
        path, limit, offset, pattern, recursive, 
        max_depth, include_hidden, sort_by, sort_order
    )


async def stat_path(path: str, follow_symlinks: bool = True) -> Dict[str, Any]:
    """获取路径状态"""
    return await get_handler().stat_path(path, follow_symlinks)


async def exists(path: str) -> Dict[str, Any]:
    """检查路径是否存在"""
    return await get_handler().exists(path)
