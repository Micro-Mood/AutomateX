"""
编辑模块处理器
实现目录操作、文件操作、内容编辑接口
"""

import os
import shutil
import asyncio
from pathlib import Path
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple, Union
import aiofiles
import structlog

from src.mcp.core.config import MCPConfig, get_config
from src.mcp.core.security import SecurityManager
from src.mcp.core.cache import CacheManager, get_cache_manager
from src.mcp.core.exceptions import (
    MCPError,
    FileNotFoundError as MCPFileNotFoundError,
    PermissionDeniedError,
    InvalidParameterError,
    ConcurrentModificationError,
    PatchApplyError,
)

logger = structlog.get_logger(__name__)


def _normalize_newlines(text: str) -> str:
    """规范化换行符，避免文本模式写入时 \\r\\n 被二次转换为 \\r\\r\\n"""
    return text.replace('\r\n', '\n').replace('\r', '\n')


class EditHandler:
    """编辑模块处理器"""
    
    def __init__(
        self,
        config: Optional[MCPConfig] = None,
        security: Optional[SecurityManager] = None,
        cache: Optional[CacheManager] = None
    ):
        self.config = config or get_config()
        self.security = security or SecurityManager(self.config)
        self.cache = cache or get_cache_manager()
    
    # ==================== 目录操作 ====================
    
    async def create_directory(
        self,
        path: str,
        recursive: bool = True,
        mode: str = "755"
    ) -> Dict[str, Any]:
        """
        创建目录
        
        Args:
            path: 目录路径
            recursive: 是否创建父目录
            mode: 权限模式
            
        Returns:
            操作结果响应字典
        """
        start_time = datetime.now(timezone.utc)
        
        try:
            # 验证路径安全性
            validated_path = self.security.validate_path(path)
            
            # 检查是否已存在
            if validated_path.exists():
                if validated_path.is_dir():
                    return {
                        "status": "success",
                        "data": {
                            "path": str(validated_path),
                            "created": False,
                            "already_exists": True,
                        },
                        "metadata": {
                            "operation": "create_directory",
                            "timestamp": start_time.isoformat(),
                            "duration_ms": 0,
                        }
                    }
                else:
                    raise InvalidParameterError(
                        parameter="path",
                        value=path,
                        reason="路径已存在且不是目录"
                    )
            
            # 检查父目录权限
            parent = validated_path.parent
            if parent.exists():
                self.security.check_file_permission(parent, "write")
            elif not recursive:
                raise MCPFileNotFoundError(path=str(parent))
            
            # 创建目录
            parent_created = not validated_path.parent.exists()
            validated_path.mkdir(parents=recursive, exist_ok=True)
            
            # 设置权限（Windows上可能不完全支持）
            try:
                mode_int = int(mode, 8)
                validated_path.chmod(mode_int)
            except (ValueError, OSError):
                pass
            
            # 使缓存失效
            self.cache.invalidate_directory(str(validated_path.parent))
            
            # 审计日志
            self.security.log_audit(
                "create_directory",
                path=str(validated_path),
                details={"recursive": recursive, "mode": mode}
            )
            
            duration = (datetime.now(timezone.utc) - start_time).total_seconds() * 1000
            
            return {
                "status": "success",
                "data": {
                    "path": str(validated_path),
                    "created": True,
                    "parent_created": parent_created,
                    "mode": mode,
                },
                "metadata": {
                    "operation": "create_directory",
                    "timestamp": start_time.isoformat(),
                    "duration_ms": round(duration, 2),
                }
            }
            
        except MCPError:
            raise
        except Exception as e:
            logger.error("创建目录失败", path=path, error=str(e))
            raise MCPError(f"创建目录失败: {str(e)}", cause=e)
    
    async def delete_directory(
        self,
        path: str,
        recursive: bool = False,
        force: bool = False
    ) -> Dict[str, Any]:
        """
        删除目录
        
        Args:
            path: 目录路径
            recursive: 是否递归删除
            force: 是否强制删除只读文件
            
        Returns:
            操作结果响应字典
        """
        start_time = datetime.now(timezone.utc)
        
        try:
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
            
            # 检查删除权限
            self.security.check_file_permission(validated_path, "delete")
            
            # 检查是否为空
            if not recursive:
                try:
                    next(validated_path.iterdir())
                    raise InvalidParameterError(
                        parameter="recursive",
                        value=False,
                        reason="目录不为空，需要设置recursive=true"
                    )
                except StopIteration:
                    pass
            
            # 统计删除内容
            deleted_files = 0
            deleted_dirs = 0
            
            def handle_remove_error(func, path, exc_info):
                """处理删除错误"""
                nonlocal deleted_files
                if force:
                    try:
                        os.chmod(path, 0o777)
                        func(path)
                        deleted_files += 1
                    except Exception:
                        pass
            
            if recursive:
                # 统计要删除的内容
                for item in validated_path.rglob('*'):
                    if item.is_file():
                        deleted_files += 1
                    elif item.is_dir():
                        deleted_dirs += 1
                
                # 删除目录树
                shutil.rmtree(str(validated_path), onerror=handle_remove_error if force else None)
            else:
                validated_path.rmdir()
            
            deleted_dirs += 1  # 包括根目录
            
            # 使缓存失效
            self.cache.invalidate_directory(str(validated_path.parent))
            self.cache.invalidate_directory(str(validated_path))
            
            # 审计日志
            self.security.log_audit(
                "delete_directory",
                path=str(validated_path),
                details={"recursive": recursive, "force": force, "deleted_files": deleted_files}
            )
            
            duration = (datetime.now(timezone.utc) - start_time).total_seconds() * 1000
            
            return {
                "status": "success",
                "data": {
                    "path": str(validated_path),
                    "deleted": True,
                    "deleted_files": deleted_files,
                    "deleted_directories": deleted_dirs,
                },
                "metadata": {
                    "operation": "delete_directory",
                    "timestamp": start_time.isoformat(),
                    "duration_ms": round(duration, 2),
                }
            }
            
        except MCPError:
            raise
        except Exception as e:
            logger.error("删除目录失败", path=path, error=str(e))
            raise MCPError(f"删除目录失败: {str(e)}", cause=e)
    
    async def move_directory(
        self,
        source: str,
        destination: str,
        overwrite: bool = False,
        copy_permissions: bool = True
    ) -> Dict[str, Any]:
        """
        移动/重命名目录
        
        Args:
            source: 源目录路径
            destination: 目标目录路径
            overwrite: 是否覆盖已存在目录
            copy_permissions: 是否复制权限
            
        Returns:
            操作结果响应字典
        """
        start_time = datetime.now(timezone.utc)
        
        try:
            # 验证源路径
            validated_source = self.security.validate_path(source)
            if not validated_source.exists():
                raise MCPFileNotFoundError(path=str(validated_source))
            if not validated_source.is_dir():
                raise InvalidParameterError(
                    parameter="source",
                    value=source,
                    reason="源路径不是目录"
                )
            
            # 验证目标路径
            validated_dest = self.security.validate_path(destination)
            
            # 检查目标是否已存在
            if validated_dest.exists():
                if not overwrite:
                    raise InvalidParameterError(
                        parameter="destination",
                        value=destination,
                        reason="目标路径已存在"
                    )
                # 删除已存在的目标
                if validated_dest.is_dir():
                    shutil.rmtree(str(validated_dest))
                else:
                    validated_dest.unlink()
            
            # 检查权限
            self.security.check_file_permission(validated_source, "read")
            self.security.check_file_permission(validated_dest.parent, "write")
            
            # 移动目录
            shutil.move(str(validated_source), str(validated_dest))
            
            # 使缓存失效
            self.cache.invalidate_directory(str(validated_source.parent))
            self.cache.invalidate_directory(str(validated_dest.parent))
            
            # 审计日志
            self.security.log_audit(
                "move_directory",
                path=str(validated_source),
                details={"destination": str(validated_dest)}
            )
            
            duration = (datetime.now(timezone.utc) - start_time).total_seconds() * 1000
            
            return {
                "status": "success",
                "data": {
                    "source": str(validated_source),
                    "destination": str(validated_dest),
                    "moved": True,
                },
                "metadata": {
                    "operation": "move_directory",
                    "timestamp": start_time.isoformat(),
                    "duration_ms": round(duration, 2),
                }
            }
            
        except MCPError:
            raise
        except Exception as e:
            logger.error("移动目录失败", source=source, destination=destination, error=str(e))
            raise MCPError(f"移动目录失败: {str(e)}", cause=e)
    
    # ==================== 文件操作 ====================
    
    async def create_file(
        self,
        path: str,
        content: str = "",
        encoding: str = "utf-8",
        overwrite: bool = False
    ) -> Dict[str, Any]:
        """
        创建文件
        
        Args:
            path: 文件路径
            content: 文件内容
            encoding: 文件编码
            overwrite: 是否覆盖已存在文件
            
        Returns:
            操作结果响应字典
        """
        start_time = datetime.now(timezone.utc)
        
        try:
            # 验证路径安全性
            validated_path = self.security.validate_path(path)
            
            # 检查是否已存在
            if validated_path.exists():
                if not overwrite:
                    raise InvalidParameterError(
                        parameter="path",
                        value=path,
                        reason="文件已存在，需要设置overwrite=true"
                    )
                if validated_path.is_dir():
                    raise InvalidParameterError(
                        parameter="path",
                        value=path,
                        reason="路径是目录而非文件"
                    )
            
            # 确保父目录存在
            parent = validated_path.parent
            if not parent.exists():
                parent.mkdir(parents=True, exist_ok=True)
            
            # 检查写入权限
            self.security.check_file_permission(parent, "write")
            
            # 验证编码
            validated_encoding = self.security.validate_encoding(encoding)
            
            # 规范化换行符（避免文本模式双重转换）
            content = _normalize_newlines(content)
            
            # 获取文件锁
            file_lock = await self.security.acquire_file_lock(str(validated_path))
            
            async with file_lock:
                # 写入文件
                async with aiofiles.open(validated_path, 'w', encoding=validated_encoding) as f:
                    await f.write(content)
            
            # 使缓存失效
            self.cache.invalidate_directory(str(parent))
            self.cache.invalidate_metadata(str(validated_path))
            
            # 审计日志
            self.security.log_audit(
                "create_file",
                path=str(validated_path),
                details={"size": len(content.encode(validated_encoding)), "encoding": validated_encoding}
            )
            
            duration = (datetime.now(timezone.utc) - start_time).total_seconds() * 1000
            
            return {
                "status": "success",
                "data": {
                    "path": str(validated_path),
                    "created": True,
                    "size": len(content.encode(validated_encoding)),
                    "encoding": validated_encoding,
                },
                "metadata": {
                    "operation": "create_file",
                    "timestamp": start_time.isoformat(),
                    "duration_ms": round(duration, 2),
                }
            }
            
        except MCPError:
            raise
        except Exception as e:
            logger.error("创建文件失败", path=path, error=str(e))
            raise MCPError(f"创建文件失败: {str(e)}", cause=e)
    
    async def write_file(
        self,
        path: str,
        content: str,
        encoding: str = "utf-8",
        create_parents: bool = True
    ) -> Dict[str, Any]:
        """
        写入文件内容（覆盖现有内容）
        
        Args:
            path: 文件路径
            content: 文件内容
            encoding: 文件编码
            create_parents: 是否创建父目录
            
        Returns:
            操作结果响应字典
        """
        start_time = datetime.now(timezone.utc)
        
        try:
            # 验证路径安全性
            validated_path = self.security.validate_path(path)
            
            # 确保父目录存在
            parent = validated_path.parent
            if not parent.exists():
                if create_parents:
                    parent.mkdir(parents=True, exist_ok=True)
                else:
                    raise MCPFileNotFoundError(path=str(parent))
            
            # 检查写入权限
            if validated_path.exists():
                self.security.check_file_permission(validated_path, "write")
            else:
                self.security.check_file_permission(parent, "write")
            
            # 验证编码
            validated_encoding = self.security.validate_encoding(encoding)
            
            # 获取文件锁
            file_lock = await self.security.acquire_file_lock(str(validated_path))
            
            async with file_lock:
                # 备份原文件（如果存在）
                backup_path = None
                if validated_path.exists():
                    backup_path = validated_path.with_suffix(validated_path.suffix + '.bak')
                    shutil.copy2(str(validated_path), str(backup_path))
                
                # 规范化换行符（避免文本模式双重转换）
                content = _normalize_newlines(content)
                
                try:
                    # 写入文件
                    async with aiofiles.open(validated_path, 'w', encoding=validated_encoding) as f:
                        await f.write(content)
                    
                    # 删除备份
                    if backup_path and backup_path.exists():
                        backup_path.unlink()
                        
                except Exception as e:
                    # 恢复备份
                    if backup_path and backup_path.exists():
                        shutil.copy2(str(backup_path), str(validated_path))
                        backup_path.unlink()
                    raise
            
            # 使缓存失效
            self.cache.invalidate_metadata(str(validated_path))
            
            # 审计日志
            self.security.log_audit(
                "write_file",
                path=str(validated_path),
                details={"size": len(content.encode(validated_encoding))}
            )
            
            duration = (datetime.now(timezone.utc) - start_time).total_seconds() * 1000
            
            return {
                "status": "success",
                "data": {
                    "path": str(validated_path),
                    "written": True,
                    "size": len(content.encode(validated_encoding)),
                    "encoding": validated_encoding,
                },
                "metadata": {
                    "operation": "write_file",
                    "timestamp": start_time.isoformat(),
                    "duration_ms": round(duration, 2),
                }
            }
            
        except MCPError:
            raise
        except Exception as e:
            logger.error("写入文件失败", path=path, error=str(e))
            raise MCPError(f"写入文件失败: {str(e)}", cause=e)
    
    async def delete_file(
        self,
        path: str,
        backup: bool = False,
        backup_dir: Optional[str] = None,
        permanent: bool = False
    ) -> Dict[str, Any]:
        """
        删除文件
        
        Args:
            path: 文件路径
            backup: 是否创建备份
            backup_dir: 备份目录
            permanent: 是否永久删除
            
        Returns:
            操作结果响应字典
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
            
            # 检查删除权限
            self.security.check_file_permission(validated_path, "delete")
            
            # 创建备份
            backup_created = None
            if backup:
                if backup_dir:
                    backup_base = self.security.validate_path(backup_dir)
                else:
                    backup_base = validated_path.parent / ".backup"
                
                backup_base.mkdir(parents=True, exist_ok=True)
                
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                backup_name = f"{validated_path.stem}_{timestamp}{validated_path.suffix}"
                backup_path = backup_base / backup_name
                
                shutil.copy2(str(validated_path), str(backup_path))
                backup_created = str(backup_path)
            
            # 删除文件
            file_size = validated_path.stat().st_size
            validated_path.unlink()
            
            # 使缓存失效
            self.cache.invalidate_directory(str(validated_path.parent))
            self.cache.invalidate_metadata(str(validated_path))
            
            # 审计日志
            self.security.log_audit(
                "delete_file",
                path=str(validated_path),
                details={"backup": backup_created, "size": file_size}
            )
            
            duration = (datetime.now(timezone.utc) - start_time).total_seconds() * 1000
            
            return {
                "status": "success",
                "data": {
                    "path": str(validated_path),
                    "deleted": True,
                    "size": file_size,
                    "backup_path": backup_created,
                },
                "metadata": {
                    "operation": "delete_file",
                    "timestamp": start_time.isoformat(),
                    "duration_ms": round(duration, 2),
                }
            }
            
        except MCPError:
            raise
        except Exception as e:
            logger.error("删除文件失败", path=path, error=str(e))
            raise MCPError(f"删除文件失败: {str(e)}", cause=e)
    
    async def move_file(
        self,
        source: str,
        destination: str,
        overwrite: bool = False,
        copy_permissions: bool = True,
        preserve_timestamps: bool = True
    ) -> Dict[str, Any]:
        """
        移动/重命名文件
        
        Args:
            source: 源文件路径
            destination: 目标文件路径
            overwrite: 是否覆盖已存在文件
            copy_permissions: 是否复制权限
            preserve_timestamps: 是否保留时间戳
            
        Returns:
            操作结果响应字典
        """
        start_time = datetime.now(timezone.utc)
        
        try:
            # 验证源路径
            validated_source = self.security.validate_path(source)
            if not validated_source.exists():
                raise MCPFileNotFoundError(path=str(validated_source))
            if not validated_source.is_file():
                raise InvalidParameterError(
                    parameter="source",
                    value=source,
                    reason="源路径不是文件"
                )
            
            # 验证目标路径
            validated_dest = self.security.validate_path(destination)
            
            # 检查目标是否已存在
            if validated_dest.exists():
                if not overwrite:
                    raise InvalidParameterError(
                        parameter="destination",
                        value=destination,
                        reason="目标文件已存在"
                    )
            
            # 确保目标目录存在
            dest_parent = validated_dest.parent
            if not dest_parent.exists():
                dest_parent.mkdir(parents=True, exist_ok=True)
            
            # 检查权限
            self.security.check_file_permission(validated_source, "read")
            self.security.check_file_permission(dest_parent, "write")
            
            # 获取源文件信息
            source_stat = validated_source.stat()
            
            # 移动文件
            if preserve_timestamps:
                shutil.move(str(validated_source), str(validated_dest))
            else:
                shutil.copy2(str(validated_source), str(validated_dest))
                validated_source.unlink()
            
            # 使缓存失效
            self.cache.invalidate_directory(str(validated_source.parent))
            self.cache.invalidate_directory(str(validated_dest.parent))
            self.cache.invalidate_metadata(str(validated_source))
            
            # 审计日志
            self.security.log_audit(
                "move_file",
                path=str(validated_source),
                details={"destination": str(validated_dest), "size": source_stat.st_size}
            )
            
            duration = (datetime.now(timezone.utc) - start_time).total_seconds() * 1000
            
            return {
                "status": "success",
                "data": {
                    "source": str(validated_source),
                    "destination": str(validated_dest),
                    "moved": True,
                    "size": source_stat.st_size,
                },
                "metadata": {
                    "operation": "move_file",
                    "timestamp": start_time.isoformat(),
                    "duration_ms": round(duration, 2),
                }
            }
            
        except MCPError:
            raise
        except Exception as e:
            logger.error("移动文件失败", source=source, destination=destination, error=str(e))
            raise MCPError(f"移动文件失败: {str(e)}", cause=e)
    
    async def copy_file(
        self,
        source: str,
        destination: str,
        overwrite: bool = False
    ) -> Dict[str, Any]:
        """
        复制文件
        
        Args:
            source: 源文件路径
            destination: 目标文件路径
            overwrite: 是否覆盖已存在文件
            
        Returns:
            操作结果响应字典
        """
        start_time = datetime.now(timezone.utc)
        
        try:
            # 验证源路径
            validated_source = self.security.validate_path(source)
            if not validated_source.exists():
                raise MCPFileNotFoundError(path=str(validated_source))
            if not validated_source.is_file():
                raise InvalidParameterError(
                    parameter="source",
                    value=source,
                    reason="源路径不是文件"
                )
            
            # 验证目标路径
            validated_dest = self.security.validate_path(destination)
            
            # 检查目标是否已存在
            if validated_dest.exists() and not overwrite:
                raise InvalidParameterError(
                    parameter="destination",
                    value=destination,
                    reason="目标文件已存在"
                )
            
            # 确保目标目录存在
            dest_parent = validated_dest.parent
            if not dest_parent.exists():
                dest_parent.mkdir(parents=True, exist_ok=True)
            
            # 检查权限
            self.security.check_file_permission(validated_source, "read")
            self.security.check_file_permission(dest_parent, "write")
            
            # 复制文件
            shutil.copy2(str(validated_source), str(validated_dest))
            
            # 使缓存失效
            self.cache.invalidate_directory(str(validated_dest.parent))
            
            # 审计日志
            source_size = validated_source.stat().st_size
            self.security.log_audit(
                "copy_file",
                path=str(validated_source),
                details={"destination": str(validated_dest), "size": source_size}
            )
            
            duration = (datetime.now(timezone.utc) - start_time).total_seconds() * 1000
            
            return {
                "status": "success",
                "data": {
                    "source": str(validated_source),
                    "destination": str(validated_dest),
                    "copied": True,
                    "size": source_size,
                },
                "metadata": {
                    "operation": "copy_file",
                    "timestamp": start_time.isoformat(),
                    "duration_ms": round(duration, 2),
                }
            }
            
        except MCPError:
            raise
        except Exception as e:
            logger.error("复制文件失败", source=source, destination=destination, error=str(e))
            raise MCPError(f"复制文件失败: {str(e)}", cause=e)
    
    # ==================== 内容编辑 ====================
    
    async def replace_range(
        self,
        path: str,
        range: Tuple[int, int],
        new_text: str,
        encoding: str = "utf-8",
        unit: str = "bytes"
    ) -> Dict[str, Any]:
        """
        替换文本范围
        
        Args:
            path: 文件路径
            range: 替换范围 [start, end]
            new_text: 新文本
            encoding: 文件编码
            unit: 范围单位 (bytes 或 chars)
            
        Returns:
            操作结果响应字典
        """
        start_time = datetime.now(timezone.utc)
        
        try:
            # 验证路径安全性
            validated_path = self.security.validate_path(path)
            
            # 检查文件是否存在
            if not validated_path.exists():
                raise MCPFileNotFoundError(path=str(validated_path))
            
            # 检查写入权限
            self.security.check_file_permission(validated_path, "write")
            
            # 验证编码
            validated_encoding = self.security.validate_encoding(encoding)
            
            # 验证范围
            start_pos, end_pos = range
            if start_pos < 0 or end_pos < start_pos:
                raise InvalidParameterError(
                    parameter="range",
                    value=range,
                    reason="无效的范围值"
                )
            
            # 获取文件锁
            file_lock = await self.security.acquire_file_lock(str(validated_path))
            
            async with file_lock:
                if unit == "bytes":
                    # bytes模式：直接二进制读写，保持字节偏移精确一致
                    async with aiofiles.open(validated_path, 'rb') as f:
                        content_bytes = await f.read()
                    
                    if end_pos > len(content_bytes):
                        end_pos = len(content_bytes)
                    
                    before_bytes = content_bytes[:start_pos]
                    after_bytes = content_bytes[end_pos:]
                    old_text = content_bytes[start_pos:end_pos].decode(validated_encoding, errors='replace')
                    new_text_bytes = new_text.encode(validated_encoding)
                    
                    new_content_bytes = before_bytes + new_text_bytes + after_bytes
                    
                    # 备份原文件
                    backup_path = validated_path.with_suffix(validated_path.suffix + '.bak')
                    shutil.copy2(str(validated_path), str(backup_path))
                    
                    try:
                        async with aiofiles.open(validated_path, 'wb') as f:
                            await f.write(new_content_bytes)
                        backup_path.unlink()
                    except Exception as e:
                        shutil.copy2(str(backup_path), str(validated_path))
                        backup_path.unlink()
                        raise
                    
                    new_size = len(new_content_bytes)
                else:
                    # chars模式：文本模式读写
                    async with aiofiles.open(validated_path, 'r', encoding=validated_encoding) as f:
                        content = await f.read()
                    
                    if end_pos > len(content):
                        end_pos = len(content)
                    
                    before = content[:start_pos]
                    after = content[end_pos:]
                    old_text = content[start_pos:end_pos]
                    
                    # 规范化换行符（避免文本模式双重转换）
                    new_text = _normalize_newlines(new_text)
                    
                    new_content = before + new_text + after
                    
                    # 备份原文件
                    backup_path = validated_path.with_suffix(validated_path.suffix + '.bak')
                    shutil.copy2(str(validated_path), str(backup_path))
                    
                    try:
                        async with aiofiles.open(validated_path, 'w', encoding=validated_encoding) as f:
                            await f.write(new_content)
                        backup_path.unlink()
                    except Exception as e:
                        shutil.copy2(str(backup_path), str(validated_path))
                        backup_path.unlink()
                        raise
                    
                    new_size = len(new_content.encode(validated_encoding))
            
            # 使缓存失效
            self.cache.invalidate_metadata(str(validated_path))
            
            # 审计日志
            self.security.log_audit(
                "replace_range",
                path=str(validated_path),
                details={"range": range, "old_length": len(old_text), "new_length": len(new_text)}
            )
            
            duration = (datetime.now(timezone.utc) - start_time).total_seconds() * 1000
            
            return {
                "status": "success",
                "data": {
                    "path": str(validated_path),
                    "replaced": True,
                    "range": range,
                    "old_text_length": len(old_text),
                    "new_text_length": len(new_text),
                    "new_size": new_size,
                },
                "metadata": {
                    "operation": "replace_range",
                    "timestamp": start_time.isoformat(),
                    "duration_ms": round(duration, 2),
                }
            }
            
        except MCPError:
            raise
        except Exception as e:
            logger.error("替换文本范围失败", path=path, error=str(e))
            raise MCPError(f"替换文本范围失败: {str(e)}", cause=e)
    
    async def insert_text(
        self,
        path: str,
        position: int,
        text: str,
        encoding: str = "utf-8",
        unit: str = "bytes"
    ) -> Dict[str, Any]:
        """
        在指定位置插入文本
        
        Args:
            path: 文件路径
            position: 插入位置（行号从1开始，或字节/字符偏移量）
            text: 要插入的文本
            encoding: 文件编码
            unit: 位置单位 (bytes, chars 或 line)
            
        Returns:
            操作结果响应字典
        """
        if unit == "line":
            # 行号模式：将行号转换为字节偏移量
            validated_path = self.security.validate_path(path)
            if not validated_path.exists():
                raise MCPFileNotFoundError(path=str(validated_path))
            
            import aiofiles
            async with aiofiles.open(validated_path, 'rb') as f:
                content_bytes = await f.read()
            
            line_num = max(1, int(position))
            # 找到第 line_num 行的起始字节偏移
            offset = 0
            current_line = 1
            while current_line < line_num and offset < len(content_bytes):
                idx = content_bytes.find(b'\n', offset)
                if idx == -1:
                    offset = len(content_bytes)
                    break
                offset = idx + 1
                current_line += 1
            
            # 确保插入的文本以换行结尾
            if text and not text.endswith('\n') and not text.endswith('\r\n'):
                text = text + '\r\n' if b'\r\n' in content_bytes else text + '\n'
            
            return await self.replace_range(
                path=path,
                range=(offset, offset),
                new_text=text,
                encoding=encoding,
                unit="bytes"
            )
        
        # bytes/chars 模式：插入实际上是替换长度为0的范围
        return await self.replace_range(
            path=path,
            range=(position, position),
            new_text=text,
            encoding=encoding,
            unit=unit
        )
    
    async def delete_range(
        self,
        path: str,
        range: Tuple[int, int],
        encoding: str = "utf-8",
        unit: str = "bytes"
    ) -> Dict[str, Any]:
        """
        删除指定范围的文本
        
        Args:
            path: 文件路径
            range: 删除范围 [start, end]
            encoding: 文件编码
            unit: 范围单位 (bytes 或 chars)
            
        Returns:
            操作结果响应字典
        """
        # 删除实际上是替换为空字符串
        return await self.replace_range(
            path=path,
            range=range,
            new_text="",
            encoding=encoding,
            unit=unit
        )
    
    async def apply_patch(
        self,
        path: str,
        patch: str,
        encoding: str = "utf-8",
        dry_run: bool = False,
        reverse: bool = False
    ) -> Dict[str, Any]:
        """
        应用统一diff补丁
        
        Args:
            path: 文件路径
            patch: 补丁内容
            encoding: 文件编码
            dry_run: 是否试运行
            reverse: 是否反向应用
            
        Returns:
            操作结果响应字典
        """
        start_time = datetime.now(timezone.utc)
        
        try:
            # 验证路径安全性
            validated_path = self.security.validate_path(path)
            
            # 检查文件是否存在
            if not validated_path.exists():
                raise MCPFileNotFoundError(path=str(validated_path))
            
            # 检查写入权限（如果不是试运行）
            if not dry_run:
                self.security.check_file_permission(validated_path, "write")
            
            # 验证编码
            validated_encoding = self.security.validate_encoding(encoding)
            
            # 解析补丁
            hunks = self._parse_patch(patch, reverse)
            
            # 读取原文件
            async with aiofiles.open(validated_path, 'r', encoding=validated_encoding) as f:
                original_lines = (await f.read()).split('\n')
            
            # 应用补丁
            new_lines = original_lines.copy()
            applied_hunks = 0
            failed_hunks = 0
            
            # 需要反向处理hunks以避免行号偏移问题
            for hunk in reversed(hunks):
                try:
                    new_lines = self._apply_hunk(new_lines, hunk)
                    applied_hunks += 1
                except Exception as e:
                    failed_hunks += 1
                    if not dry_run:
                        raise PatchApplyError(
                            path=str(validated_path),
                            reason=f"Hunk应用失败: {e}"
                        )
            
            if not dry_run and failed_hunks == 0:
                # 获取文件锁
                file_lock = await self.security.acquire_file_lock(str(validated_path))
                
                async with file_lock:
                    # 备份原文件
                    backup_path = validated_path.with_suffix(validated_path.suffix + '.bak')
                    shutil.copy2(str(validated_path), str(backup_path))
                    
                    try:
                        # 写入新内容
                        async with aiofiles.open(validated_path, 'w', encoding=validated_encoding) as f:
                            await f.write('\n'.join(new_lines))
                        
                        # 删除备份
                        backup_path.unlink()
                        
                    except Exception as e:
                        # 恢复备份
                        shutil.copy2(str(backup_path), str(validated_path))
                        backup_path.unlink()
                        raise
                
                # 使缓存失效
                self.cache.invalidate_metadata(str(validated_path))
            
            # 审计日志
            if not dry_run:
                self.security.log_audit(
                    "apply_patch",
                    path=str(validated_path),
                    details={"applied_hunks": applied_hunks, "failed_hunks": failed_hunks}
                )
            
            duration = (datetime.now(timezone.utc) - start_time).total_seconds() * 1000
            
            return {
                "status": "success",
                "data": {
                    "path": str(validated_path),
                    "applied": not dry_run and failed_hunks == 0,
                    "dry_run": dry_run,
                    "applied_hunks": applied_hunks,
                    "failed_hunks": failed_hunks,
                    "total_hunks": len(hunks),
                },
                "metadata": {
                    "operation": "apply_patch",
                    "timestamp": start_time.isoformat(),
                    "duration_ms": round(duration, 2),
                }
            }
            
        except MCPError:
            raise
        except Exception as e:
            logger.error("应用补丁失败", path=path, error=str(e))
            raise MCPError(f"应用补丁失败: {str(e)}", cause=e)
    
    def _parse_patch(self, patch: str, reverse: bool = False) -> List[Dict[str, Any]]:
        """解析统一diff补丁"""
        import re
        
        hunks = []
        lines = patch.split('\n')
        
        hunk_pattern = re.compile(r'^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@')
        
        current_hunk = None
        
        for line in lines:
            match = hunk_pattern.match(line)
            if match:
                if current_hunk:
                    hunks.append(current_hunk)
                
                old_start = int(match.group(1))
                old_count = int(match.group(2)) if match.group(2) else 1
                new_start = int(match.group(3))
                new_count = int(match.group(4)) if match.group(4) else 1
                
                current_hunk = {
                    "old_start": new_start if reverse else old_start,
                    "old_count": new_count if reverse else old_count,
                    "new_start": old_start if reverse else new_start,
                    "new_count": old_count if reverse else new_count,
                    "lines": [],
                }
            elif current_hunk is not None:
                if line.startswith('+'):
                    if reverse:
                        current_hunk["lines"].append(('-', line[1:]))
                    else:
                        current_hunk["lines"].append(('+', line[1:]))
                elif line.startswith('-'):
                    if reverse:
                        current_hunk["lines"].append(('+', line[1:]))
                    else:
                        current_hunk["lines"].append(('-', line[1:]))
                elif line.startswith(' '):
                    current_hunk["lines"].append((' ', line[1:]))
                elif line == '':
                    current_hunk["lines"].append((' ', ''))
        
        if current_hunk:
            hunks.append(current_hunk)
        
        return hunks
    
    def _apply_hunk(self, lines: List[str], hunk: Dict[str, Any]) -> List[str]:
        """应用单个hunk"""
        new_lines = lines.copy()
        
        # 从hunk起始位置开始（1-indexed转0-indexed）
        start_index = hunk["old_start"] - 1
        
        # 收集新内容
        result_lines = []
        source_index = 0
        
        for op, content in hunk["lines"]:
            if op == ' ':
                # 上下文行，保持不变
                result_lines.append(content)
                source_index += 1
            elif op == '-':
                # 删除行，跳过源文件中的行
                source_index += 1
            elif op == '+':
                # 添加行
                result_lines.append(content)
        
        # 替换原内容
        new_lines = (
            new_lines[:start_index] + 
            result_lines + 
            new_lines[start_index + hunk["old_count"]:]
        )
        
        return new_lines


# 模块级便捷函数
_handler: Optional[EditHandler] = None


def get_handler() -> EditHandler:
    """获取处理器实例"""
    global _handler
    if _handler is None:
        _handler = EditHandler()
    return _handler


def reset_handler() -> None:
    """重置处理器实例（用于运行时配置更新）"""
    global _handler
    _handler = None


# 目录操作
async def create_directory(path: str, recursive: bool = True, mode: str = "755") -> Dict[str, Any]:
    return await get_handler().create_directory(path, recursive, mode)


async def delete_directory(path: str, recursive: bool = False, force: bool = False) -> Dict[str, Any]:
    return await get_handler().delete_directory(path, recursive, force)


async def move_directory(source: str, destination: str, overwrite: bool = False, copy_permissions: bool = True) -> Dict[str, Any]:
    return await get_handler().move_directory(source, destination, overwrite, copy_permissions)


# 文件操作
async def create_file(path: str, content: str = "", encoding: str = "utf-8", overwrite: bool = False) -> Dict[str, Any]:
    return await get_handler().create_file(path, content, encoding, overwrite)


async def write_file(path: str, content: str, encoding: str = "utf-8", create_parents: bool = True) -> Dict[str, Any]:
    return await get_handler().write_file(path, content, encoding, create_parents)


async def delete_file(path: str, backup: bool = False, backup_dir: Optional[str] = None, permanent: bool = False) -> Dict[str, Any]:
    return await get_handler().delete_file(path, backup, backup_dir, permanent)


async def move_file(source: str, destination: str, overwrite: bool = False, copy_permissions: bool = True, preserve_timestamps: bool = True) -> Dict[str, Any]:
    return await get_handler().move_file(source, destination, overwrite, copy_permissions, preserve_timestamps)


async def copy_file(source: str, destination: str, overwrite: bool = False) -> Dict[str, Any]:
    return await get_handler().copy_file(source, destination, overwrite)


# 内容编辑
async def replace_range(path: str, range: Tuple[int, int], new_text: str, encoding: str = "utf-8", unit: str = "bytes") -> Dict[str, Any]:
    return await get_handler().replace_range(path, range, new_text, encoding, unit)


async def insert_text(path: str, position: int, text: str, encoding: str = "utf-8", unit: str = "bytes") -> Dict[str, Any]:
    return await get_handler().insert_text(path, position, text, encoding, unit)


async def delete_range(path: str, range: Tuple[int, int], encoding: str = "utf-8", unit: str = "bytes") -> Dict[str, Any]:
    return await get_handler().delete_range(path, range, encoding, unit)


async def apply_patch(path: str, patch: str, encoding: str = "utf-8", dry_run: bool = False, reverse: bool = False) -> Dict[str, Any]:
    return await get_handler().apply_patch(path, patch, encoding, dry_run, reverse)
