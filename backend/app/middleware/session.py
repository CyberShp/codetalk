import uuid
from typing import Any


class AnonymousSessionMiddleware:
    """ASGI middleware that assigns a persistent anonymous session ID to every browser.

    - Reads ``codetalk_session`` from incoming Cookie header.
    - Validates UUID4 format; generates a fresh UUID4 if absent or invalid.
    - Injects ``session_id`` into ``scope["state"]`` so downstream handlers can
      access it via ``request.state.session_id``.
    - For new sessions, appends ``Set-Cookie`` to the HTTP response headers.
    - WebSocket handshakes also receive the session_id (read-only; no response
      header is set because the WS handshake response goes through the HTTP path
      before the upgrade, so Set-Cookie is handled there when the session is new).
    - No Redis, no database — purely stateless cookie-based identity.
    """

    COOKIE_NAME = "codetalk_session"
    MAX_AGE = 31_536_000  # 1 year in seconds

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        session_id, is_new = self._get_or_create(scope)

        # Inject into scope state (Starlette State wraps this dict)
        if "state" not in scope:
            scope["state"] = {}
        scope["state"]["session_id"] = session_id

        if not is_new:
            # Existing session — pass through unchanged
            await self.app(scope, receive, send)
            return

        # New session — intercept http.response.start to append Set-Cookie
        cookie_header = (
            f"{self.COOKIE_NAME}={session_id}; "
            f"Path=/; HttpOnly; SameSite=Lax; Max-Age={self.MAX_AGE}"
        ).encode()

        async def send_with_cookie(message: dict) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.append((b"set-cookie", cookie_header))
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_cookie)

    def _get_or_create(self, scope: dict) -> tuple[str, bool]:
        """Return ``(session_id, is_new)``.

        Parses the raw ``cookie`` header without depending on any framework
        helpers so the middleware stays ASGI-portable.
        """
        raw_cookie = ""
        for header_name, header_value in scope.get("headers", []):
            if header_name == b"cookie":
                raw_cookie = header_value.decode("latin-1")
                break

        for part in raw_cookie.split(";"):
            key, _, value = part.strip().partition("=")
            if key.strip() == self.COOKIE_NAME and value.strip():
                candidate = value.strip()
                try:
                    parsed = uuid.UUID(candidate, version=4)
                    if parsed.version == 4:
                        return str(parsed), False
                except (ValueError, AttributeError):
                    pass

        return str(uuid.uuid4()), True
