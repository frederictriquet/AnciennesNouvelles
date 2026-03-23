# Pattern de retry avec backoff exponentiel [ARCHITECTURE.md — utils/retry.py, ARCH-m2]
from __future__ import annotations

import asyncio
import logging
from typing import Callable, Awaitable, TypeVar

from ancnouv.exceptions import ImageHostingError

logger = logging.getLogger(__name__)

T = TypeVar("T")

# [ARCH-m2] Exceptions non-retriables : upload_to_remote gère ses propres retries en interne.
# Inclure ImageHostingError ici évite un double-wrapping (retry sur un échec déjà définitif).
NON_RETRIABLE: tuple[type[Exception], ...] = (ImageHostingError,)


async def with_retry(
    func: Callable[..., Awaitable[T]],
    *args,
    max_attempts: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 60.0,
    **kwargs,
) -> T:
    """Exécute func avec backoff exponentiel sur exception retriable.

    Levée immédiate sans retry si l'exception est dans NON_RETRIABLE.
    Délais : 1s, 2s, 4s (plafonné à max_delay).

    Usage :
        result = await with_retry(my_async_func, arg1, arg2, max_attempts=3)
    """
    last_exc: Exception | None = None

    for attempt in range(max_attempts):
        try:
            return await func(*args, **kwargs)
        except NON_RETRIABLE:
            raise
        except Exception as exc:
            last_exc = exc
            if attempt + 1 >= max_attempts:
                break
            delay = min(base_delay * (2 ** attempt), max_delay)
            logger.warning(
                "Tentative %d/%d échouée pour %s : %s — retry dans %.1fs",
                attempt + 1,
                max_attempts,
                getattr(func, "__name__", str(func)),
                exc,
                delay,
            )
            await asyncio.sleep(delay)

    raise last_exc  # type: ignore[misc]
