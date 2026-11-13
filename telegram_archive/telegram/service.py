"""One authenticated Telegram session, shared by every module.

Per the architecture doc's "Important reuse rule": no operating mode gets
its own Telegram downloader or login handler. video/, playback/ and jobs/
all borrow this service's .client rather than creating their own
TelegramClient.
"""

# ------------------------------------------------------------------
# FILE MAP (search for "SECTION:" to jump between sections)
#       - Setup and status
#       - Connection management
#       - Code and 2FA login
#       - QR login
#       - Credentials and sessions
#       - Channels
# ------------------------------------------------------------------
from __future__ import annotations

from ..core.swallowed import note as _swallowed_note
import asyncio
import base64
import io
import json
import time
from typing import Optional

from .. import config
from ..core.database import Database
from ..core.governor import TransferGovernor
from .client_policy import add_reconnect_listener, configure_telethon_client, make_telegram_client, FLOOD_SLEEP_THRESHOLD_SECONDS, REQUEST_RETRIES
from .capabilities import TelegramAccountProfile

try:
    from telethon import TelegramClient, functions, utils
    from telethon.errors import FloodWaitError, RPCError, SessionPasswordNeededError
    try:
        from telethon.network.connection.tcpobfuscated import ConnectionTcpObfuscated
    except Exception:
        ConnectionTcpObfuscated = None
    TELETHON_AVAILABLE = True
except ImportError:
    ConnectionTcpObfuscated = None
    TELETHON_AVAILABLE = False
    TelegramClient = None
    functions = None
    utils = None
    class _TelethonUnavailableError(Exception):
        pass
    FloodWaitError = RPCError = SessionPasswordNeededError = _TelethonUnavailableError


class TelegramService:

    # ------------------------------------------------------------------
    # SUBSECTION: Setup and status
    # Construction, performance status and limits.
    # ------------------------------------------------------------------
    """The one authenticated Telegram session, shared by every module."""
    def __init__(self, db: Database, governor: TransferGovernor):
        self.db = db
        self.governor = governor
        self.client = None
        self.connected = False
        self.session_invalidated = False
        self.status_text = "NOT CONFIGURED"
        self.login_phone = None
        self.login_phone_code_hash = None
        # QR login shares the same Telethon client and persistent session as
        # phone-code login.  Keep the QR waiter in the service so its lifetime
        # is not tied to a single browser request.
        self._qr_login = None
        self._qr_login_task: asyncio.Task | None = None
        self._qr_login_status = "IDLE"
        self._qr_login_message = ""
        self._qr_login_image = None
        self._qr_login_expires_at = 0
        self._qr_2fa_pending = False
        self.cryptg_available = False
        self.account_profile: TelegramAccountProfile | None = None
        self._last_download_config_attempt = 0.0
        self._last_download_config_log = 0.0
        self._connected_status_text = ""
        self._recovering = False
        self.reconnect_count = 0
        self.last_disconnect_at = 0.0
        add_reconnect_listener(self._on_client_reconnect)
        try:
            import cryptg  # optional native Telethon crypto accelerator
            self.cryptg_available = True
        except Exception:
            self.cryptg_available = False

    def performance_status(self) -> dict:
        return {
            "cryptg_available": bool(self.cryptg_available),
            "connected": bool(self.connected),
            "account_profile": self.account_profile.to_dict() if self.account_profile else None,
        }

    async def refresh_download_limits(self, *, force: bool = False) -> bool:
        """Refresh Telegram file-operation limits from help.getAppConfig.

        This is called by the connection warm watcher, but is throttled to a
        30-minute cadence. It makes no media requests and is safe to run before
        Play; it only updates the shared request admission governor.
        """
        now = time.monotonic()
        if self.client is None or not self.connected or functions is None:
            return False
        if not force and now - self._last_download_config_attempt < 1800.0:
            return False
        self._last_download_config_attempt = now
        try:
            request_type = getattr(getattr(functions, "help", None), "GetAppConfigRequest", None)
            if request_type is None:
                return False
            response = await self.client(request_type(hash=0))
            config_obj = getattr(response, "config", response)
            values = getattr(config_obj, "value", None)
            # Telethon JSONValue wraps scalars in JsonNumber/JsonString.value.
            while values is not None and not isinstance(values, (list, tuple)) and hasattr(values, "value"):
                values = getattr(values, "value", None)
            found = {}
            for item in values if isinstance(values, (list, tuple)) else ():
                key = str(getattr(item, "key", "") or "")
                value = getattr(item, "value", None)
                for _ in range(5):
                    if value is None or isinstance(value, (str, int, float, bool)):
                        break
                    value = getattr(value, "value", value)
                try:
                    number = int(value)
                except (TypeError, ValueError, OverflowError):
                    continue
                if key in {"small_queue_max_active_operations_count", "large_queue_max_active_operations_count"}:
                    if number > 0:
                        found[key] = number
            # These are app-config per-DC file-download caps. If Telegram
            # omits either value, use the documented conservative default.
            small = found.get("small_queue_max_active_operations_count", 3)
            large = found.get("large_queue_max_active_operations_count", 2)
            self.governor.set_telegram_download_limits(small, large, refreshed_at=now)
            self.db.log(
                f"Telegram file operation limits refreshed: small (<20 MB)={small}, large (>=20 MB)={large}; "
                f"background file limits only; foreground playback part requests remain throughput-adaptive",
                "INFO",
            )
            return True
        except Exception as exc:
            if now - self._last_download_config_log >= 1800.0:
                self._last_download_config_log = now
                try:
                    self.db.log(f"Telegram file-operation limit refresh failed; keeping conservative limits: {type(exc).__name__}: {exc}", "WARN")
                except Exception:
                    _swallowed_note("service.TelegramService.refresh_download_limits")
                    pass
            return False

    async def refresh_account_profile(self, reason: str = "connect") -> TelegramAccountProfile | None:
        """Read Telegram account capabilities once and publish the snapshot.

        The profile is ephemeral and contains no account identifier.  Playback
        and upload modules reuse it through the shared Telegram client so a
        reconnect/login immediately changes the active tuning profile.
        """
        if self.client is None or not self.connected:
            return None
        try:
            me = await self.client.get_me()
            profile = TelegramAccountProfile.from_user(me, source=f"telegram.get_me:{reason}")
            self.account_profile = profile
            try:
                setattr(self.client, "_telegram_archive_account_profile", profile)
            except Exception:
                _swallowed_note("service.TelegramService.refresh_account_profile")
                pass
            p = profile
            self.db.log(
                "Telegram account profile detected: "
                f"{p.account_label}; playback fetch={p.playback_initial_fetch}/"
                f"{p.playback_max_fetch}, transfer_sessions="
                f"{p.playback_initial_transfer_sessions}/{p.playback_max_transfer_sessions}"
            )
            return profile
        except Exception as exc:
            self.account_profile = None
            try:
                setattr(self.client, "_telegram_archive_account_profile", None)
            except Exception:
                _swallowed_note("service.TelegramService.refresh_account_profile")
                pass
            self.db.log(f"Telegram account profile detection failed: {type(exc).__name__}: {exc}", "WARN")
            return None

    def config(self):
        """API credentials. Read from the database first (entered through
        the Settings panel in the app) — config.json is only consulted as
        a one-time migration path for anyone still using it from before
        this became editable in-app."""
        api_id = self.db.setting("telegram_api_id")
        api_hash = self.db.setting("telegram_api_hash")
        phone = self.db.setting("telegram_phone")
        if api_id and api_hash:
            return {"api_id": api_id, "api_hash": api_hash, "phone": phone or ""}

        if not config.CONFIG_PATH.exists():
            return {}
        try:
            return json.loads(config.CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {}


    # ------------------------------------------------------------------
    # SUBSECTION: Connection management
    # Client building, fallback, reconnect and self-test.
    # ------------------------------------------------------------------
    def _build_client(self, session, api_id, api_hash, *, obfuscated=False):
        kwargs = {
            "governor": self.governor,
            "request_retries": REQUEST_RETRIES,
            "flood_sleep_threshold": FLOOD_SLEEP_THRESHOLD_SECONDS,
        }
        if obfuscated:
            if ConnectionTcpObfuscated is None:
                raise RuntimeError("Telethon obfuscated TCP transport is unavailable")
            kwargs["connection"] = ConnectionTcpObfuscated
        client = make_telegram_client(session, int(api_id), str(api_hash), **kwargs)
        configure_telethon_client(client)
        try:
            setattr(client, "_telegram_archive_governor", self.governor)
        except Exception:
            _swallowed_note("service.TelegramService._build_client")
            pass
        return client

    async def _connect_with_fallback(self, client, *, timeout_seconds: float = 10.0):
        """Connect using the normal MTProto transport, then obfuscated TCP on timeout.

        Some Windows networks/firewalls allow ordinary HTTPS/PyPI traffic but
        interfere with Telegram's default MTProto TCP handshake. Obfuscated TCP
        is a supported Telethon transport and is a safe fallback for the login
        path.
        """
        try:
            await asyncio.wait_for(client.connect(), timeout=max(3.0, float(timeout_seconds)))
            return "default"
        except (asyncio.TimeoutError, OSError, ConnectionError) as first_exc:
            try:
                if client.is_connected():
                    await client.disconnect()
            except Exception:
                _swallowed_note("service.TelegramService._connect_with_fallback")
                pass
            # MTProxy is itself the selected Telegram transport. The normal
            # obfuscated-TCP fallback would replace that transport and cannot
            # reuse an MTProxy tuple safely.
            if getattr(client, "_archive_x_proxy_type", None) == "mtproto":
                raise first_exc
            if ConnectionTcpObfuscated is None:
                raise first_exc
            cfg = self.config()
            fallback = self._build_client(str(config.SESSION_PATH), cfg.get("api_id"), cfg.get("api_hash"), obfuscated=True)
            try:
                await asyncio.wait_for(fallback.connect(), timeout=max(3.0, float(timeout_seconds)))
            except Exception:
                try:
                    if fallback.is_connected():
                        await fallback.disconnect()
                except Exception:
                    _swallowed_note("service.TelegramService._connect_with_fallback")
                    pass
                raise first_exc
            self.client = fallback
            return "obfuscated"

    def _on_client_reconnect(self, client, ok, reason, attempts, error) -> None:
        """Log recoveries done transparently inside a request."""
        if client is not self.client:
            return
        if ok:
            self.reconnect_count += 1
            if (not self.connected and not self.session_invalidated
                    and str(self.status_text or "").startswith("RECONNECTING")):
                self.connected = True
                self.status_text = self._connected_status_text or "CONNECTED (reconnected)"
            self.db.log(
                f"Telegram connection restored after {attempts} attempt(s) ({reason or 'connection dropped'})",
                "INFO", event="telegram.reconnected",
            )
        else:
            self.db.log(
                f"Telegram reconnect failed after {attempts} attempt(s) ({reason or 'connection dropped'}): "
                f"{type(error).__name__ + ': ' + str(error) if error else 'no response'}",
                "WARN", event="telegram.reconnect_failed",
            )

    def connection_alive(self) -> bool:
        client = self.client
        if client is None:
            return False
        try:
            return bool(client.is_connected())
        except Exception:
            return False

    async def recover_connection(self, *, budget_seconds: float = 60.0) -> bool:
        """Revive a dropped connection on the *same* client object.

        Engines, running jobs and the player keep a reference to ``self.client``;
        replacing it would leave them on a dead connection, so recovery reopens
        that same client and only reports the outcome through the status flags.
        """
        client = self.client
        if client is None or self._recovering or self.session_invalidated:
            return False
        if self.connection_alive():
            return True
        can_recover = getattr(client, "archive_x_can_recover", None)
        if callable(can_recover) and not can_recover():
            return False
        self._recovering = True
        was_connected = self.connected
        if was_connected:
            self._connected_status_text = self.status_text
            self.last_disconnect_at = time.time()
            self.db.log("Telegram connection dropped; reconnecting automatically", "WARN", event="telegram.disconnected")
        self.connected = False
        self.status_text = "RECONNECTING: Telegram connection dropped"
        try:
            reconnect = getattr(client, "archive_x_reconnect", None)
            if callable(reconnect):
                ok = await reconnect(reason="connection watchdog", budget_seconds=budget_seconds)
            else:
                await asyncio.wait_for(client.connect(), timeout=budget_seconds)
                ok = self.connection_alive()
            if ok and await asyncio.wait_for(client.is_user_authorized(), timeout=20.0):
                self.connected = True
                self.status_text = self._connected_status_text or "CONNECTED (reconnected)"
                return True
            if ok:
                self.status_text = "LOGIN REQUIRED"
                self.db.log("Telegram reconnected but the session is no longer authorized; web login required", "WARN")
            return False
        except Exception as exc:
            if self._mark_invalidated_session(exc):
                return False
            self.status_text = "RECONNECTING: Telegram connection dropped"
            self.db.log(f"Telegram reconnect attempt failed: {type(exc).__name__}: {exc}", "WARN")
            return False
        finally:
            self._recovering = False

    def _mark_invalidated_session(self, exc: Exception) -> bool:
        if type(exc).__name__ != "AuthKeyDuplicatedError":
            return False
        self.session_invalidated = True
        self.connected = False
        self.status_text = "TELEGRAM SESSION INVALIDATED — start a fresh login"
        self.db.log(
            "Telegram invalidated this authorization after simultaneous use from different IP addresses; "
            "a fresh Archive X login is required.",
            "ERROR",
        )
        return True

    async def connect(self):
        """Connect without blocking the web server for Telegram login."""
        if not TELETHON_AVAILABLE:
            self.status_text = "Telethon not installed"
            return False
        if self.session_invalidated:
            self.connected = False
            self.status_text = "TELEGRAM SESSION INVALIDATED — start a fresh login"
            return False
        cfg = self.config()
        api_id, api_hash = cfg.get("api_id"), cfg.get("api_hash")
        if not api_id or not api_hash:
            self.status_text = "CONFIG REQUIRED: api_id/api_hash"
            self.db.log("Telegram API credentials are missing from config.json", "WARN")
            return False
        self.account_profile = None
        try:
            self.client = self._build_client(str(config.SESSION_PATH), api_id, api_hash)
            transport = await self._connect_with_fallback(self.client, timeout_seconds=8.0)
            if await self.client.is_user_authorized():
                self.connected = True
                self.session_invalidated = False
                profile = await self.refresh_account_profile("saved-session")
                self.status_text = (f"CONNECTED (saved session; {profile.account_label})"
                                    if profile else "CONNECTED (saved session)")
                self.db.log(f"Telegram connected using saved session ({transport} transport)")
                return True
            self.connected = False
            self.status_text = "LOGIN REQUIRED"
            self.db.log("Telegram session is not authorized; web login required", "WARN")
            return False
        except FloodWaitError as e:
            seconds = getattr(e, "seconds", 60)
            self.governor.note_floodwait(seconds)
            self.status_text = f"FLOODWAIT {seconds}s"
            return False
        except asyncio.TimeoutError:
            self.connected = False
            self.status_text = "TELEGRAM NETWORK TIMEOUT"
            self.db.log("Telegram connection timed out on both default and obfuscated transports", "ERROR")
            return False
        except Exception as e:
            self.connected = False
            if self._mark_invalidated_session(e):
                try:
                    if self.client:
                        await self.client.disconnect()
                except Exception:
                    _swallowed_note("service.TelegramService.connect")
                    pass
                return False
            self.status_text = f"ERROR: {e}"
            self.db.log(f"Telegram connection error: {type(e).__name__}: {e}", "ERROR")
            return False

    async def self_test_connection(self, timeout_seconds: float = 12.0) -> dict:
        """Verify Telegram API connectivity without logging into an account.

        Try the normal MTProto TCP transport first and a supported obfuscated
        TCP transport as a fallback. The test uses an in-memory session and
        never changes the user's persistent Telegram login session.
        """
        if not TELETHON_AVAILABLE:
            return {"ok": False, "stage": "dependency", "detail": "Telethon is not installed."}
        cfg = self.config()
        api_id, api_hash = cfg.get("api_id"), cfg.get("api_hash")
        if not api_id or not api_hash:
            return {"ok": False, "stage": "configuration", "detail": "API ID and API Hash are required."}
        from telethon.sessions import MemorySession
        total = max(6.0, float(timeout_seconds))
        per_attempt = max(3.0, total / 2.0)
        loop = asyncio.get_running_loop()
        previous_exception_handler = loop.get_exception_handler()
        def _self_test_exception_handler(current_loop, context):
            exc = context.get("exception")
            if isinstance(exc, ConnectionResetError) and getattr(exc, "winerror", None) == 10054:
                return
            if previous_exception_handler is not None:
                previous_exception_handler(current_loop, context)
            else:
                current_loop.default_exception_handler(context)
        loop.set_exception_handler(_self_test_exception_handler)
        errors = []
        try:
            for transport_name, obfuscated in (("default", False), ("obfuscated", True)):
                client = None
                try:
                    client = self._build_client(MemorySession(), api_id, api_hash, obfuscated=obfuscated)
                    await asyncio.wait_for(client.connect(), timeout=per_attempt)
                    connected = bool(client.is_connected())
                    authorized = False
                    if connected:
                        # A TCP socket being connected is not enough to call the
                        # API reachable. Exercise a real unauthenticated MTProto
                        # RPC so the self-test and the login path measure the same
                        # control-plane connectivity.
                        self._set_auth_priority_on(client, True)
                        try:
                            await asyncio.wait_for(client(functions.help.GetConfigRequest()), timeout=min(5.0, per_attempt))
                        finally:
                            self._set_auth_priority_on(client, False)
                        try:
                            authorized = bool(await asyncio.wait_for(client.is_user_authorized(), timeout=min(5.0, per_attempt)))
                        except Exception:
                            authorized = False
                    if connected:
                        self.db.log(f"Telegram API self-test succeeded via {transport_name} transport")
                        return {
                            "ok": True,
                            "stage": "connected",
                            "transport": transport_name,
                            "connected": True,
                            "authorized": authorized,
                            "login_required": not authorized,
                            "detail": f"Telegram API RPC reachable via {transport_name} transport; login is not required for this test."
                        }
                    errors.append(f"{transport_name}: connection did not become active")
                except Exception as exc:
                    errors.append(f"{transport_name}: {type(exc).__name__}: {exc}")
                    self.db.log(f"Telegram API self-test {transport_name} transport failed: {type(exc).__name__}: {exc}", "WARN")
                finally:
                    try:
                        if client is not None and client.is_connected():
                            await client.disconnect()
                    except Exception:
                        _swallowed_note("service.TelegramService.self_test_connection")
                        pass
            detail = "Telegram API connection failed on both supported transports: " + " | ".join(errors)
            self.db.log(detail, "ERROR")
            return {"ok": False, "stage": "network", "detail": detail}
        finally:
            loop.call_soon(loop.set_exception_handler, previous_exception_handler)


    # ------------------------------------------------------------------
    # SUBSECTION: Code and 2FA login
    # Auth priority, login code and 2FA.
    # ------------------------------------------------------------------
    def _set_auth_priority(self, enabled: bool) -> None:
        """Temporarily bypass the data-plane governor for Telegram auth RPCs."""
        if self.client is None:
            return
        try:
            setattr(self.client, "_archive_x_auth_priority", bool(enabled))
        except Exception:
            _swallowed_note("service.TelegramService._set_auth_priority")
            pass

    @staticmethod
    def _set_auth_priority_on(client, enabled: bool) -> None:
        try:
            setattr(client, "_archive_x_auth_priority", bool(enabled))
        except Exception:
            _swallowed_note("service.TelegramService._set_auth_priority_on")
            pass

    async def send_login_code(self, phone: str):
        if not phone:
            raise ValueError("Phone number is required.")
        # Only one authorization method should be active on the shared client.
        if self._qr_login_task or self._qr_login:
            await self.cancel_qr_login()
        if not self.client or not self.client.is_connected():
            await self.connect()
        if not self.client or not self.client.is_connected():
            raise RuntimeError(self.status_text or "Telegram is not connected. Check the Telegram API/network test.")
        self._set_auth_priority(True)
        try:
            result = await asyncio.wait_for(self.client.send_code_request(phone), timeout=30.0)
        except asyncio.TimeoutError:
            self.status_text = "SEND CODE TIMEOUT"
            self.db.log("Telegram send-code timed out after 30 seconds", "ERROR")
            raise RuntimeError("Telegram did not respond within 30 seconds. The authentication RPC timed out; check Telegram connectivity and try again.")
        finally:
            self._set_auth_priority(False)
        self.login_phone = phone
        self.login_phone_code_hash = result.phone_code_hash
        self.status_text = "CODE SENT"
        self.db.log("Telegram login code requested")
        return {"ok": True, "status": "CODE SENT"}

    async def verify_login_code(self, phone: str, code: str):
        if not self.client:
            raise RuntimeError("Telegram client is not connected.")
        if not code:
            raise ValueError("Telegram login code is required.")
        phone = phone or getattr(self, "login_phone", "")
        phone_code_hash = getattr(self, "login_phone_code_hash", None)
        if not phone_code_hash:
            raise ValueError("No active Telegram code request. Send a new code first.")
        self._set_auth_priority(True)
        try:
            await self.client.sign_in(phone=phone, code=code, phone_code_hash=phone_code_hash)
        except SessionPasswordNeededError:
            self.status_text = "2FA REQUIRED"
            self.db.log("Telegram requested 2FA password")
            return {"ok": True, "two_factor_required": True}
        finally:
            self._set_auth_priority(False)
        if await self.client.is_user_authorized():
            self.connected = True
            profile = await self.refresh_account_profile("login")
            self.status_text = f"CONNECTED ({profile.account_label})" if profile else "CONNECTED"
            self.db.log("Telegram login successful; session saved")
            return {"ok": True, "connected": True, "two_factor_required": False}
        return {"ok": False, "connected": False}

    async def verify_2fa(self, password: str):
        if not self.client:
            raise RuntimeError("Telegram client is not connected.")
        if not password:
            raise ValueError("Telegram 2FA password is required.")
        self._set_auth_priority(True)
        try:
            await self.client.sign_in(password=password)
        finally:
            self._set_auth_priority(False)
        if await self.client.is_user_authorized():
            self.connected = True
            profile = await self.refresh_account_profile("2fa")
            self.status_text = f"CONNECTED ({profile.account_label})" if profile else "CONNECTED"
            self.db.log("Telegram 2FA authentication successful; session saved")
            if self._qr_2fa_pending or self._qr_login_status == "PASSWORD_REQUIRED":
                self._qr_2fa_pending = False
                self._qr_login_status = "AUTHORIZED"
                self._qr_login_message = "Telegram login completed successfully."
            return {"ok": True, "connected": True}
        return {"ok": False, "connected": False}


    # ------------------------------------------------------------------
    # SUBSECTION: QR login
    # QR login flow.
    # ------------------------------------------------------------------
    @staticmethod
    def _load_qrcode_modules():
        """Load and validate QR rendering support before requesting a Telegram token."""
        try:
            import qrcode
            from qrcode.image.svg import SvgPathImage
        except ImportError as exc:  # pragma: no cover - covered via API error path
            raise RuntimeError(
                "QR login needs the qrcode package. Close Archive X and relaunch with Archive-X.bat "
                "so the dependency checker can install the app requirements."
            ) from exc
        return qrcode, SvgPathImage

    @staticmethod
    def _qr_svg_data_url(url: str) -> str:
        """Create a self-contained QR SVG for the current Telegram login URL."""
        qrcode, SvgPathImage = TelegramService._load_qrcode_modules()
        qr = qrcode.QRCode(
            version=None,
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=7,
            border=4,
        )
        qr.add_data(url)
        qr.make(fit=True)
        buffer = io.BytesIO()
        qr.make_image(image_factory=SvgPathImage).save(buffer)
        return "data:image/svg+xml;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")

    async def _stop_qr_login_task(self) -> None:
        task = self._qr_login_task
        self._qr_login_task = None
        if task and task is not asyncio.current_task() and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

    async def _ensure_qr_rpc_initialized(self) -> None:
        """Verify the shared Telegram client can perform an RPC before exporting a login token.

        Telethon can report a live socket while the MTProto connection is not yet
        initialized. A harmless GetConfig RPC warms that same shared client; if
        it reports ConnectionNotInited/NotConnected, reconnect once and retry.
        """
        if not self.client or not self.client.is_connected():
            await self.connect()
        if not self.client or not self.client.is_connected():
            raise RuntimeError(self.status_text or "Telegram connection is unavailable.")

        async def probe() -> None:
            if functions is None:
                raise RuntimeError("Telethon is not installed.")
            try:
                await asyncio.wait_for(self.client(functions.help.GetConfigRequest()), timeout=12.0)
            except asyncio.CancelledError as exc:
                task = asyncio.current_task()
                if task is not None and task.cancelling():
                    raise
                self.connected = False
                self.status_text = "TELEGRAM NETWORK TIMEOUT"
                raise RuntimeError(self.status_text) from exc

        self._set_auth_priority(True)
        try:
            try:
                await probe()
                return
            except Exception as exc:
                if type(exc).__name__ not in {"ConnectionNotInitedError", "ConnectionNotConnectedError"}:
                    raise
                self.db.log(
                    "QR login found an uninitialized Telegram RPC connection; reconnecting the shared client once",
                    "WARN",
                )
                old_client = self.client
                try:
                    if old_client is not None:
                        await old_client.disconnect()
                except Exception:
                    _swallowed_note("service.TelegramService._ensure_qr_rpc_initialized")
                    pass
                self.client = None
                await self.connect()
                if not self.client or not self.client.is_connected():
                    raise RuntimeError(self.status_text or "Telegram reconnection failed.") from exc
                await probe()
        finally:
            self._set_auth_priority(False)

    async def start_qr_login(self) -> dict:
        """Start or restart QR sign-in on Archive X's shared Telegram session."""
        await self._stop_qr_login_task()
        self._qr_login = None
        self._qr_login_image = None
        self._qr_2fa_pending = False
        if self.session_invalidated:
            self._qr_login_status = "ERROR"
            self._qr_login_message = (
                "Telegram invalidated this session after it was used from two IP addresses. "
                "Archive the rejected session and start a fresh login first."
            )
            raise RuntimeError(self._qr_login_message)
        if not self.has_credentials():
            self._qr_login_status = "CONFIG_REQUIRED"
            self._qr_login_message = "Save your Telegram API ID and API Hash first."
            raise ValueError(self._qr_login_message)

        # Fail before connecting or requesting ExportLoginToken so a missing
        # renderer cannot create an unused token or mask the dependency error.
        self._load_qrcode_modules()

        try:
            await self._ensure_qr_rpc_initialized()
        except Exception as exc:
            self._qr_login_status = "ERROR"
            self._qr_login_message = f"Telegram connection is not ready for QR login: {type(exc).__name__}: {exc}"
            raise RuntimeError(self._qr_login_message) from exc

        if await self.client.is_user_authorized():
            self.connected = True
            self._qr_login_status = "AUTHORIZED"
            self._qr_login_message = "This Telegram session is already connected."
            return self.qr_login_status()

        self._set_auth_priority(True)
        try:
            self._qr_login = await self.client.qr_login()
            self._qr_login_image = self._qr_svg_data_url(self._qr_login.url)
            self._qr_login_expires_at = self._qr_expiry_timestamp(self._qr_login)
            self._qr_login_status = "WAITING_FOR_SCAN"
            self._qr_login_message = "Open Telegram on your phone and scan this code from Settings → Devices → Link Desktop Device."
            self._qr_login_task = asyncio.create_task(
                self._watch_qr_login(self._qr_login),
                name="archive-x-telegram-qr-login",
            )
            self.db.log("Telegram QR login started")
        except Exception as exc:
            self._qr_login_status = "ERROR"
            if self._mark_invalidated_session(exc):
                self._qr_login_message = (
                    "Telegram invalidated this session after it was used from two IP addresses. "
                    "Archive the rejected session and start a fresh login."
                )
                try:
                    if self.client:
                        await self.client.disconnect()
                except Exception:
                    _swallowed_note("service.TelegramService.start_qr_login")
                    pass
            else:
                self._qr_login_message = f"Could not start QR login: {type(exc).__name__}: {exc}"
                self.db.log(f"Telegram QR login start failed: {type(exc).__name__}: {exc}", "ERROR")
            raise RuntimeError(self._qr_login_message) from exc
        finally:
            self._set_auth_priority(False)
        return self.qr_login_status()

    @staticmethod
    def _qr_expiry_timestamp(qr_login) -> int:
        try:
            return int(qr_login.expires.timestamp())
        except Exception:
            return int(time.time()) + 30

    async def _watch_qr_login(self, qr_login) -> None:
        """Wait for authorization and rotate expired tokens automatically."""
        task = asyncio.current_task()
        try:
            while self._qr_login is qr_login:
                self._set_auth_priority(True)
                try:
                    await qr_login.wait()
                except SessionPasswordNeededError:
                    if self._qr_login is qr_login:
                        self._qr_2fa_pending = True
                        self._qr_login_status = "PASSWORD_REQUIRED"
                        self._qr_login_message = "QR accepted. Enter your Telegram Cloud Password (Two-Step Verification) to finish signing in."
                        self.status_text = "2FA REQUIRED"
                        self.db.log("Telegram QR login accepted; cloud password required")
                    return
                except asyncio.TimeoutError:
                    if self._qr_login is not qr_login:
                        return
                    # Telethon reports an expired QR wait as a timeout. Recreate
                    # the token in place so the browser always sees a fresh QR.
                    await qr_login.recreate()
                    self._qr_login_image = self._qr_svg_data_url(qr_login.url)
                    self._qr_login_expires_at = self._qr_expiry_timestamp(qr_login)
                    self._qr_login_status = "WAITING_FOR_SCAN"
                    self._qr_login_message = "QR code refreshed automatically. Scan the new code with Telegram."
                    continue
                finally:
                    self._set_auth_priority(False)

                if self._qr_login is not qr_login:
                    return
                if self.client and await self.client.is_user_authorized():
                    self.connected = True
                    profile = await self.refresh_account_profile("qr-login")
                    self.status_text = f"CONNECTED ({profile.account_label})" if profile else "CONNECTED"
                    self._qr_2fa_pending = False
                    self._qr_login_status = "AUTHORIZED"
                    self._qr_login_message = "Telegram login completed successfully."
                    self.db.log("Telegram QR login successful; session saved")
                else:
                    self._qr_login_status = "ERROR"
                    self._qr_login_message = "Telegram did not authorize this session. Start QR login again."
                return
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if self._qr_login is qr_login:
                self._qr_login_status = "ERROR"
                self._qr_login_message = f"QR login failed: {type(exc).__name__}: {exc}"
                self.db.log(f"Telegram QR login failed: {type(exc).__name__}: {exc}", "ERROR")
        finally:
            if self._qr_login_task is task:
                self._qr_login_task = None

    def qr_login_status(self) -> dict:
        return {
            "ok": True,
            "status": self._qr_login_status,
            "message": self._qr_login_message,
            "qr_image": self._qr_login_image if self._qr_login_status == "WAITING_FOR_SCAN" else None,
            "expires_at": self._qr_login_expires_at if self._qr_login_status == "WAITING_FOR_SCAN" else None,
            "connected": bool(self.connected),
            "two_factor_required": self._qr_login_status == "PASSWORD_REQUIRED",
        }

    async def cancel_qr_login(self) -> dict:
        await self._stop_qr_login_task()
        self._qr_login = None
        self._qr_login_image = None
        self._qr_login_expires_at = 0
        self._qr_2fa_pending = False
        self._qr_login_status = "CANCELLED"
        self._qr_login_message = "QR login was cancelled."
        return self.qr_login_status()


    # ------------------------------------------------------------------
    # SUBSECTION: Credentials and sessions
    # Credentials, session reset and logout.
    # ------------------------------------------------------------------
    async def set_credentials(self, api_id: str, api_hash: str, phone: str) -> dict:
        """Save API credentials from the Settings panel and (re)connect
        immediately, so entering them in the app is a one-step action —
        no config.json editing, no restart. An empty api_hash keeps the
        previously saved one (the UI never re-displays a saved hash, so
        leaving the field blank on an update has to mean "unchanged")."""
        api_id = str(api_id).strip()
        api_hash = str(api_hash).strip()
        phone = str(phone).strip()
        if not api_hash:
            api_hash = str(self.db.setting("telegram_api_hash") or "")
        if not api_id or not api_hash:
            raise ValueError("API ID and API Hash are required.")

        await self.cancel_qr_login()

        self.db.set_setting("telegram_api_id", api_id)
        self.db.set_setting("telegram_api_hash", api_hash)
        self.db.set_setting("telegram_phone", phone)
        self.db.log("Telegram API credentials updated from the Settings panel")

        if self.client:
            try:
                await self.client.disconnect()
            except Exception:
                _swallowed_note("service.TelegramService.set_credentials")
                pass
            self.client = None
            self.connected = False
            self.account_profile = None

        await self.connect()
        return {"connected": self.connected, "status": self.status_text}

    def has_credentials(self) -> bool:
        cfg = self.config()
        return bool(cfg.get("api_id") and cfg.get("api_hash"))

    async def reset_invalid_session(self) -> dict:
        """Archive a Telegram-rejected session so the user can authorize a new one."""
        await self.cancel_qr_login()
        client = self.client
        if client is not None:
            try:
                await client.disconnect()
            except Exception:
                _swallowed_note("service.TelegramService.reset_invalid_session")
                pass
            try:
                setattr(client, "_telegram_archive_account_profile", None)
            except Exception:
                _swallowed_note("service.TelegramService.reset_invalid_session")
                pass

        self.client = None
        self.connected = False
        self.account_profile = None
        self.login_phone = None
        self.login_phone_code_hash = None

        session_path = config.SESSION_PATH
        archived_path = None
        try:
            if session_path.exists():
                archived_path = session_path.with_name(
                    f"{session_path.name}.replaced-{time.time_ns()}.bak"
                )
                session_path.replace(archived_path)
        except OSError as exc:
            self.status_text = "TELEGRAM SESSION INVALIDATED — session archive failed"
            self.db.log(
                f"Could not archive invalid Telegram session: {type(exc).__name__}",
                "ERROR",
            )
            raise RuntimeError(
                "Archive X could not preserve the invalid Telegram session. No new session was created."
            ) from exc

        self.session_invalidated = False
        self._qr_login = None
        self._qr_login_task = None
        self._qr_login_status = "IDLE"
        self._qr_login_message = "The invalid session was archived. Sign in to Telegram again."
        self._qr_login_image = None
        self._qr_login_expires_at = 0
        self._qr_2fa_pending = False
        self.status_text = "LOGIN REQUIRED" if self.has_credentials() else "CONFIG REQUIRED: api_id/api_hash"
        self.db.log("Invalid Telegram session archived locally; a new sign-in is required", "WARN")
        return {
            "ok": True,
            "connected": False,
            "status": self.status_text,
            "session_archived": archived_path.name if archived_path else None,
            "credentials_retained": self.has_credentials(),
        }

    async def logout_account(self) -> dict:
        """Revoke this Telegram authorization and return Archive X to sign-in state.

        API ID/API Hash settings are deliberately retained. Unlike disconnect(),
        this method uses Telegram's auth.LogOut RPC when the current session is
        authorized, so a later reconnect cannot silently reuse the same login.
        """
        await self.cancel_qr_login()
        self.login_phone = None
        self.login_phone_code_hash = None

        # Reconnect the saved session if the service currently has no live client;
        # a remote logout must not be reported successful if Telegram cannot be
        # reached to revoke an authorized session.
        if self.client is None or not self.client.is_connected():
            stale_client = self.client
            if stale_client is not None:
                try:
                    if stale_client.is_connected():
                        await stale_client.disconnect()
                except Exception:
                    _swallowed_note("service.TelegramService.logout_account")
                    pass
            self.client = None
            if not self.has_credentials():
                self.status_text = "CONFIG REQUIRED: api_id/api_hash"
                raise RuntimeError("Save Telegram API ID and API Hash before logging out of the Telegram account.")
            await self.connect()

        client = self.client
        if client is None or not client.is_connected():
            raise RuntimeError(self.status_text or "Telegram is unavailable; reconnect before logging out.")

        try:
            authorized = bool(await asyncio.wait_for(client.is_user_authorized(), timeout=10.0))
        except Exception as exc:
            raise RuntimeError(f"Could not verify the Telegram session before logout: {type(exc).__name__}.") from exc

        was_authorized = authorized
        if authorized:
            self._set_auth_priority(True)
            try:
                logged_out = await asyncio.wait_for(client.log_out(), timeout=20.0)
                if logged_out is False:
                    raise RuntimeError("Telegram did not confirm that this session was logged out. Please retry when connected.")
            except Exception as exc:
                self.db.log(f"Telegram account logout failed: {type(exc).__name__}: {exc}", "ERROR")
                raise RuntimeError(f"Telegram could not revoke this session: {type(exc).__name__}. Check the connection and retry.") from exc
            finally:
                self._set_auth_priority(False)
        else:
            # This client is connected but already unauthorised; close it and
            # expose the sign-in UI without touching the saved API credentials.
            try:
                await client.disconnect()
            except Exception:
                _swallowed_note("service.TelegramService.logout_account")
                pass

        try:
            if client.is_connected():
                await client.disconnect()
        except Exception:
            _swallowed_note("service.TelegramService.logout_account")
            pass

        self.client = None
        self.connected = False
        self.account_profile = None
        try:
            setattr(client, "_telegram_archive_account_profile", None)
        except Exception:
            _swallowed_note("service.TelegramService.logout_account")
            pass
        self.status_text = "LOGIN REQUIRED" if self.has_credentials() else "CONFIG REQUIRED: api_id/api_hash"
        self.db.log("Telegram account logged out; API credentials retained")
        return {
            "ok": True,
            "connected": False,
            "logged_out": was_authorized,
            "status": self.status_text,
            "credentials_retained": self.has_credentials(),
        }

    async def disconnect(self):
        await self.cancel_qr_login()
        if self.client:
            try:
                setattr(self.client, "_telegram_archive_account_profile", None)
            except Exception:
                _swallowed_note("service.TelegramService.disconnect")
                pass
            await self.client.disconnect()
        self.connected = False
        self.account_profile = None
        self.status_text = "DISCONNECTED"


    # ------------------------------------------------------------------
    # SUBSECTION: Channels
    # Dialogs and channel refresh.
    # ------------------------------------------------------------------
    async def dialogs(self):
        if not self.client or not self.connected:
            return []
        result = []
        async for d in self.client.iter_dialogs():
            if d.is_channel:
                result.append({
                    "id": str(d.id),
                    "title": d.title,
                    "username": getattr(d.entity, "username", None),
                })
        return result

    async def refresh_channels(self):
        items = await self.dialogs()
        for item in items:
            with self.db.lock:
                self.db.conn.execute(
                    """INSERT INTO channels(telegram_id,title,username,role,updated_at)
                       VALUES(?,?,?,?,?)
                       ON CONFLICT(telegram_id) DO UPDATE SET
                       title=CASE WHEN COALESCE(channels.title_locked,0)=1 THEN channels.title ELSE excluded.title END,
                       username=excluded.username,
                       updated_at=excluded.updated_at""",
                    (item["id"], item["title"] or "Unknown Channel", item["username"], "available", config.now_iso()),
                )
                self.db.conn.commit()
        return items
