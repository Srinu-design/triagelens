import json
import re
import time
import uuid

from app.errors import make_problem_response
from app.logs import logger as access_logger

MAX_BODY_SIZE = 16 * 1024  # 16 KB = 16,384 bytes
REQUEST_ID_REGEX = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

# DECISION: Security headers applied on every HTTP response via plain ASGI middleware.
SECURITY_HEADERS = [
    (
        b"content-security-policy",
        b"default-src 'self'; script-src 'self'; style-src 'self'; "
        b"img-src 'self' data:; connect-src 'self'; font-src 'self'; "
        b"object-src 'none'; base-uri 'none'; frame-ancestors 'none'; "
        b"form-action 'self'",
    ),
    (b"x-content-type-options", b"nosniff"),
    (b"referrer-policy", b"strict-origin-when-cross-origin"),
    (b"x-frame-options", b"DENY"),
    (b"permissions-policy", b"camera=(), microphone=(), geolocation=()"),
    (b"cross-origin-opener-policy", b"same-origin"),
]
SECURITY_HEADER_NAMES = {name for name, _ in SECURITY_HEADERS}


class BodyLimitMiddleware:
    """Plain ASGI middleware enforcing the 16 KB request body limit."""

    def __init__(self, app, max_body_size: int = MAX_BODY_SIZE):
        self.app = app
        self.max_body_size = max_body_size

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def too_large():
            # DECISION: The §10 error table has no code for 413; use PAYLOAD_TOO_LARGE.
            await make_problem_response(
                413,
                "PAYLOAD_TOO_LARGE",
                "Payload Too Large",
                f"Request body exceeds {self.max_body_size // 1024} KB limit.",
            )(scope, receive, send)

        # 1. Check Content-Length header before reading chunks
        cl_header = dict(scope.get("headers", [])).get(b"content-length")
        if cl_header:
            try:
                if int(cl_header.decode("latin1")) > self.max_body_size:
                    await too_large()
                    return
            except ValueError:
                pass

        # 2. Buffer streamed chunks up to max_body_size
        chunks = []
        total_bytes = 0
        while True:
            message = await receive()
            if message["type"] == "http.request":
                chunk = message.get("body", b"")
                total_bytes += len(chunk)
                if total_bytes > self.max_body_size:
                    await too_large()
                    return
                chunks.append(chunk)
                if not message.get("more_body", False):
                    break
            elif message["type"] == "http.disconnect":
                return

        body = b"".join(chunks)
        sent = False

        async def replayed_receive():
            nonlocal sent
            if not sent:
                sent = True
                return {
                    "type": "http.request",
                    "body": body,
                    "more_body": False,
                }
            return await receive()

        await self.app(scope, replayed_receive, send)


class SecurityHeadersMiddleware:
    """Plain ASGI middleware adding security headers to all HTTP responses."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                headers = [
                    (k, v)
                    for k, v in message.get("headers", [])
                    if k.lower() not in SECURITY_HEADER_NAMES
                ]
                headers.extend(SECURITY_HEADERS)
                # Revalidate pages and assets (cheap with the ETag) so a deploy shows at once.
                if not any(k.lower() == b"cache-control" for k, _ in headers):
                    headers.append((b"cache-control", b"no-cache"))
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_with_headers)


class RequestLogMiddleware:
    """Plain ASGI middleware logging exactly one structured JSON line per request."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers", []))
        raw_req_id = headers.get(b"x-request-id")
        req_id_str = raw_req_id.decode("latin1").strip() if raw_req_id else ""
        if req_id_str and REQUEST_ID_REGEX.match(req_id_str):
            request_id = req_id_str
        else:
            request_id = uuid.uuid4().hex

        scope.setdefault("state", {})["request_id"] = request_id
        start_time = time.perf_counter()
        response_status = 500

        async def send_wrapper(message):
            nonlocal response_status
            if message["type"] == "http.response.start":
                response_status = message["status"]
                raw_headers = list(message.get("headers", []))
                raw_headers.append((b"x-request-id", request_id.encode("latin1")))
                message["headers"] = raw_headers
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            latency_ms = round((time.perf_counter() - start_time) * 1000, 2)
            state = scope.get("state", {})
            log_data = {
                "request_id": request_id,
                "method": scope.get("method", ""),
                "path": scope.get("path", ""),
                "status": response_status,
                "latency_ms": latency_ms,
                "embed_ms": state.get("embed_ms"),
                "db_ms": state.get("db_ms"),
                "verdict": state.get("verdict"),
                "classifier_source": state.get("classifier_source"),
                "redaction_total": state.get("redaction_total"),
            }
            access_logger.info(json.dumps(log_data))
