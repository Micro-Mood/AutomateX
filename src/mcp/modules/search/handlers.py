"""
搜索模块处理器
实现search_files, search_content, search_symbol接口
"""

import os
import re
import fnmatch
import asyncio
from pathlib import Path
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Pattern
import aiofiles
import chardet
import structlog

from src.mcp.core.config import MCPConfig, get_config
from src.mcp.core.security import SecurityManager
from src.mcp.core.cache import CacheManager, get_cache_manager
from src.mcp.core.exceptions import (
    MCPError,
    FileNotFoundError as MCPFileNotFoundError,
    InvalidParameterError,
    ResourceLimitExceededError,
    TimeoutError as MCPTimeoutError,
)

logger = structlog.get_logger(__name__)


class SearchHandler:
    """搜索模块处理器"""
    
    def __init__(
        self,
        config: Optional[MCPConfig] = None,
        security: Optional[SecurityManager] = None,
        cache: Optional[CacheManager] = None,
        max_scanned_dirs: int = 500,
    ):
        self.config = config or get_config()
        self.security = security or SecurityManager(self.config)
        self.cache = cache or get_cache_manager()
        
        # 搜索限制（可通过构造参数配置）
        self.max_scanned_files = 10000
        self.max_scanned_dirs = max_scanned_dirs
    
    async def search_files(
        self,
        pattern: str,
        root_dir: str,
        max_results: int = 100,
        recursive: bool = True,
        file_types: Optional[List[str]] = None,
        exclude_patterns: Optional[List[str]] = None,
        min_size: Optional[int] = None,
        max_size: Optional[int] = None,
        modified_after: Optional[str] = None,
        modified_before: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        搜索文件
        
        Args:
            pattern: 文件名模式（通配符或正则表达式）
            root_dir: 搜索根目录
            max_results: 最大结果数
            recursive: 是否递归搜索
            file_types: 文件类型过滤
            exclude_patterns: 排除模式
            min_size: 最小文件大小
            max_size: 最大文件大小
            modified_after: 修改时间下限
            modified_before: 修改时间上限
            
        Returns:
            搜索结果响应字典
        """
        start_time = datetime.now(timezone.utc)
        
        try:
            # 验证参数
            max_results = min(max(1, max_results), self.config.performance.max_search_results)
            
            # 验证根目录
            validated_root = self.security.validate_path(root_dir)
            if not validated_root.exists():
                raise MCPFileNotFoundError(path=str(validated_root))
            if not validated_root.is_dir():
                raise InvalidParameterError(
                    parameter="root_dir",
                    value=root_dir,
                    reason="必须是目录"
                )
            
            # 尝试从缓存获取
            cached = self.cache.get_search(
                pattern, str(validated_root),
                max_results=max_results,
                recursive=recursive,
                file_types=file_types
            )
            if cached:
                return cached
            
            # 解析模式
            is_regex = pattern.startswith('/') and pattern.endswith('/')
            if is_regex:
                try:
                    regex_pattern = re.compile(pattern[1:-1], re.IGNORECASE)
                except re.error as e:
                    raise InvalidParameterError(
                        parameter="pattern",
                        value=pattern,
                        reason=f"无效的正则表达式: {e}"
                    )
            else:
                regex_pattern = None
            
            # 解析时间过滤
            modified_after_ts = None
            modified_before_ts = None
            if modified_after:
                modified_after_ts = datetime.fromisoformat(modified_after.replace('Z', '+00:00')).timestamp()
            if modified_before:
                modified_before_ts = datetime.fromisoformat(modified_before.replace('Z', '+00:00')).timestamp()
            
            # 搜索统计
            stats = {
                "total_scanned": 0,
                "directories_scanned": 0,
                "size_scanned_bytes": 0,
            }
            
            results: List[Dict[str, Any]] = []
            exclude_patterns = exclude_patterns or []
            
            async def scan_directory(dir_path: Path, depth: int = 0):
                """递归扫描目录"""
                nonlocal stats
                
                if len(results) >= max_results:
                    return
                
                if stats["directories_scanned"] >= self.max_scanned_dirs:
                    return
                
                stats["directories_scanned"] += 1
                
                try:
                    entries = list(dir_path.iterdir())
                except PermissionError:
                    return
                
                for entry in entries:
                    if len(results) >= max_results:
                        break
                    
                    if stats["total_scanned"] >= self.max_scanned_files:
                        break
                    
                    stats["total_scanned"] += 1
                    
                    try:
                        # 检查排除模式
                        if self._should_exclude(entry.name, exclude_patterns):
                            continue
                        
                        if entry.is_file():
                            # 匹配文件名
                            if self._match_file(
                                entry, pattern, regex_pattern, is_regex,
                                file_types, min_size, max_size,
                                modified_after_ts, modified_before_ts
                            ):
                                stat_info = entry.stat()
                                stats["size_scanned_bytes"] += stat_info.st_size
                                
                                # 计算相对目录
                                try:
                                    rel_dir = str(entry.parent.relative_to(validated_root))
                                except ValueError:
                                    rel_dir = str(entry.parent)
                                
                                results.append({
                                    "path": str(entry),
                                    "name": entry.name,
                                    "size": stat_info.st_size,
                                    "modified": datetime.fromtimestamp(
                                        stat_info.st_mtime, timezone.utc
                                    ).isoformat(),
                                    "directory": rel_dir if rel_dir != '.' else '',
                                    "match_type": "regex" if is_regex else "wildcard",
                                })
                        
                        elif entry.is_dir() and recursive:
                            await scan_directory(entry, depth + 1)
                    
                    except (PermissionError, OSError):
                        continue
            
            await scan_directory(validated_root)
            
            # 计算耗时
            duration = (datetime.now(timezone.utc) - start_time).total_seconds() * 1000
            
            result = {
                "status": "success",
                "data": {
                    "query": {
                        "pattern": pattern,
                        "root_dir": str(validated_root),
                    },
                    "results": results,
                    "statistics": {
                        "total_found": len(results),
                        "total_scanned": stats["total_scanned"],
                        "directories_scanned": stats["directories_scanned"],
                        "time_elapsed_ms": round(duration, 2),
                        "size_scanned_bytes": stats["size_scanned_bytes"],
                    },
                    "limits": {
                        "max_results": max_results,
                        "max_scanned_files": self.max_scanned_files,
                        "max_scanned_dirs": self.max_scanned_dirs,
                    }
                },
                "metadata": {
                    "operation": "search_files",
                    "timestamp": start_time.isoformat(),
                    "duration_ms": round(duration, 2),
                }
            }
            
            # 缓存结果
            self.cache.set_search(
                pattern, str(validated_root), result,
                max_results=max_results,
                recursive=recursive,
                file_types=file_types
            )
            
            return result
            
        except MCPError:
            raise
        except Exception as e:
            logger.error("搜索文件失败", pattern=pattern, error=str(e))
            raise MCPError(f"搜索文件失败: {str(e)}", cause=e)
    
    async def search_content(
        self,
        query: str,
        root_dir: str,
        file_pattern: Optional[str] = None,
        max_files: int = 50,
        max_matches_per_file: int = 10,
        case_sensitive: bool = False,
        whole_word: bool = False,
        encoding: str = "auto",
        context_lines: int = 2,
        is_regex: bool = False,
        multiline: bool = False
    ) -> Dict[str, Any]:
        """
        搜索文件内容
        
        Args:
            query: 搜索文本或正则表达式
            root_dir: 搜索根目录
            file_pattern: 文件模式过滤
            max_files: 最大扫描文件数
            max_matches_per_file: 每文件最大匹配数
            case_sensitive: 是否区分大小写
            whole_word: 是否全词匹配
            encoding: 文件编码
            context_lines: 上下文行数
            is_regex: 是否正则表达式
            multiline: 是否多行匹配
            
        Returns:
            搜索结果响应字典
        """
        start_time = datetime.now(timezone.utc)
        
        try:
            # 验证参数
            max_files = min(max(1, max_files), 500)
            max_matches_per_file = min(max(1, max_matches_per_file), 100)
            context_lines = min(max(0, context_lines), 10)
            
            # 验证根目录
            validated_root = self.security.validate_path(root_dir)
            if not validated_root.exists():
                raise MCPFileNotFoundError(path=str(validated_root))
            
            # 构建搜索模式
            flags = 0 if case_sensitive else re.IGNORECASE
            if multiline:
                flags |= re.MULTILINE
            
            try:
                if is_regex:
                    search_pattern = re.compile(query, flags)
                elif whole_word:
                    search_pattern = re.compile(rf'\b{re.escape(query)}\b', flags)
                else:
                    search_pattern = re.compile(re.escape(query), flags)
            except re.error as e:
                raise InvalidParameterError(
                    parameter="query",
                    value=query,
                    reason=f"无效的搜索模式: {e}"
                )
            
            # 解析文件模式
            file_patterns = []
            if file_pattern:
                file_patterns = [p.strip() for p in file_pattern.split(';')]
            
            # 搜索统计
            stats = {
                "files_scanned": 0,
                "bytes_scanned": 0,
                "total_matches": 0,
                "files_with_matches": 0,
            }
            
            results: List[Dict[str, Any]] = []
            
            async def search_in_file(file_path: Path) -> Optional[Dict[str, Any]]:
                """在文件中搜索"""
                nonlocal stats
                
                try:
                    # 读取文件内容
                    async with aiofiles.open(file_path, 'rb') as f:
                        content_bytes = await f.read()
                    
                    stats["bytes_scanned"] += len(content_bytes)
                    
                    # 检测编码
                    if encoding == "auto":
                        detected = chardet.detect(content_bytes)
                        file_encoding = detected.get('encoding', 'utf-8') or 'utf-8'
                    else:
                        file_encoding = encoding
                    
                    try:
                        content = content_bytes.decode(file_encoding)
                    except UnicodeDecodeError:
                        return None
                    
                    # 分割成行
                    lines = content.split('\n')
                    
                    # 搜索匹配
                    matches: List[Dict[str, Any]] = []
                    
                    for line_num, line in enumerate(lines, 1):
                        if len(matches) >= max_matches_per_file:
                            break
                        
                        for match in search_pattern.finditer(line):
                            if len(matches) >= max_matches_per_file:
                                break
                            
                            # 获取上下文
                            context_before_lines = lines[max(0, line_num - 1 - context_lines):line_num - 1]
                            context_after_lines = lines[line_num:min(len(lines), line_num + context_lines)]
                            
                            matches.append({
                                "line": line_num,
                                "column": match.start() + 1,
                                "text": line.rstrip(),
                                "context_before": '\n'.join(context_before_lines) if context_before_lines else "",
                                "context_after": '\n'.join(context_after_lines) if context_after_lines else "",
                                "line_number": line_num,
                                "byte_offset": sum(len(l) + 1 for l in lines[:line_num - 1]) + match.start(),
                                "match_length": match.end() - match.start(),
                            })
                    
                    if matches:
                        stats["total_matches"] += len(matches)
                        stats["files_with_matches"] += 1
                        
                        try:
                            rel_path = str(file_path.relative_to(validated_root))
                        except ValueError:
                            rel_path = str(file_path)
                        
                        return {
                            "file": str(file_path),
                            "path": rel_path,
                            "matches": matches,
                            "match_count": len(matches),
                            "file_size": len(content_bytes),
                            "encoding": file_encoding,
                        }
                    
                    return None
                    
                except (PermissionError, OSError):
                    return None
            
            async def scan_directory(dir_path: Path):
                """扫描目录"""
                nonlocal stats
                
                if stats["files_scanned"] >= max_files:
                    return
                
                try:
                    entries = list(dir_path.iterdir())
                except PermissionError:
                    return
                
                for entry in entries:
                    if stats["files_scanned"] >= max_files:
                        break
                    
                    try:
                        if entry.is_file():
                            # 检查文件模式
                            if file_patterns:
                                if not any(fnmatch.fnmatch(entry.name, p) for p in file_patterns):
                                    continue
                            
                            # 检查文件大小限制
                            if entry.stat().st_size > self.config.get_max_file_size_bytes():
                                continue
                            
                            stats["files_scanned"] += 1
                            result = await search_in_file(entry)
                            if result:
                                results.append(result)
                        
                        elif entry.is_dir():
                            await scan_directory(entry)
                    
                    except (PermissionError, OSError):
                        continue
            
            await scan_directory(validated_root)
            
            # 计算耗时
            duration = (datetime.now(timezone.utc) - start_time).total_seconds() * 1000
            
            return {
                "status": "success",
                "data": {
                    "query": {
                        "text": query,
                        "is_regex": is_regex,
                        "case_sensitive": case_sensitive,
                    },
                    "results": results,
                    "statistics": {
                        "total_matches": stats["total_matches"],
                        "files_with_matches": stats["files_with_matches"],
                        "files_scanned": stats["files_scanned"],
                        "bytes_scanned": stats["bytes_scanned"],
                        "time_elapsed_ms": round(duration, 2),
                    },
                    "limits": {
                        "max_files": max_files,
                        "max_matches_per_file": max_matches_per_file,
                        "max_file_size": self.config.get_max_file_size_bytes(),
                    }
                },
                "metadata": {
                    "operation": "search_content",
                    "timestamp": start_time.isoformat(),
                    "duration_ms": round(duration, 2),
                }
            }
            
        except MCPError:
            raise
        except Exception as e:
            logger.error("搜索内容失败", query=query, error=str(e))
            raise MCPError(f"搜索内容失败: {str(e)}", cause=e)
    
    async def search_symbol(
        self,
        symbol: str,
        root_dir: str,
        symbol_type: Optional[str] = None,
        language: Optional[str] = None,
        max_results: int = 50,
        include_definitions: bool = True,
        include_references: bool = False,
        exact_match: bool = True
    ) -> Dict[str, Any]:
        """
        搜索代码符号
        
        Args:
            symbol: 符号名称
            root_dir: 搜索根目录
            symbol_type: 符号类型
            language: 编程语言
            max_results: 最大结果数
            include_definitions: 包含定义
            include_references: 包含引用
            exact_match: 精确匹配
            
        Returns:
            搜索结果响应字典
        """
        start_time = datetime.now(timezone.utc)
        
        try:
            # 验证根目录
            validated_root = self.security.validate_path(root_dir)
            if not validated_root.exists():
                raise MCPFileNotFoundError(path=str(validated_root))
            
            # 语言到文件扩展名映射
            language_extensions = {
                "python": [".py", ".pyw"],
                "javascript": [".js", ".jsx", ".mjs"],
                "typescript": [".ts", ".tsx"],
                "java": [".java"],
                "cpp": [".cpp", ".cc", ".cxx", ".c", ".h", ".hpp", ".hxx"],
                "csharp": [".cs"],
                "go": [".go"],
                "rust": [".rs"],
                "ruby": [".rb"],
                "php": [".php"],
            }
            
            # 符号类型的正则表达式模式
            symbol_patterns = {
                "python": {
                    "function": rf'^\s*(?:async\s+)?def\s+({re.escape(symbol) if exact_match else symbol})\s*\(',
                    "class": rf'^\s*class\s+({re.escape(symbol) if exact_match else symbol})\s*[\(:]',
                    "variable": rf'^({re.escape(symbol) if exact_match else symbol})\s*=',
                    "import": rf'(?:from\s+\S+\s+)?import\s+.*?(?:^|,\s*)({re.escape(symbol) if exact_match else symbol})(?:$|,|\s+as)',
                },
                "javascript": {
                    "function": rf'(?:async\s+)?function\s+({re.escape(symbol) if exact_match else symbol})\s*\(|(?:const|let|var)\s+({re.escape(symbol) if exact_match else symbol})\s*=\s*(?:async\s+)?\(|({re.escape(symbol) if exact_match else symbol})\s*:\s*(?:async\s+)?function',
                    "class": rf'class\s+({re.escape(symbol) if exact_match else symbol})\s*(?:extends|implements|\{{)',
                    "variable": rf'(?:const|let|var)\s+({re.escape(symbol) if exact_match else symbol})\s*=',
                    "import": rf'import\s+.*?({re.escape(symbol) if exact_match else symbol}).*?from',
                },
            }
            
            # 确定要搜索的扩展名
            extensions: List[str] = []
            if language and language.lower() in language_extensions:
                extensions = language_extensions[language.lower()]
            else:
                for exts in language_extensions.values():
                    extensions.extend(exts)
            
            results: List[Dict[str, Any]] = []
            files_searched = 0
            
            async def search_in_file(file_path: Path):
                """在文件中搜索符号"""
                nonlocal files_searched
                files_searched += 1
                
                try:
                    async with aiofiles.open(file_path, 'r', encoding='utf-8') as f:
                        content = await f.read()
                except (PermissionError, UnicodeDecodeError, OSError):
                    return
                
                # 检测语言
                ext = file_path.suffix.lower()
                detected_language = None
                for lang, exts in language_extensions.items():
                    if ext in exts:
                        detected_language = lang
                        break
                
                if not detected_language:
                    detected_language = "unknown"
                
                # 获取该语言的模式
                patterns = symbol_patterns.get(detected_language, {})
                
                symbols: List[Dict[str, Any]] = []
                lines = content.split('\n')
                
                for line_num, line in enumerate(lines, 1):
                    for sym_type, pattern in patterns.items():
                        if symbol_type and sym_type != symbol_type:
                            continue
                        
                        try:
                            match = re.search(pattern, line, re.IGNORECASE if not exact_match else 0)
                            if match:
                                # 找到匹配的符号名
                                matched_symbol = next(
                                    (g for g in match.groups() if g), 
                                    symbol
                                )
                                
                                symbols.append({
                                    "name": matched_symbol,
                                    "type": sym_type,
                                    "line": line_num,
                                    "column": match.start() + 1,
                                    "definition": line.strip(),
                                    "context": "",
                                })
                        except re.error:
                            continue
                
                if symbols:
                    results.append({
                        "file": str(file_path),
                        "language": detected_language,
                        "symbols": symbols[:max_results],
                    })
            
            async def scan_directory(dir_path: Path):
                """扫描目录"""
                if len(results) >= max_results:
                    return
                
                try:
                    entries = list(dir_path.iterdir())
                except PermissionError:
                    return
                
                for entry in entries:
                    if len(results) >= max_results:
                        break
                    
                    try:
                        if entry.is_file() and entry.suffix.lower() in extensions:
                            await search_in_file(entry)
                        elif entry.is_dir() and not entry.name.startswith('.'):
                            # 跳过常见的非代码目录
                            if entry.name not in ['node_modules', '__pycache__', 'venv', '.git', 'dist', 'build']:
                                await scan_directory(entry)
                    except (PermissionError, OSError):
                        continue
            
            await scan_directory(validated_root)
            
            # 计算耗时
            duration = (datetime.now(timezone.utc) - start_time).total_seconds() * 1000
            
            return {
                "status": "success",
                "data": {
                    "symbol": symbol,
                    "results": results[:max_results],
                    "statistics": {
                        "files_searched": files_searched,
                        "matches_found": len(results),
                    }
                },
                "metadata": {
                    "operation": "search_symbol",
                    "timestamp": start_time.isoformat(),
                    "duration_ms": round(duration, 2),
                }
            }
            
        except MCPError:
            raise
        except Exception as e:
            logger.error("搜索符号失败", symbol=symbol, error=str(e))
            raise MCPError(f"搜索符号失败: {str(e)}", cause=e)
    
    def _should_exclude(self, name: str, exclude_patterns: List[str]) -> bool:
        """检查是否应该排除"""
        for pattern in exclude_patterns:
            if fnmatch.fnmatch(name, pattern):
                return True
        return False
    
    def _match_file(
        self,
        entry: Path,
        pattern: str,
        regex_pattern: Optional[Pattern],
        is_regex: bool,
        file_types: Optional[List[str]],
        min_size: Optional[int],
        max_size: Optional[int],
        modified_after: Optional[float],
        modified_before: Optional[float]
    ) -> bool:
        """检查文件是否匹配搜索条件"""
        # 名称匹配
        if is_regex and regex_pattern:
            if not regex_pattern.match(entry.name):
                return False
        else:
            if not fnmatch.fnmatch(entry.name.lower(), pattern.lower()):
                return False
        
        # 文件类型过滤
        if file_types:
            ext = entry.suffix.lstrip('.').lower()
            if ext not in [t.lower().lstrip('.') for t in file_types]:
                return False
        
        # 获取stat信息
        try:
            stat_info = entry.stat()
        except (PermissionError, OSError):
            return False
        
        # 大小过滤
        if min_size is not None and stat_info.st_size < min_size:
            return False
        if max_size is not None and stat_info.st_size > max_size:
            return False
        
        # 时间过滤
        if modified_after is not None and stat_info.st_mtime < modified_after:
            return False
        if modified_before is not None and stat_info.st_mtime > modified_before:
            return False
        
        return True


# 模块级便捷函数
_handler: Optional[SearchHandler] = None


def get_handler() -> SearchHandler:
    """获取处理器实例"""
    global _handler
    if _handler is None:
        _handler = SearchHandler()
    return _handler


def reset_handler() -> None:
    """重置处理器实例（用于运行时配置更新）"""
    global _handler
    _handler = None


async def search_files(
    pattern: str,
    root_dir: str,
    max_results: int = 100,
    recursive: bool = True,
    file_types: Optional[List[str]] = None,
    exclude_patterns: Optional[List[str]] = None,
    min_size: Optional[int] = None,
    max_size: Optional[int] = None,
    modified_after: Optional[str] = None,
    modified_before: Optional[str] = None
) -> Dict[str, Any]:
    """搜索文件"""
    return await get_handler().search_files(
        pattern, root_dir, max_results, recursive,
        file_types, exclude_patterns, min_size, max_size,
        modified_after, modified_before
    )


async def search_content(
    query: str,
    root_dir: str,
    file_pattern: Optional[str] = None,
    max_files: int = 50,
    max_matches_per_file: int = 10,
    case_sensitive: bool = False,
    whole_word: bool = False,
    encoding: str = "auto",
    context_lines: int = 2,
    is_regex: bool = False,
    multiline: bool = False
) -> Dict[str, Any]:
    """搜索文件内容"""
    return await get_handler().search_content(
        query, root_dir, file_pattern, max_files, max_matches_per_file,
        case_sensitive, whole_word, encoding, context_lines, is_regex, multiline
    )


async def search_symbol(
    symbol: str,
    root_dir: str,
    symbol_type: Optional[str] = None,
    language: Optional[str] = None,
    max_results: int = 50,
    include_definitions: bool = True,
    include_references: bool = False,
    exact_match: bool = True
) -> Dict[str, Any]:
    """搜索代码符号"""
    return await get_handler().search_symbol(
        symbol, root_dir, symbol_type, language,
        max_results, include_definitions, include_references, exact_match
    )
