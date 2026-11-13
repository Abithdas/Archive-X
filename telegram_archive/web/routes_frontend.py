"""Serves the built React frontend (frontend/dist) as the only UI: files from dist, and index.html for every client-side route."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse, RedirectResponse

NOT_BUILT = "frontend not built: run npm run build in frontend/"
OLD_PREFIX = "/app-next"
# Paths that belong to the backend: an unknown one is a 404, never the app shell.
REVALIDATE = ("sw.js", "manifest.webmanifest")
RESERVED = ("api", "stream", "static", "ws", "desktop", "health", "ca.crt")


def register_frontend_routes(router: APIRouter, *, dist_dir: Path) -> None:
    """Must be registered after every other route (the app factory does it last): it answers any GET that nothing else matched."""
    dist_root = Path(dist_dir).resolve()

    @router.get(OLD_PREFIX)
    @router.get(OLD_PREFIX + "/{path:path}")
    async def old_prefix_redirect(path: str = ""):
        return RedirectResponse("/" + path)

    @router.get("/{path:path}")
    async def frontend_file(path: str):
        index = dist_root / "index.html"
        if not index.is_file():
            return PlainTextResponse(NOT_BUILT, status_code=404)
        relative = Path(path)
        if "\\" in path or relative.is_absolute() or ".." in relative.parts:
            raise HTTPException(404)
        first = relative.parts[0] if relative.parts else ""
        if first in RESERVED:
            raise HTTPException(404)
        candidate = (dist_root / relative).resolve()
        if path and candidate.is_file() and dist_root in candidate.parents:
            immutable = first == "assets"
            # The service worker and manifest must be re-checked on every load, or an update to the app would never reach an installed copy.
            cache = "no-cache" if path in REVALIDATE else "public, max-age=31536000, immutable" if immutable else "public, max-age=3600"
            media = "application/manifest+json" if path == "manifest.webmanifest" else None
            return FileResponse(candidate, media_type=media, headers={"Cache-Control": cache})
        if relative.suffix:
            raise HTTPException(404)
        return FileResponse(index, headers={"Cache-Control": "no-store"})

