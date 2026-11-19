"""A network error in words a person can act on (the raw ones read like '[WinError 10054] An existing connection was forcibly closed')."""
from __future__ import annotations

import socket
import ssl
import urllib.error


def explain(exc: BaseException, host: str = "") -> str:
    """One short sentence for what went wrong reaching a provider; '' for an error that is not a network one."""
    reason = getattr(exc, "reason", exc) if isinstance(exc, urllib.error.URLError) else exc
    text = str(reason)
    where = f" ({host})" if host else ""
    if isinstance(reason, ConnectionResetError) or "10054" in text or "forcibly closed" in text or "Connection reset" in text:
        return f"The provider{where} closed the connection. Your network, ISP or a firewall may be blocking it; try another network, a VPN or another DNS."
    if isinstance(reason, (socket.timeout, TimeoutError)) or "timed out" in text:
        return f"The provider{where} did not answer in time. Check the connection and try again."
    if isinstance(reason, socket.gaierror) or "getaddrinfo" in text or "Name or service not known" in text:
        return f"The provider's address{where} could not be found. Check the internet connection or DNS."
    if isinstance(reason, ssl.SSLError) or "CERTIFICATE" in text.upper():
        return f"A secure connection to the provider{where} could not be made (certificate or TLS problem)."
    if isinstance(exc, urllib.error.HTTPError):
        if exc.code in (401, 403):
            return f"The provider{where} refused the key (HTTP {exc.code}). Check it in Settings, Metadata."
        if exc.code == 429:
            return f"The provider{where} says too many requests (HTTP 429). It will work again after a short wait."
        return f"The provider{where} answered HTTP {exc.code}."
    if isinstance(exc, (urllib.error.URLError, OSError)):
        return f"The provider{where} could not be reached: {text[:120]}"
    return ""
