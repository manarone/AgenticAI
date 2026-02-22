"""Shared exception tuples for queue and bus error handling."""

try:
    from redis.exceptions import RedisError

    BUS_EXCEPTIONS: tuple[type[Exception], ...] = (
        RedisError,
        RuntimeError,
        TimeoutError,
        ConnectionError,
        OSError,
    )
except ImportError:
    BUS_EXCEPTIONS = (
        RuntimeError,
        TimeoutError,
        ConnectionError,
        OSError,
    )

# Alias for route-level enqueue/dequeue usage to keep call sites expressive.
QUEUE_EXCEPTIONS = BUS_EXCEPTIONS
