import logging
import time

from fastapi import Request

from app.errors import ApiError

logger = logging.getLogger(__name__)

# DECISION: Rate limit Lua script from SPEC §16 (Redis fixed window, returns count).
RATE_LIMIT_LUA = """-- rate_limit.lua
local n = redis.call('INCR', KEYS[1])
if n == 1 then redis.call('PEXPIRE', KEYS[1], ARGV[1]) end
return n
"""

# Limits per action from SPEC §16: (limit, window_seconds)
LIMITS = {
    "search": (120, 60),
    "preview": (30, 60),
    "commit": (30, 60),
    "ann_lab": (30, 60),
    "llm": (20, 3600),
}


async def check_rate_limit(request: Request, scope: str, limit: int, window_seconds: int) -> None:
    settings = request.app.state.settings
    if not settings.rate_limit_enabled:
        return

    redis = getattr(request.app.state, "redis", None)
    if redis is None:
        return

    # DECISION: Client IP is request.client.host; uvicorn --proxy-headers handles reverse proxies.
    ip = request.client.host if request.client else "127.0.0.1"
    now = int(time.time())
    window_start = (now // window_seconds) * window_seconds
    key = f"tl:rl:{scope}:{ip}:{window_start}"
    window_ms = window_seconds * 1000
    retry_after = max(1, (window_start + window_seconds) - now)

    try:
        count = await redis.eval(RATE_LIMIT_LUA, 1, key, window_ms)
        if count > limit:
            raise ApiError(
                status=429,
                code="RATE_LIMITED",
                title="Rate Limited",
                detail=f"Rate limit exceeded for {scope}. Try again in {retry_after} seconds.",
                headers={"Retry-After": str(retry_after)},
            )
    except ApiError:
        raise
    except Exception as exc:
        # DECISION: Fail open on Redis errors for per-IP rate limits (log error type only).
        logger.warning("Redis rate limit check failed (%s)", type(exc).__name__)
        return


async def check_llm_limit(request: Request) -> bool:
    settings = request.app.state.settings
    if not settings.llm_enabled:
        return False
    if not settings.rate_limit_enabled:
        return True

    redis = getattr(request.app.state, "redis", None)
    if redis is None:
        logger.warning("LLM rate limit check failed (Redis is None)")
        return False

    ip = request.client.host if request.client else "127.0.0.1"
    limit, window_seconds = LIMITS["llm"]
    now = int(time.time())
    window_start = (now // window_seconds) * window_seconds
    key = f"tl:rl:llm:{ip}:{window_start}"
    window_ms = window_seconds * 1000

    try:
        count = await redis.eval(RATE_LIMIT_LUA, 1, key, window_ms)
        if count > limit:
            return False
        return True
    except Exception as exc:
        # DECISION: LLM per-IP limit fails closed on Redis errors, same as daily budget.
        logger.warning("LLM rate limit check failed (%s)", type(exc).__name__)
        return False


async def limit_search(request: Request) -> None:
    limit, window = LIMITS["search"]
    await check_rate_limit(request, "search", limit, window)


async def limit_preview(request: Request) -> None:
    limit, window = LIMITS["preview"]
    await check_rate_limit(request, "preview", limit, window)


async def limit_items(request: Request) -> None:
    limit, window = LIMITS["commit"]
    await check_rate_limit(request, "commit", limit, window)


async def limit_ann_lab(request: Request) -> None:
    limit, window = LIMITS["ann_lab"]
    await check_rate_limit(request, "ann_lab", limit, window)
