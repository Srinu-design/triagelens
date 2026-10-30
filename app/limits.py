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


async def _over_limit(request: Request, scope: str) -> int:
    """Count this request in its fixed window; return seconds until the window resets when the
    count is over the limit, else 0. Redis errors propagate so each caller picks its fail mode."""
    limit, window_seconds = LIMITS[scope]
    # DECISION: Client IP is request.client.host; uvicorn --proxy-headers handles reverse proxies.
    ip = request.client.host if request.client else "127.0.0.1"
    now = int(time.time())
    window_start = (now // window_seconds) * window_seconds
    key = f"tl:rl:{scope}:{ip}:{window_start}"
    count = await request.app.state.redis.eval(RATE_LIMIT_LUA, 1, key, window_seconds * 1000)
    return max(1, window_start + window_seconds - now) if count > limit else 0


def rate_limit(scope: str):
    """A route dependency enforcing the per-IP limit for scope."""

    async def check(request: Request) -> None:
        if not request.app.state.settings.rate_limit_enabled:
            return
        try:
            retry_after = await _over_limit(request, scope)
        except Exception as exc:
            # DECISION: Fail open on Redis errors for per-IP rate limits (log error type only).
            logger.warning("Redis rate limit check failed (%s)", type(exc).__name__)
            return
        if retry_after:
            raise ApiError(
                status=429,
                code="RATE_LIMITED",
                title="Rate Limited",
                detail=f"Rate limit exceeded for {scope}. Try again in {retry_after} seconds.",
                headers={"Retry-After": str(retry_after)},
            )

    return check


async def check_llm_limit(request: Request) -> bool:
    settings = request.app.state.settings
    if not settings.llm_enabled:
        return False
    if not settings.rate_limit_enabled:
        return True
    try:
        return not await _over_limit(request, "llm")
    except Exception as exc:
        # DECISION: LLM per-IP limit fails closed on Redis errors, same as daily budget.
        logger.warning("LLM rate limit check failed (%s)", type(exc).__name__)
        return False
