"""
MCP缓存模块
实现文件元数据、搜索结果等缓存策略
"""

import asyncio
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Generic, Optional, TypeVar
from dataclasses import dataclass, field
from functools import wraps
import hashlib
import structlog

from cachetools import TTLCache, LRUCache

from src.mcp.core.config import MCPConfig, get_config

logger = structlog.get_logger(__name__)

T = TypeVar('T')


@dataclass
class CacheEntry(Generic[T]):
    """缓存条目"""
    value: T
    created_at: datetime
    expires_at: datetime
    hits: int = 0
    
    def is_expired(self) -> bool:
        """检查是否过期"""
        return datetime.now(timezone.utc) > self.expires_at
    
    def touch(self) -> None:
        """更新访问统计"""
        self.hits += 1


@dataclass
class CacheStats:
    """缓存统计"""
    hits: int = 0
    misses: int = 0
    evictions: int = 0
    size: int = 0
    
    @property
    def hit_rate(self) -> float:
        """命中率"""
        total = self.hits + self.misses
        return self.hits / total if total > 0 else 0.0


class CacheManager:
    """
    缓存管理器
    
    提供多种缓存策略:
    - 文件元数据缓存 (60秒)
    - 目录列表缓存 (30秒)
    - 搜索结果缓存 (5分钟)
    - 任务状态缓存 (10秒)
    """
    
    def __init__(self, config: Optional[MCPConfig] = None):
        self.config = config or get_config()
        
        # 文件元数据缓存
        self._metadata_cache: TTLCache = TTLCache(
            maxsize=10000,
            ttl=60  # 60秒
        )
        
        # 目录列表缓存
        self._directory_cache: TTLCache = TTLCache(
            maxsize=1000,
            ttl=30  # 30秒
        )
        
        # 目录缓存反向映射: path -> set of cache keys
        self._directory_path_keys: Dict[str, set] = {}
        
        # 搜索结果缓存
        self._search_cache: TTLCache = TTLCache(
            maxsize=100,
            ttl=300  # 5分钟
        )
        
        # 任务状态缓存
        self._task_cache: TTLCache = TTLCache(
            maxsize=1000,
            ttl=10  # 10秒
        )
        
        # 通用LRU缓存
        self._lru_cache: LRUCache = LRUCache(maxsize=1000)
        
        # 统计信息
        self._stats: Dict[str, CacheStats] = {
            "metadata": CacheStats(),
            "directory": CacheStats(),
            "search": CacheStats(),
            "task": CacheStats(),
            "lru": CacheStats(),
        }
        
        # 锁
        self._lock = asyncio.Lock()
    
    def _generate_key(self, *args, **kwargs) -> str:
        """生成缓存键"""
        key_parts = [str(arg) for arg in args]
        key_parts.extend(f"{k}={v}" for k, v in sorted(kwargs.items()))
        key_str = ":".join(key_parts)
        return hashlib.md5(key_str.encode()).hexdigest()
    
    # 文件元数据缓存
    def get_metadata(self, path: str) -> Optional[Dict[str, Any]]:
        """获取文件元数据缓存"""
        try:
            value = self._metadata_cache.get(path)
            if value is not None:
                self._stats["metadata"].hits += 1
                return value
            self._stats["metadata"].misses += 1
            return None
        except KeyError:
            self._stats["metadata"].misses += 1
            return None
    
    def set_metadata(self, path: str, metadata: Dict[str, Any]) -> None:
        """设置文件元数据缓存"""
        self._metadata_cache[path] = metadata
        self._stats["metadata"].size = len(self._metadata_cache)
    
    def invalidate_metadata(self, path: str) -> None:
        """使文件元数据缓存失效"""
        try:
            del self._metadata_cache[path]
        except KeyError:
            pass
    
    # 目录列表缓存
    def get_directory(
        self, 
        path: str, 
        pattern: Optional[str] = None,
        recursive: bool = False,
        offset: int = 0,
        limit: int = 100
    ) -> Optional[Dict[str, Any]]:
        """获取目录列表缓存"""
        key = self._generate_key(path, pattern=pattern, recursive=recursive, offset=offset, limit=limit)
        try:
            value = self._directory_cache.get(key)
            if value is not None:
                self._stats["directory"].hits += 1
                return value
            self._stats["directory"].misses += 1
            return None
        except KeyError:
            self._stats["directory"].misses += 1
            return None
    
    def set_directory(
        self, 
        path: str, 
        data: Dict[str, Any],
        pattern: Optional[str] = None,
        recursive: bool = False,
        offset: int = 0,
        limit: int = 100
    ) -> None:
        """设置目录列表缓存"""
        key = self._generate_key(path, pattern=pattern, recursive=recursive, offset=offset, limit=limit)
        self._directory_cache[key] = data
        # 记录 path -> key 映射，用于精确失效
        if path not in self._directory_path_keys:
            self._directory_path_keys[path] = set()
        self._directory_path_keys[path].add(key)
        self._stats["directory"].size = len(self._directory_cache)
    
    def invalidate_directory(self, path: str) -> None:
        """使目录缓存失效（包括该路径及其所有父目录的缓存）"""
        import os
        paths_to_invalidate = set()
        # 收集该路径本身及所有父目录
        p = os.path.normpath(path)
        while p:
            paths_to_invalidate.add(p)
            parent = os.path.dirname(p)
            if parent == p:
                break
            p = parent
        
        for dir_path in paths_to_invalidate:
            keys = self._directory_path_keys.pop(dir_path, set())
            for key in keys:
                try:
                    del self._directory_cache[key]
                except KeyError:
                    pass
    
    # 搜索结果缓存
    def get_search(
        self,
        query: str,
        root_dir: str,
        **options
    ) -> Optional[Dict[str, Any]]:
        """获取搜索结果缓存"""
        key = self._generate_key("search", query, root_dir, **options)
        try:
            value = self._search_cache.get(key)
            if value is not None:
                self._stats["search"].hits += 1
                return value
            self._stats["search"].misses += 1
            return None
        except KeyError:
            self._stats["search"].misses += 1
            return None
    
    def set_search(
        self,
        query: str,
        root_dir: str,
        data: Dict[str, Any],
        **options
    ) -> None:
        """设置搜索结果缓存"""
        key = self._generate_key("search", query, root_dir, **options)
        self._search_cache[key] = data
        self._stats["search"].size = len(self._search_cache)
    
    # 任务状态缓存
    def get_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        """获取任务状态缓存"""
        try:
            value = self._task_cache.get(task_id)
            if value is not None:
                self._stats["task"].hits += 1
                return value
            self._stats["task"].misses += 1
            return None
        except KeyError:
            self._stats["task"].misses += 1
            return None
    
    def set_task(self, task_id: str, data: Dict[str, Any]) -> None:
        """设置任务状态缓存"""
        self._task_cache[task_id] = data
        self._stats["task"].size = len(self._task_cache)
    
    def invalidate_task(self, task_id: str) -> None:
        """使任务状态缓存失效"""
        try:
            del self._task_cache[task_id]
        except KeyError:
            pass
    
    # 通用缓存方法
    def get(self, key: str) -> Optional[Any]:
        """获取通用缓存"""
        try:
            value = self._lru_cache.get(key)
            if value is not None:
                self._stats["lru"].hits += 1
                return value
            self._stats["lru"].misses += 1
            return None
        except KeyError:
            self._stats["lru"].misses += 1
            return None
    
    def set(self, key: str, value: Any) -> None:
        """设置通用缓存"""
        self._lru_cache[key] = value
        self._stats["lru"].size = len(self._lru_cache)
    
    def delete(self, key: str) -> None:
        """删除通用缓存"""
        try:
            del self._lru_cache[key]
        except KeyError:
            pass
    
    # 缓存管理
    def clear_all(self) -> None:
        """清空所有缓存"""
        self._metadata_cache.clear()
        self._directory_cache.clear()
        self._directory_path_keys.clear()
        self._search_cache.clear()
        self._task_cache.clear()
        self._lru_cache.clear()
        
        for stats in self._stats.values():
            stats.size = 0
        
        logger.info("所有缓存已清空")
    
    def get_stats(self) -> Dict[str, Dict[str, Any]]:
        """获取缓存统计"""
        return {
            name: {
                "hits": stats.hits,
                "misses": stats.misses,
                "evictions": stats.evictions,
                "size": stats.size,
                "hit_rate": f"{stats.hit_rate:.2%}",
            }
            for name, stats in self._stats.items()
        }
    
    def get_total_size(self) -> int:
        """获取总缓存大小"""
        return (
            len(self._metadata_cache) +
            len(self._directory_cache) +
            len(self._search_cache) +
            len(self._task_cache) +
            len(self._lru_cache)
        )


def cached(
    cache_type: str = "lru",
    ttl: Optional[int] = None,
    key_func: Optional[Callable] = None
):
    """
    缓存装饰器
    
    Args:
        cache_type: 缓存类型 (metadata, directory, search, task, lru)
        ttl: 缓存过期时间（秒）
        key_func: 自定义键生成函数
    """
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            # 获取缓存管理器实例
            cache_manager = get_cache_manager()
            
            # 生成缓存键
            if key_func:
                cache_key = key_func(*args, **kwargs)
            else:
                cache_key = cache_manager._generate_key(func.__name__, *args, **kwargs)
            
            # 尝试从缓存获取
            cached_value = cache_manager.get(cache_key)
            if cached_value is not None:
                return cached_value
            
            # 执行函数
            result = await func(*args, **kwargs)
            
            # 存入缓存
            cache_manager.set(cache_key, result)
            
            return result
        
        return wrapper
    return decorator


# 全局缓存管理器实例
_cache_manager: Optional[CacheManager] = None


def get_cache_manager() -> CacheManager:
    """获取全局缓存管理器"""
    global _cache_manager
    if _cache_manager is None:
        _cache_manager = CacheManager()
    return _cache_manager


def set_cache_manager(manager: CacheManager) -> None:
    """设置全局缓存管理器"""
    global _cache_manager
    _cache_manager = manager


def reset_cache_manager() -> None:
    """重置全局缓存管理器"""
    global _cache_manager
    if _cache_manager:
        _cache_manager.clear_all()
    _cache_manager = None
