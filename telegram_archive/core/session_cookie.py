"""Session cookies for the native LAN app and managed, embedded hosting."""
from __future__ import annotations

from .. import config


def set_session_cookie(response, request, token: str | None = None) -> None:
    # Replit's HTTPS preview is embedded on a different site. Strict cookies
    # are not sent in that iframe. CHIPS isolates its cookie to the embedding
    # site while HttpOnly and Secure keep the token out of JavaScript/HTTP.
    embedded = bool(getattr(request.app.state, "embedded_session_cookies", False))
    secure = embedded or str(request.url.scheme).lower() == "https"
    options = dict(path="/", secure=secure, httponly=True,
                   samesite="none" if embedded else "strict")
    if token is None:
        response.delete_cookie(config.SESSION_COOKIE, **options)
    else:
        response.set_cookie(config.SESSION_COOKIE, token,
                            max_age=config.SESSION_TTL_SECONDS, **options)
    if embedded:
        # Python 3.11's SimpleCookie doesn't know the Partitioned attribute.
        # Append only to our session cookie, not to unrelated response cookies.
        prefix = (config.SESSION_COOKIE + "=").encode("ascii")
        response.raw_headers = [
            (key, value + b"; Partitioned")
            if key.lower() == b"set-cookie" and value.startswith(prefix)
            else (key, value)
            for key, value in response.raw_headers
        ]
