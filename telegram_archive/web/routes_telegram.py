"""Explicit registration for Telegram account and channel management routes."""

# ------------------------------------------------------------------
# FILE MAP (search for "SECTION:" to jump between sections)
#       - Telegram login
#       - Session and settings
#       - Channel editing
#       - Local account profile
#       - Channels
#       - Playback Snapshot Builder
#       - Content Automation configuration
# ------------------------------------------------------------------
from __future__ import annotations

from ..core.swallowed import note as _swallowed_note
from ..core.safe_paths import inside
import asyncio
import io
import json
import os
import re
import sys
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, Response

from ..core import telethon_lazy

_tl = telethon_lazy.errors()   # Telegram's error classes, looked up only when an exception reaches an `except _tl.X` (the library is not loaded at start-up)

from .image_payloads import parse_manual_image_payload as _parse_manual_image_payload


def register_telegram_routes(
    router: APIRouter,
    *,
    db,
    governor,
    jobs,
    telegram,
    require_auth,
    config,
    content_automation,
    workflow_snapshot,
    TELETHON_AVAILABLE,
    _channel_profile_photo_cache,
    _channel_profile_photo_locks,
) -> dict[str, object]:

    # ------------------------------------------------------------------
    # SUBSECTION: Telegram login
    # Code, 2FA and QR login.
    # ------------------------------------------------------------------
    """Register Telegram account, channel and workflow routes."""
    @router.post("/api/telegram/verify-code")
    async def telegram_verify_code(request: Request):
        """POST /api/telegram/verify-code: Telegram verify code."""
        require_auth(request)
        data = await request.json()
        try:
            return await telegram.verify_login_code(
                str(data.get("phone", "")).strip(),
                str(data.get("code", "")).strip()
            )
        except _tl.FloodWaitError as e:
            seconds = getattr(e, "seconds", 60)
            governor.note_floodwait(seconds)
            raise HTTPException(429, f"Telegram FloodWait: wait {seconds} seconds")
        except Exception as e:
            db.log(f"Telegram code verification error: {e}", "ERROR")
            raise HTTPException(400, str(e))

    @router.post("/api/telegram/verify-2fa")
    async def telegram_verify_2fa(request: Request):
        """POST /api/telegram/verify-2fa: Telegram verify 2fa."""
        require_auth(request)
        data = await request.json()
        try:
            return await telegram.verify_2fa(str(data.get("password", "")))
        except _tl.PasswordHashInvalidError:
            raise HTTPException(400, "Incorrect Telegram Cloud Password. Please try again.")
        except _tl.FloodWaitError as e:
            seconds = getattr(e, "seconds", 60)
            governor.note_floodwait(seconds)
            raise HTTPException(429, f"Telegram FloodWait: wait {seconds} seconds")
        except Exception:
            db.log("Telegram 2FA verification failed", "ERROR")
            raise HTTPException(400, "Telegram 2FA verification failed")

    @router.post("/api/telegram/qr-login/start")
    async def telegram_qr_login_start(request: Request):
        """Start QR sign-in on the same Telethon client/session used by Archive X."""
        require_auth(request)
        try:
            return await telegram.start_qr_login()
        except _tl.FloodWaitError as e:
            seconds = getattr(e, "seconds", 60)
            governor.note_floodwait(seconds)
            raise HTTPException(429, f"Telegram FloodWait: wait {seconds} seconds")
        except (ValueError, RuntimeError) as e:
            raise HTTPException(400, str(e))
        except Exception as e:
            db.log(f"Telegram QR login start failed: {type(e).__name__}: {e}", "ERROR")
            raise HTTPException(400, "Could not start Telegram QR login. Check API credentials and connection.")

    @router.get("/api/telegram/qr-login/status")
    async def telegram_qr_login_status(request: Request):
        """Return current QR state; the QR image contains the short-lived login token."""
        require_auth(request)
        return telegram.qr_login_status()

    @router.post("/api/telegram/qr-login/refresh")
    async def telegram_qr_login_refresh(request: Request):
        """Generate a fresh QR token when the user requests a manual refresh."""
        require_auth(request)
        try:
            return await telegram.start_qr_login()
        except _tl.FloodWaitError as e:
            seconds = getattr(e, "seconds", 60)
            governor.note_floodwait(seconds)
            raise HTTPException(429, f"Telegram FloodWait: wait {seconds} seconds")
        except (ValueError, RuntimeError) as e:
            raise HTTPException(400, str(e))
        except Exception as e:
            db.log(f"Telegram QR refresh failed: {type(e).__name__}: {e}", "ERROR")
            raise HTTPException(400, "Could not refresh the Telegram QR code.")

    @router.post("/api/telegram/qr-login/cancel")
    async def telegram_qr_login_cancel(request: Request):
        """Stop QR polling without deleting or replacing the saved Telegram session."""
        require_auth(request)
        return await telegram.cancel_qr_login()


    # ------------------------------------------------------------------
    # SUBSECTION: Session and settings
    # Logout, self-test and proxy settings.
    # ------------------------------------------------------------------
    @router.post("/api/telegram/logout-account")
    async def telegram_logout_account(request: Request):
        """Revoke the active Telegram account session; keep API credentials saved."""
        require_auth(request)
        try:
            return await telegram.logout_account()
        except RuntimeError as e:
            raise HTTPException(400, str(e))
        except Exception as e:
            db.log(f"Telegram account logout failed: {type(e).__name__}: {e}", "ERROR")
            raise HTTPException(400, "Could not log out of Telegram. Check the connection and retry.")

    @router.post("/api/telegram/reset-invalid-session")
    async def telegram_reset_invalid_session(request: Request):
        """Archive a Telegram-rejected local session so a fresh authorization can be created."""
        require_auth(request)
        try:
            return await telegram.reset_invalid_session()
        except RuntimeError as e:
            raise HTTPException(409, str(e))
        except Exception as e:
            db.log(f"Telegram invalid-session recovery failed: {type(e).__name__}", "ERROR")
            raise HTTPException(500, "Could not archive the invalid Telegram session.")

    @router.post("/api/telegram/self-test")
    async def telegram_self_test(request: Request):
        """Test Telegram API credentials/network without account login."""
        require_auth(request)
        try:
            return await telegram.self_test_connection()
        except Exception as e:
            db.log(f"Telegram API self-test failed: {e}", "ERROR")
            raise HTTPException(400, str(e))

    @router.get("/api/telegram/auth-status")
    async def telegram_auth_status(request: Request):
        """GET /api/telegram/auth-status: Telegram auth status."""
        require_auth(request)
        return {
            "connected": telegram.connected,
            "status": telegram.status_text,
            "session_invalidated": telegram.session_invalidated,
            "send_code_endpoint": "/api/telegram/send-code",
            "verify_code_endpoint": "/api/telegram/verify-code",
            "verify_2fa_endpoint": "/api/telegram/verify-2fa",
        }

    @router.get("/api/settings/telegram")
    async def get_telegram_settings(request: Request):
        """GET /api/settings/telegram: Get telegram settings."""
        require_auth(request)
        from ..telegram.client_policy import telegram_proxy_settings_for_ui
        proxy_settings = telegram_proxy_settings_for_ui()
        cfg = telegram.config()
        api_hash = str(cfg.get("api_hash") or "")
        configured = bool(cfg.get("api_id") and api_hash)
        api_verified = bool(configured and (telegram.connected or str(telegram.status_text or "") == "LOGIN REQUIRED"))
        return {
            "configured": configured,
            "api_verified": api_verified,
            "api_id": cfg.get("api_id") or "",
            # Never echo the full hash back to the browser once saved —
            # same principle as the web login password.
            "api_hash_masked": (api_hash[:4] + "…" + api_hash[-4:]) if len(api_hash) > 8 else ("set" if api_hash else ""),
            "phone": cfg.get("phone") or "",
            "connected": telegram.connected,
            "status": telegram.status_text,
            "session_invalidated": telegram.session_invalidated,
            "proxy_status": proxy_settings["status"],
            "proxy_settings": proxy_settings,
        }

    @router.post("/api/settings/telegram")
    async def save_telegram_settings(request: Request):
        """POST /api/settings/telegram: Save telegram settings."""
        require_auth(request)
        data = await request.json()
        try:
            result = await telegram.set_credentials(
                data.get("api_id", ""), data.get("api_hash", ""), data.get("phone", "")
            )
            cfg = telegram.config()
            configured = bool(cfg.get("api_id") and cfg.get("api_hash"))
            api_verified = bool(configured and (telegram.connected or str(telegram.status_text or "") == "LOGIN REQUIRED"))
            return {"ok": True, "configured": configured, "api_verified": api_verified, **result}
        except ValueError as e:
            raise HTTPException(400, str(e))
        except _tl.FloodWaitError as e:
            seconds = getattr(e, "seconds", 60)
            governor.note_floodwait(seconds)
            raise HTTPException(429, f"Telegram FloodWait: wait {seconds} seconds")
        except Exception as e:
            db.log(f"Saving Telegram settings failed: {e}", "ERROR")
            raise HTTPException(400, str(e))

    @router.post("/api/settings/telegram/proxy")
    async def save_telegram_proxy_settings_route(request: Request):
        require_auth(request)
        from ..telegram.client_policy import save_telegram_proxy_settings
        try:
            data = await request.json()
            proxy_settings = save_telegram_proxy_settings(data)
            return {"ok": True, "proxy_settings": proxy_settings}
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        except Exception as exc:
            # Never include request data or exception text: either may contain
            # proxy authentication material from a third-party endpoint.
            db.log(f"Saving Telegram proxy settings failed: {type(exc).__name__}", "ERROR")
            raise HTTPException(500, "Could not save protected Telegram proxy settings.")


    # ------------------------------------------------------------------
    # SUBSECTION: Channel editing
    # Channel edit data and photos.
    # ------------------------------------------------------------------
    @router.get("/api/channels/{channel_id}/edit")
    async def channel_edit_data(channel_id: str, request: Request):
        require_auth(request)
        def _read_channel():
            with db.reading() as rdb:
                row = rdb.execute("SELECT telegram_id,title,username,image,image_source,title_locked,image_locked FROM channels WHERE telegram_id=?", (str(channel_id),)).fetchone()
            return dict(row) if row else None
        row = await asyncio.to_thread(_read_channel)
        if not row:
            raise HTTPException(404, "Channel not found")
        return row

    def _channel_photo_path(cid: str):
        return inside(Path(config.CACHE_DIR) / "channel_photos", f"{cid.lstrip('-') or '0'}{'n' if cid.startswith('-') else ''}.img")

    def _channel_photo_disk(cid: str):
        try:
            data = _channel_photo_path(cid).read_bytes()
        except OSError:
            return None
        if not data:
            return None
        mime = "image/png" if data.startswith(b"\x89PNG") else "image/webp" if data[8:12] == b"WEBP" else "image/gif" if data.startswith(b"GIF8") else "image/jpeg"
        return data, mime

    def _channel_photo_disk_save(cid: str, data: bytes) -> None:
        try:
            path = _channel_photo_path(cid)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        except OSError:
            pass

    @router.get("/api/channels/{channel_id}/telegram-photo")
    async def channel_telegram_photo(channel_id: str, request: Request):
        """Return the current Telegram profile photo for a channel.

        Telegram photos are cached in memory for six hours to avoid one Telegram
        request per library repaint. The cache is invalidated after an explicit
        Archive X profile sync; the browser can retain each response for 15 min.
        """
        require_auth(request)
        cid = str(channel_id)
        if not cid.lstrip("-").isdigit():
            raise HTTPException(404, "Channel not found")
        now = time.monotonic()
        cached = _channel_profile_photo_cache.get(cid)
        if cached and now - cached[0] < 6 * 60 * 60:
            return Response(content=cached[1], media_type=cached[2], headers={
                "Cache-Control": "private, max-age=900", "X-Content-Type-Options": "nosniff"})
        # A copy on disk survives restarts, so channel pictures show at once on launch and while Telegram is offline.
        disk = _channel_photo_disk(cid)
        if cached is None and disk is not None:
            _channel_profile_photo_cache[cid] = cached = (now - 6 * 60 * 60 + 60, disk[0], disk[1])   # served now, refreshed within a minute when Telegram is up
            return Response(content=disk[0], media_type=disk[1], headers={
                "Cache-Control": "private, max-age=300", "X-Content-Type-Options": "nosniff"})
        with db.reading() as rdb:
            exists = rdb.execute("SELECT 1 FROM channels WHERE telegram_id=?", (cid,)).fetchone()
        if not exists:
            raise HTTPException(404, "Channel not found")
        client = getattr(telegram, "client", None)
        connected = bool(getattr(telegram, "connected", False))
        if client is None or not TELETHON_AVAILABLE or not connected:
            if cached:
                return Response(content=cached[1], media_type=cached[2], headers={
                    "Cache-Control": "private, max-age=300", "X-Content-Type-Options": "nosniff"})
            raise HTTPException(503, "Telegram is not connected and no cached profile photo is available")
        lock = _channel_profile_photo_locks.setdefault(cid, asyncio.Lock())
        async with lock:
            cached = _channel_profile_photo_cache.get(cid)
            if cached and time.monotonic() - cached[0] < 6 * 60 * 60:
                return Response(content=cached[1], media_type=cached[2], headers={
                    "Cache-Control": "private, max-age=900", "X-Content-Type-Options": "nosniff"})
            try:
                entity = await client.get_entity(int(cid))
                if not getattr(entity, "photo", None):
                    raise HTTPException(404, "This Telegram channel has no profile photo")
                photo = await client.download_profile_photo(entity, file=bytes)
                if not isinstance(photo, (bytes, bytearray)) or not photo:
                    raise HTTPException(404, "Telegram returned no profile photo")
                data = bytes(photo)
            except HTTPException:
                raise
            except Exception as exc:
                if cached:
                    return Response(content=cached[1], media_type=cached[2], headers={
                        "Cache-Control": "private, max-age=300", "X-Content-Type-Options": "nosniff"})
                raise HTTPException(502, f"Could not load Telegram channel profile photo: {type(exc).__name__}") from exc
            if data.startswith(b"\x89PNG\r\n\x1a\n"):
                mime = "image/png"
            elif data.startswith((b"GIF87a", b"GIF89a")):
                mime = "image/gif"
            elif data.startswith(b"RIFF") and data[8:12] == b"WEBP":
                mime = "image/webp"
            else:
                mime = "image/jpeg"
            _channel_profile_photo_cache[cid] = (time.monotonic(), data, mime)
            _channel_photo_disk_save(cid, data)
            return Response(content=data, media_type=mime, headers={
                "Cache-Control": "private, max-age=900", "X-Content-Type-Options": "nosniff"})

    @router.get("/api/telegram/me/photo")
    async def telegram_account_photo(request: Request):
        """Return the signed-in Telegram account's profile photo (cached six hours)."""
        require_auth(request)
        cached = _channel_profile_photo_cache.get("__me__")
        if cached and time.monotonic() - cached[0] < 6 * 60 * 60:
            return Response(content=cached[1], media_type=cached[2], headers={
                "Cache-Control": "private, max-age=900", "X-Content-Type-Options": "nosniff"})
        client = getattr(telegram, "client", None)
        if client is None or not TELETHON_AVAILABLE or not bool(getattr(telegram, "connected", False)):
            raise HTTPException(503, "Telegram is not connected")
        try:
            photo = await client.download_profile_photo("me", file=bytes)
        except Exception as exc:
            raise HTTPException(502, f"Could not load Telegram profile photo: {type(exc).__name__}") from exc
        if not isinstance(photo, (bytes, bytearray)) or not photo:
            raise HTTPException(404, "This Telegram account has no profile photo")
        data = bytes(photo)
        if data.startswith(b"\x89PNG\r\n\x1a\n"):
            mime = "image/png"
        elif data.startswith(b"RIFF") and data[8:12] == b"WEBP":
            mime = "image/webp"
        else:
            mime = "image/jpeg"
        _channel_profile_photo_cache["__me__"] = (time.monotonic(), data, mime)
        return Response(content=data, media_type=mime, headers={
            "Cache-Control": "private, max-age=900", "X-Content-Type-Options": "nosniff"})

    @router.get("/api/telegram/me/video")
    async def telegram_account_video(request: Request):
        """The signed-in account's animated profile picture (a short looping MP4), when it has one. 404 otherwise: the still photo is used."""
        require_auth(request)
        cached = _channel_profile_photo_cache.get("__me_video__")
        if cached and time.monotonic() - cached[0] < 6 * 60 * 60:
            if not cached[1]:
                raise HTTPException(404, "This Telegram account has no animated profile picture")
            return Response(content=cached[1], media_type="video/mp4", headers={"Cache-Control": "private, max-age=900", "X-Content-Type-Options": "nosniff"})
        client = getattr(telegram, "client", None)
        if client is None or not TELETHON_AVAILABLE or not bool(getattr(telegram, "connected", False)):
            raise HTTPException(503, "Telegram is not connected")
        try:
            photos = await client.get_profile_photos("me", limit=1)
            sizes = list(getattr(photos[0], "video_sizes", None) or []) if photos else []
            if not sizes:
                _channel_profile_photo_cache["__me_video__"] = (time.monotonic(), b"", "video/mp4")
                raise HTTPException(404, "This Telegram account has no animated profile picture")
            # The small square one is enough for an avatar; fall back to the largest available.
            size = next((x for x in sizes if getattr(x, "type", "") == "u"), None) or max(sizes, key=lambda x: int(getattr(x, "size", 0) or 0))
            data = await client.download_media(photos[0], file=bytes, thumb=size)
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(502, f"Could not load the Telegram profile video: {type(exc).__name__}") from exc
        if not isinstance(data, (bytes, bytearray)) or not data:
            raise HTTPException(404, "This Telegram account has no animated profile picture")
        _channel_profile_photo_cache["__me_video__"] = (time.monotonic(), bytes(data), "video/mp4")
        return Response(content=bytes(data), media_type="video/mp4", headers={"Cache-Control": "private, max-age=900", "X-Content-Type-Options": "nosniff"})

    # ---- Local account profile (display name + photo). Never changes the real Telegram profile. ----
    _account_profile_dir = config.DATA_DIR / "account"
    _account_profile_image = _account_profile_dir / "profile-image.bin"
    _telegram_me_cache: dict[str, object] = {"at": 0.0, "name": "", "username": ""}


    # ------------------------------------------------------------------
    # SUBSECTION: Local account profile
    # Display name and photo; never changes the real Telegram profile.
    # ------------------------------------------------------------------
    async def _telegram_me_identity() -> tuple[str, str]:
        if time.monotonic() - float(_telegram_me_cache["at"]) < 600 and _telegram_me_cache["name"]:
            return str(_telegram_me_cache["name"]), str(_telegram_me_cache["username"])
        client = getattr(telegram, "client", None)
        if client is None or not TELETHON_AVAILABLE or not bool(getattr(telegram, "connected", False)):
            return "", ""
        try:
            me = await asyncio.wait_for(client.get_me(), timeout=6)
        except Exception:
            return "", ""
        name = " ".join(x for x in (str(getattr(me, "first_name", "") or ""), str(getattr(me, "last_name", "") or "")) if x).strip()
        username = str(getattr(me, "username", "") or "")
        _telegram_me_cache.update({"at": time.monotonic(), "name": name, "username": username})
        return name, username

    def _account_profile_payload(tg_name: str, tg_username: str) -> dict:
        custom = str(db.setting("account_profile_name", "") or "").strip()
        has_image = bool(db.setting("account_profile_image_mime", "")) and _account_profile_image.is_file()
        return {
            "name": custom or tg_name,
            "custom_name": custom,
            "telegram_name": tg_name,
            "username": tg_username,
            "has_custom_image": has_image,
            "image_version": str(db.setting("account_profile_image_version", "") or ""),
        }

    @router.get("/api/account/profile")
    async def get_account_profile(request: Request):
        require_auth(request)
        tg_name, tg_username = await _telegram_me_identity()
        return _account_profile_payload(tg_name, tg_username)

    @router.post("/api/account/profile")
    async def save_account_profile(request: Request):
        """Save the local display name and/or photo shown in the header and Settings."""
        require_auth(request)
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(400, "Invalid profile payload")
        name = str(body.get("name") or "").strip()
        if len(name) > 64:
            raise HTTPException(400, "Display name must be 64 characters or fewer")
        blob, mime = _parse_manual_image_payload(body)
        reset_image = bool(body.get("reset_image"))
        if blob:
            if mime == "image/svg+xml":
                raise HTTPException(400, "Upload a PNG, JPEG, WebP, or GIF image")
            try:
                from PIL import Image
                with Image.open(io.BytesIO(blob)) as img:
                    if img.width <= 0 or img.height <= 0 or img.width * img.height > 40_000_000:
                        raise ValueError("Image dimensions exceed the 40-megapixel safety limit")
                    from PIL import Image as _Img, ImageOps as _Ops
                    img = _Ops.exif_transpose(img)   # honour the camera's rotation before the EXIF data is dropped
                    img = img.convert("RGBA") if img.mode in ("P", "LA", "RGBA") else img.convert("RGB")
                    img.thumbnail((512, 512), _Img.Resampling.LANCZOS)
                    out = io.BytesIO()
                    if img.mode == "RGBA":
                        img.save(out, "PNG", optimize=True); mime = "image/png"
                    else:
                        img.save(out, "JPEG", quality=90, optimize=True); mime = "image/jpeg"
                    blob = out.getvalue()
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from exc
            except Exception as exc:
                raise HTTPException(400, "Uploaded photo is not a valid image") from exc
        db.set_setting("account_profile_name", name)
        if blob:
            _account_profile_dir.mkdir(parents=True, exist_ok=True)
            tmp = _account_profile_image.with_suffix(".tmp")
            tmp.write_bytes(blob)
            tmp.replace(_account_profile_image)
            db.set_setting("account_profile_image_mime", mime)
            db.set_setting("account_profile_image_version", str(int(time.time())))
        elif reset_image:
            try:
                _account_profile_image.unlink()
            except FileNotFoundError:
                pass
            db.set_setting("account_profile_image_mime", "")
            db.set_setting("account_profile_image_version", str(int(time.time())))
        db.log("Account profile updated locally")
        tg_name, tg_username = await _telegram_me_identity()
        return {"ok": True, **_account_profile_payload(tg_name, tg_username)}

    @router.get("/api/account/profile/image")
    def get_account_profile_image(request: Request):
        require_auth(request)
        mime = str(db.setting("account_profile_image_mime", "") or "")
        if not mime or not _account_profile_image.is_file():
            raise HTTPException(404, "No custom profile photo")
        return Response(content=_account_profile_image.read_bytes(), media_type=mime, headers={
            "Cache-Control": "private, max-age=900", "X-Content-Type-Options": "nosniff"})


    # ------------------------------------------------------------------
    # SUBSECTION: Channels
    # Channel images, sync and lists.
    # ------------------------------------------------------------------
    @router.get("/api/channels/{channel_id}/image")
    async def channel_local_image(channel_id: str, request: Request):
        """Serve a locally uploaded channel artwork image from the validated cache."""
        require_auth(request)
        def _read_image():
            with db.reading() as rdb:
                row = rdb.execute(
                    "SELECT c.image,cache.mime,cache.data FROM channels c JOIN channel_image_cache cache "
                    "ON cache.telegram_id=c.telegram_id AND cache.url=c.image WHERE c.telegram_id=?",
                    (str(channel_id),),
                ).fetchone()
                if not row:
                    return None
                return (str(row["image"] or ""), str(row["mime"] or "image/jpeg"), bytes(row["data"] or b""))
        row = await asyncio.to_thread(_read_image)
        if not row or not row[0].startswith("manual-upload:") or not row[2]:
            raise HTTPException(404, "Local channel artwork not found")
        from ..core.image_derivatives import derive, requested_width
        data, mime = await asyncio.to_thread(derive, row[2], row[1], requested_width(request.query_params.get("w")))
        return Response(content=data, media_type=mime,
                        headers={"Cache-Control": "private, max-age=3600", "X-Content-Type-Options": "nosniff"})

    # Apply a local channel edit, including an image.
    @router.post("/api/channels/{channel_id}/edit")
    async def channel_edit(channel_id: str, request: Request):
        require_auth(request)
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(400, "request body must be valid JSON")
        if not isinstance(body, dict):
            raise HTTPException(400, "request body must be an object")
        title = str(body.get("title") or "").strip()
        image = str(body.get("image_url") or "").strip()
        reset_image = bool(body.get("reset_image_to_api"))
        blob, mime = _parse_manual_image_payload(body)
        if not title:
            raise HTTPException(400, "Channel title is required")
        if len(title) > 255:
            raise HTTPException(400, "Channel title must be 255 characters or fewer")
        if image and not re.match(r"^https?://[^\s]+$", image, re.I):
            raise HTTPException(400, "Image URL must be HTTP or HTTPS")
        if blob:
            if mime == "image/svg+xml":
                raise HTTPException(400, "SVG artwork cannot be synchronized to Telegram; upload PNG, JPEG, or WebP")
            try:
                from PIL import Image
                with Image.open(io.BytesIO(blob)) as candidate:
                    image_format = str(candidate.format or "").upper()
                    width, height = candidate.size
                    if image_format not in {"JPEG", "PNG", "WEBP", "GIF"}:
                        raise ValueError("Upload a PNG, JPEG, WebP, or GIF image")
                    if width <= 0 or height <= 0 or width > 12000 or height > 12000 or width * height > 40_000_000:
                        raise ValueError("Image dimensions exceed the 40-megapixel safety limit")
                    candidate.verify()
                mime = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp", "GIF": "image/gif"}[image_format]
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from exc
            except Exception as exc:
                raise HTTPException(400, "Uploaded artwork is not a valid image") from exc
        channel_id = str(channel_id)
        now = config.now_iso()
        with db.lock:
            row = db.conn.execute("SELECT * FROM channels WHERE telegram_id=?", (channel_id,)).fetchone()
            if not row:
                raise HTTPException(404, "Channel not found")
            fields=["title=?","title_locked=1"]
            vals=[title]
            if blob:
                import hashlib
                token = "manual-upload:" + hashlib.sha256(blob).hexdigest()
                fields += ["image=?","image_source='manual'","image_locked=1"]
                vals.append(token)
                db.conn.execute(
                    "INSERT OR REPLACE INTO channel_image_cache(telegram_id,url,mime,data,updated_at) VALUES(?,?,?,?,?)",
                    (channel_id, token, mime, blob, now),
                )
            elif reset_image:
                fields += ["image=NULL","image_source=NULL","image_locked=0"]
                db.conn.execute("DELETE FROM channel_image_cache WHERE telegram_id=?", (channel_id,))
            elif image:
                fields += ["image=?","image_source='manual'","image_locked=1"]
                vals.append(image)
                db.conn.execute("DELETE FROM channel_image_cache WHERE telegram_id=?", (channel_id,))
            vals.append(channel_id)
            db.conn.execute(f"UPDATE channels SET {','.join(fields)},updated_at=? WHERE telegram_id=?", [*vals[:-1], now, vals[-1]])
            db.conn.commit()
            out=db.conn.execute("SELECT * FROM channels WHERE telegram_id=?", (channel_id,)).fetchone()
        db.log(f"Manual channel edit: {channel_id} {title}")
        return {"ok":True,"channel":dict(out),"local_photo_saved":bool(blob)}

    @router.post("/api/channels/{channel_id}/sync-to-telegram")
    async def channel_sync_to_telegram(channel_id: str, request: Request):
        """Push explicitly selected local channel title/photo after checking change-info rights.

        Photo bytes must already be in the validated local upload cache; this endpoint
        never fetches image URLs or modifies Telegram unless the user explicitly submits it.
        """
        require_auth(request)
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(400, "request body must be valid JSON")
        if not isinstance(body, dict):
            raise HTTPException(400, "request body must be an object")
        sync_title, sync_photo = body.get("sync_title", False), body.get("sync_photo", False)
        if not isinstance(sync_title, bool) or not isinstance(sync_photo, bool) or not (sync_title or sync_photo):
            raise HTTPException(400, "Select sync_title and/or sync_photo")
        client = getattr(telegram, "client", None)
        if client is None or not TELETHON_AVAILABLE:
            raise HTTPException(503, "Telegram client is not connected")
        is_connected = getattr(client, "is_connected", None)
        if callable(is_connected) and not is_connected():
            raise HTTPException(503, "Telegram client is not connected")
        with db.reading() as rdb:
            row = rdb.execute("SELECT telegram_id,title,image FROM channels WHERE telegram_id=?", (str(channel_id),)).fetchone()
            cached_image = rdb.execute(
                "SELECT mime,data FROM channel_image_cache WHERE telegram_id=? AND url=(SELECT image FROM channels WHERE telegram_id=?)",
                (str(channel_id), str(channel_id)),
            ).fetchone() if sync_photo else None
        if not row:
            raise HTTPException(404, "Channel not found")
        if sync_photo and (not str(row["image"] or "").startswith("manual-upload:") or not cached_image or not cached_image["data"]):
            raise HTTPException(400, "To sync a photo, upload a local PNG, JPEG, or WebP image in the channel editor first")
        try:
            entity = await client.get_entity(int(channel_id))
        except Exception as exc:
            raise HTTPException(502, f"Could not resolve Telegram channel: {type(exc).__name__}") from exc
        if not hasattr(entity, "broadcast") and not hasattr(entity, "megagroup"):
            raise HTTPException(400, "Remote title/photo sync is supported for Telegram channels and supergroups only")
        try:
            participant = await client.get_permissions(entity, "me")
        except Exception as exc:
            raise HTTPException(403, "Could not verify Telegram change-info permissions") from exc
        from ..core.channel_sync import has_change_info_permission
        if not has_change_info_permission(participant):
            raise HTTPException(403, "Telegram requires channel owner or administrator change-info permission")
        synced = []
        try:
            if sync_title:
                title = str(row["title"] or "").strip()
                if not title or len(title) > 255:
                    raise HTTPException(400, "A channel title between 1 and 255 characters is required")
                from telethon.tl.functions.channels import EditTitleRequest
                await client(EditTitleRequest(channel=entity, title=title))
                synced.append("title")
            if sync_photo:
                from telethon import types
                import os
                import tempfile
                from ..core.channel_sync import normalize_telegram_photo
                try:
                    image_bytes = normalize_telegram_photo(bytes(cached_image["data"]))
                except ValueError as exc:
                    raise HTTPException(400, str(exc)) from exc
                temp_path = None
                try:
                    # Close the temporary file before Telethon reopens its path.
                    # This is required for Windows, where an open NamedTemporaryFile
                    # may not be shareable with the uploader.
                    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as temp:
                        temp_path = temp.name
                        temp.write(image_bytes)
                        temp.flush()
                    uploaded = await client.upload_file(temp_path)
                    from telethon.tl.functions.channels import EditPhotoRequest
                    await client(EditPhotoRequest(channel=entity, photo=types.InputChatUploadedPhoto(file=uploaded)))
                finally:
                    if temp_path:
                        try:
                            os.unlink(temp_path)
                        except OSError:
                            pass
                synced.append("photo")
        except HTTPException:
            raise
        except _tl.FloodWaitError as exc:
            seconds = int(getattr(exc, "seconds", 60) or 60)
            try:
                governor.note_floodwait(seconds)
            except Exception:
                _swallowed_note("routes_telegram.register_telegram_routes.channel_sync_to_telegram")
                pass
            db.log(f"Telegram channel sync rate-limited for {channel_id}; wait {seconds} seconds", "WARN")
            raise HTTPException(429, f"Telegram asked Archive X to wait {seconds} seconds") from exc
        except Exception as exc:
            db.log(f"Telegram channel sync failed for {channel_id} ({','.join(synced) or 'none'}): {type(exc).__name__}: {exc}", "ERROR")
            raise HTTPException(502, f"Telegram channel sync failed after {','.join(synced) or 'no'} changes: {type(exc).__name__}") from exc
        def _work():
            with db.lock:
                db.conn.execute("UPDATE channels SET updated_at=? WHERE telegram_id=?", (config.now_iso(), str(channel_id)))
                db.conn.commit()
            _channel_profile_photo_cache.pop(str(channel_id), None)
            db.log(f"Telegram channel sync completed for {channel_id}: {', '.join(synced)}")
            return {"ok": True, "channel_id": str(channel_id), "synced": synced,
                    "warning": "Telegram was changed; local edits are separate and may be refreshed from Telegram later."}
        return await asyncio.to_thread(_work)

    @router.get("/api/source-channels")
    async def source_channels(request: Request):
        """Return the locally saved Telegram channels for operator workflows.

        Loading this catalogue is read-only. Channel discovery is an explicit
        operator action, never an implicit side effect of opening a page.
        """
        require_auth(request)
        def _read_source_channels():
            from ..core import content_mode as _cm
            with db.reading() as rdb:
                rows = [dict(r) for r in rdb.execute("SELECT * FROM channels ORDER BY title").fetchall()]
            hidden = _cm.hidden_channels(db)   # SFW: the adult channels' names are not listed
            return [r for r in rows if str(r.get("telegram_id")) not in hidden]
        return await asyncio.to_thread(_read_source_channels)

    @router.get("/api/channels")
    async def channels(request: Request):
        """Return source/grouping channels that have videos ready for Library cards.

        A known Telegram message with its validated snapshot set can be browsed
        before provider scene identification is complete.
        """
        require_auth(request)
        def _read_library_channels():
            """Read channels and their video counts from the database."""
            from ..core import content_mode as _cm
            from ..engines import local_scanner as _ls
            mode = _cm.get_mode(db)
            with db.reading() as rdb:
                # Every channel the database has a row for, whatever the mode shows: a channel hidden by the mode (adult in SFW, not in the library,
                # the other kind) is not an unknown channel, and must not come back as one from the document registry below.
                known = {str(r[0]) for r in rdb.execute("SELECT telegram_id FROM main.channels")}
                folders = {int(r[0]): str(r[1] or "") for r in rdb.execute("SELECT id,path FROM main.local_sources")}
                rows = rdb.execute(
                    "SELECT c.*, COUNT(DISTINCT cm.id) video_count "
                    "FROM channels c JOIN archive_x_library_media cm ON cm.source_channel=c.telegram_id "
                    "GROUP BY c.id ORDER BY c.title"
                ).fetchall()
                orphan = rdb.execute(
                    "SELECT cm.source_channel telegram_id, COUNT(*) video_count FROM archive_x_library_media cm "
                    "WHERE NOT EXISTS (SELECT 1 FROM channels c WHERE c.telegram_id=cm.source_channel) "
                    "AND cm.source_channel GLOB '-[0-9]*' GROUP BY cm.source_channel"
                ).fetchall()
                registry_rows = rdb.execute(
                    "SELECT l.channel_id, "
                    "SUM(CASE WHEN l.location_state='PRESENT' THEN 1 ELSE 0 END) known_count, "
                    "SUM(CASE WHEN l.location_state='PRESENT' AND r.prepared_state='PREPARED' THEN 1 ELSE 0 END) prepared_count, "
                    "SUM(CASE WHEN l.location_state='PRESENT' AND r.prepared_state<>'PREPARED' THEN 1 ELSE 0 END) pending_count, "
                    "SUM(CASE WHEN l.location_state<>'PRESENT' THEN 1 ELSE 0 END) removed_count, "
                    "s.last_scan_at,s.last_scan_status,s.high_water_mark "
                    "FROM telegram_document_locations l JOIN telegram_document_registry r ON r.registry_key=l.registry_key "
                    "LEFT JOIN telegram_channel_registry_state s ON s.channel_id=l.channel_id GROUP BY l.channel_id"
                ).fetchall()
            out = [dict(r) for r in rows]

            def orphan_entry(source: str, count: int):
                """A channel with videos but no row of its own. A local folder takes its folder's name; a folder that is gone is not listed at all
                (its files cannot be played any more); an unnamed Telegram channel is only listed where the mode shows such channels (NSFW)."""
                if source in known:
                    return None
                if _ls.is_local_channel(source):
                    path = folders.get(_ls.source_id_of(source))
                    if not path:
                        return None
                    return {"id": None, "telegram_id": source, "title": os.path.basename(path.rstrip("/\\")) or "Local folder", "username": None, "video_count": count, "source_only": True}
                if mode != "nsfw":
                    return None
                return {"id": None, "telegram_id": source, "title": "Unknown channel", "username": None, "video_count": count, "source_only": True, "unnamed": True}

            for r in orphan:
                entry = orphan_entry(str(r["telegram_id"] or ""), int(r["video_count"] or 0))
                if entry:
                    out.append(entry)
            by_id = {str(c.get("telegram_id") or ""): c for c in out}
            for row in registry_rows:
                source = str(row["channel_id"] or "")
                if not source.lstrip("-").isdigit():
                    continue  # V29.4.223: local staging is not a channel
                stat = dict(row)
                channel = by_id.get(source)
                # Retired-only locations are history, not a selectable Library
                # channel. A verified Telegram move leaves its old registry
                # location in MOVED_BY_ARCHIVE_X/MOVED_PENDING_DELETE; do not
                # resurrect an empty source channel card from those counts.
                if channel is None and int(stat.get("known_count") or 0) <= 0:
                    continue
                if channel is None:
                    channel = orphan_entry(source, 0)
                    if channel is None:
                        continue
                    out.append(channel)
                    by_id[source] = channel
                channel.update({k: stat.get(k) for k in (
                    "known_count", "prepared_count", "pending_count", "removed_count",
                    "last_scan_at", "last_scan_status", "high_water_mark")})
            # Even channels with no registry entries get stable zero values in the UI.
            for channel in out:
                channel.setdefault("known_count", int(channel.get("video_count") or 0))
                channel.setdefault("prepared_count", int(channel.get("video_count") or 0))
                channel.setdefault("pending_count", 0)
                channel.setdefault("removed_count", 0)
                channel.setdefault("last_scan_at", None)
                channel.setdefault("last_scan_status", "NEVER")
            out.sort(key=lambda c: str(c.get("title") or "").casefold())
            return out
        return await asyncio.to_thread(_read_library_channels)

    @router.post("/api/telegram/refresh")
    async def refresh_channels(request: Request):
        """POST /api/telegram/refresh: Refresh channels."""
        require_auth(request)
        if not telegram.connected:
            raise HTTPException(503, f"Telegram is not connected ({telegram.status_text})")
        try:
            items = await telegram.refresh_channels()
            # An explicit Telegram refresh may discover profile-photo changes
            # made outside Archive X, so discard those channels' in-memory avatars.
            for item in items:
                _channel_profile_photo_cache.pop(str(item.get("id") or ""), None)
        except _tl.FloodWaitError as e:
            seconds = getattr(e, "seconds", 60)
            governor.note_floodwait(seconds)
            raise HTTPException(429, f"Telegram FloodWait: wait {seconds} seconds")
        except Exception as e:
            db.log(f"Channel refresh failed: {e}", "ERROR")
            raise HTTPException(502, f"Channel refresh failed: {e}")
        from ..core import content_mode as _cm
        hidden = await asyncio.to_thread(_cm.hidden_channels, db)   # SFW: the refresh still happens, but the adult channels' names are not sent back
        shown = [i for i in items if str(i.get("id") or "") not in hidden]
        return {"count": len(shown), "channels": shown}


    # ------------------------------------------------------------------
    # SUBSECTION: Playback Snapshot Builder
    # Thumbnail index jobs.
    # ------------------------------------------------------------------
    @router.get("/api/operations/thumbnails/status")
    async def thumbnail_index_status(request: Request):
        require_auth(request)
        active = await asyncio.to_thread(db.active_jobs, "THUMBNAIL_INDEXER")
        job = active[0] if active else None
        return {"running": bool(job), "status": (job.get("status") if job else "idle"),
                "id": (job.get("id") if job else None), "completed": int((job or {}).get("completed") or 0),
                "total": int((job or {}).get("total") or 0), "current": str((job or {}).get("current_item") or ""),
                "error": str((job or {}).get("error") or "")}

    @router.post("/api/operations/thumbnails/start")
    async def thumbnail_index_start(request: Request):
        require_auth(request)
        try: body = await request.json()
        except Exception: body = {}
        channels = [int(x) for x in (body.get("channels") or []) if str(x).strip()]
        if not channels: raise HTTPException(400, "Select at least one source channel for playback snapshots")
        scope = str(body.get("scope") or "missing").strip().casefold()
        if scope not in {"missing", "all"}:
            scope = "missing"
        limit = max(0, min(5000, int(body.get("limit", 0) or 0)))
        active = db.active_jobs("THUMBNAIL_INDEXER")
        if active: return {"ok": True, "already_running": True, "id": active[0].get("id")}
        detail={"operations":["thumbnails"],"thumbnails":{"channels":channels,"scope":scope,"limit":limit}}
        jid=db.create_job("THUMBNAIL_INDEXER","","",False,detail=json.dumps(detail)); jobs.submit(jid)
        return {"ok":True,"id":jid}

    @router.post("/api/operations/thumbnails/stop")
    async def thumbnail_index_stop(request: Request):
        require_auth(request)
        active=db.active_jobs("THUMBNAIL_INDEXER")
        for job in active: jobs.cancel(job["id"])
        return {"ok":True,"stopped":len(active)}


    # ------------------------------------------------------------------
    # SUBSECTION: Content Automation configuration
    # Workflows, folders and saved config.
    # ------------------------------------------------------------------
    @router.get("/api/operations/workflows")
    def operations_workflows(request: Request):
        require_auth(request)
        return {"ok": True, "workflows": workflow_snapshot()}

    @router.get("/api/operations/automation/config")
    async def automation_config(request: Request):
        """GET /api/operations/automation/config: Automation config."""
        require_auth(request)
        return content_automation.get_config(db, masked=True)

    @router.post("/api/settings/folder-picker")
    @router.post("/api/operations/automation/pick-folder")
    async def automation_pick_folder(request: Request):
        """Open the host machine's native folder picker for any Archive X workflow."""
        require_auth(request)
        try:
            body = await request.json()
            body = body if isinstance(body, dict) else {}
        except Exception:
            body = {}
        title = str(body.get("title") or "Select Archive X folder").strip()[:120]
        initial_raw = str(body.get("initial_path") or "").strip()
        initial = Path(initial_raw).expanduser() if initial_raw else Path(config.AUTOMATION_DOWNLOAD_DIR)
        if initial.is_file():
            initial = initial.parent
        if not initial.is_dir():
            initial = Path(config.AUTOMATION_DOWNLOAD_DIR)
        if not initial.is_dir():
            initial = Path.home()
        must_exist = bool(body.get("must_exist", False))
        allow_create = bool(body.get("allow_create", not must_exist))
        # The dialog opens on the PC running Archive X. From a phone/tablet the
        # request would hang (up to 5 min) on a window nobody can see, so ask
        # remote clients to type the path instead.
        _client_host = request.client.host if request.client else ""
        if _client_host not in {"127.0.0.1", "::1", "localhost"}:
            raise HTTPException(409, "The folder picker opens on the Archive X PC. On this device, type the folder path instead.")

        def _pick_folder():
            try:
                import tkinter as tk
                from tkinter import filedialog
                root = tk.Tk()
                root.withdraw()
                try:
                    root.attributes("-topmost", True)
                    root.update()
                except Exception:
                    _swallowed_note("routes_telegram.register_telegram_routes.automation_pick_folder._pick_folder")
                    pass
                try:
                    selected = filedialog.askdirectory(title=title, mustexist=must_exist, initialdir=str(initial))
                finally:
                    try: root.destroy()
                    except Exception:
                        _swallowed_note("routes_telegram.register_telegram_routes.automation_pick_folder._pick_folder")
                        pass
                return str(selected or "").strip()
            except Exception as tk_exc:
                if sys.platform.startswith("win"):
                    try:
                        import subprocess
                        def psquote(value: str) -> str:
                            return "'" + str(value).replace("'", "''") + "'"
                        script = (
                            "Add-Type -AssemblyName System.Windows.Forms; "
                            "$d=New-Object System.Windows.Forms.FolderBrowserDialog; "
                            f"$d.Description={psquote(title)}; "
                            f"$d.ShowNewFolderButton=${str(allow_create).lower()}; "
                            f"$d.SelectedPath={psquote(str(initial))}; "
                            "if($d.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK){[Console]::Write($d.SelectedPath)}"
                        )
                        completed = subprocess.run(["powershell.exe", "-NoProfile", "-STA", "-Command", script],
                            capture_output=True, text=True, timeout=300,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                        if completed.returncode == 0:
                            return completed.stdout.strip()
                        raise RuntimeError(completed.stderr.strip() or "PowerShell folder picker failed")
                    except Exception as ps_exc:
                        raise RuntimeError(f"Native picker failed ({type(tk_exc).__name__}); fallback failed ({type(ps_exc).__name__}: {ps_exc})") from ps_exc
                raise RuntimeError(f"Native folder picker unavailable: {type(tk_exc).__name__}: {tk_exc}") from tk_exc

        try:
            selected = await asyncio.to_thread(_pick_folder)
        except RuntimeError as exc:
            # No GUI toolkit on this host (headless/Linux without tkinter): a clear
            # message the UI can show, not an unhandled 500.
            raise HTTPException(501, f"{exc}. Type the folder path instead.") from exc
        if not selected:
            return {"ok": True, "cancelled": True, "path": ""}
        path = Path(selected).expanduser()
        try:
            if allow_create:
                path.mkdir(parents=True, exist_ok=True)
            if not path.is_dir():
                raise ValueError("Selected path is not a directory")
        except Exception as exc:
            raise HTTPException(400, f"Cannot access selected folder: {exc}")
        return {"ok": True, "cancelled": False, "path": str(path.resolve())}

    _folder_preference_keys = {"transfer_source"}

    @router.get("/api/settings/folder-preference")
    def get_folder_preference(request: Request):
        require_auth(request)
        key = str(request.query_params.get("key") or "").strip()
        if key not in _folder_preference_keys:
            raise HTTPException(400, "Unsupported folder preference")
        return {"ok": True, "key": key, "path": str(db.setting(f"ax_folder_pref_{key}", "") or "")}

    @router.post("/api/settings/folder-preference")
    async def save_folder_preference(request: Request):
        require_auth(request)
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(400, "request body must be valid JSON")
        key = str((body or {}).get("key") or "").strip()
        raw_path = str((body or {}).get("path") or "").strip()
        if key not in _folder_preference_keys:
            raise HTTPException(400, "Unsupported folder preference")
        if not raw_path:
            raise HTTPException(400, "Folder path is required")
        folder = Path(raw_path).expanduser()
        if not folder.is_absolute():
            raise HTTPException(400, "Choose an absolute folder path")
        if not folder.is_dir():
            raise HTTPException(400, "The source folder does not exist or is not accessible")
        folder = folder.resolve()
        db.set_setting(f"ax_folder_pref_{key}", str(folder))
        return {"ok": True, "key": key, "path": str(folder)}

    @router.post("/api/operations/automation/config")
    async def automation_save_config(request: Request):
        """POST /api/operations/automation/config: Automation save config."""
        require_auth(request)
        data = await request.json()
        try:
            result = content_automation.save_config(db, data if isinstance(data, dict) else {})
            db.log("Content Automation settings saved")
            return {"ok": True, "config": result}
        except Exception as exc:
            db.log(f"Content Automation settings error: {type(exc).__name__}: {exc}", "ERROR")
            raise HTTPException(400, str(exc))

    return {
        "telegram_verify_code": telegram_verify_code,
        "telegram_verify_2fa": telegram_verify_2fa,
    }
