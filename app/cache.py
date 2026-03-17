from __future__ import annotations

from datetime import datetime, timedelta
from functools import wraps
from threading import Lock
from typing import Any, Callable


_KW_MARKER = "__kw__"


def ttl_cache(seconds: int, maxsize: int = 128) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        cache: dict[tuple[Any, ...], tuple[datetime, Any]] = {}
        lock = Lock()
        ttl = timedelta(seconds=seconds)

        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            key = args
            if kwargs:
                key += (_KW_MARKER,)
                key += tuple(sorted(kwargs.items()))

            now = datetime.utcnow()
            with lock:
                cached = cache.get(key)
                if cached and now - cached[0] <= ttl:
                    return cached[1]

            value = func(*args, **kwargs)

            with lock:
                cache[key] = (now, value)
                if len(cache) > maxsize:
                    oldest_key = min(cache.items(), key=lambda item: item[1][0])[0]
                    cache.pop(oldest_key, None)
            return value

        def cache_clear() -> None:
            with lock:
                cache.clear()

        wrapper.cache_clear = cache_clear  # type: ignore[attr-defined]
        return wrapper

    return decorator
