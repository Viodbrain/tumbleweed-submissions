"""Bound request bodies and avoid caching maintainer data or session responses."""

from starlette.responses import JSONResponse


class DirectorySecurity:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        path = scope["path"]
        protected = path.startswith(("/api/moderation", "/api/maintainer"))
        directory = path in ("/directory", "/maintain") or path.startswith("/directory-assets")
        if scope["method"] in ("POST", "PUT") and path.startswith(
            ("/api/submissions", "/api/moderation", "/api/maintainer")
        ):
            body = bytearray()
            while True:
                message = await receive()
                if message["type"] == "http.disconnect":
                    return
                body.extend(message.get("body", b""))
                if len(body) > 16384:
                    return await JSONResponse({"detail": "Request is too large"}, status_code=413)(scope, receive, send)
                if not message.get("more_body", False):
                    break
            original_receive = receive
            delivered = False

            async def replay():
                nonlocal delivered
                if not delivered:
                    delivered = True
                    return {"type": "http.request", "body": bytes(body), "more_body": False}
                return await original_receive()

            receive = replay

        async def secure_send(message):
            if message["type"] == "http.response.start" and (protected or directory):
                headers = list(message.get("headers", []))
                headers.extend([(b"x-content-type-options", b"nosniff"), (b"referrer-policy", b"no-referrer")])
                if directory:
                    headers.append((b"x-frame-options", b"DENY"))
                if protected:
                    headers.append((b"cache-control", b"no-store"))
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, secure_send)
