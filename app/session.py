from uuid import UUID, uuid4

from fastapi import Request
from sqlalchemy import text
from starlette.middleware.base import BaseHTTPMiddleware

from app.db import write_transaction


class SessionMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if path in {"/healthz", "/readyz"} or path.startswith("/static/"):
            return await call_next(request)

        try:
            session_id = UUID(request.cookies.get("tl_session", ""))
            new_cookie = False
        except ValueError:
            session_id = uuid4()
            new_cookie = True

        async with write_transaction(request.app.state.engine) as connection:
            await connection.execute(
                text(
                    "INSERT INTO sessions (id) VALUES (:id) "
                    "ON CONFLICT (id) DO UPDATE SET last_seen = now() "
                    # a 24 h idle TTL needs no finer clock; skipping the rewrite saves a dead row
                    # (and its WAL) on nearly every request
                    "WHERE sessions.last_seen < now() - interval '5 minutes'"
                ),
                {"id": session_id},
            )
        request.state.session_id = session_id
        response = await call_next(request)
        if new_cookie:
            settings = request.app.state.settings
            response.set_cookie(
                "tl_session",
                str(session_id),
                max_age=settings.session_ttl_hours * 3600,
                path="/",
                httponly=True,
                samesite="lax",
                secure=settings.app_env == "prod",
            )
        return response


def current_session_id(request: Request) -> UUID:
    return request.state.session_id
