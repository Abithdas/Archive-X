"""Serves the built React frontend (frontend/dist) at /app-next/ next to the classic UI."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse, RedirectResponse

PREFIX = "/app-next"
NOT_BUILT = "frontend not built: run npm run build in frontend/"


def register_frontend_routes(router: APIRouter, *, dist_dir: Path) -> None:
    dist_root = Path(dist_dir)

    @router.get(PREFIX)
    async def frontend_root_redirect():
        return RedirectResponse(f"{PREFIX}/")

    @router.get(PREFIX + "/{path:path}")
    async def frontend_file(path: str):
        index = dist_root / "index.html"
        if not index.is_file():
            return PlainTextResponse(NOT_BUILT, status_code=404)
        relative = Path(path)
        if "\\" in path or relative.is_absolute() or ".." in relative.parts:
            raise HTTPException(404)
        candidate = (dist_root / relative).resolve()
        if path and candidate.is_file() and dist_root.resolve() in candidate.parents:
            if relative.parts[0] == "assets":
                cache = "public, max-age=31536000, immutable"
            else:
                cache = "public, max-age=3600"
            return FileResponse(candidate, headers={"Cache-Control": cache})
        if relative.suffix:
            raise HTTPException(404)
        return FileResponse(index, headers={"Cache-Control": "no-store"})
