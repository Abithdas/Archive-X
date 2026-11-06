"""V5.5 user-experience, history, markers, favorites and provider dashboard routes."""

# ------------------------------------------------------------------
# FILE MAP (search for "SECTION:" to jump between sections)
#   - Module helpers
#   - Route registration
#       - Play history and markers
#       - Recommendations and favorites
#       - Experience and storage settings
#       - Provider dashboard
# ------------------------------------------------------------------
from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

from fastapi import HTTPException, Request

from .. import config



# ==============================================================================
# SECTION: Module helpers
# Time, entity serialization and safe UI values.
# ==============================================================================

def _iso_now() -> str:
    return config.now_iso()


def _entity_table(kind: str) -> str:
    k = str(kind or '').strip().casefold()
    if k == 'performer':
        return 'performers'
    if k == 'studio':
        return 'studios'
    raise HTTPException(400, 'entity type must be performer or studio')


def _media_row(db, media_id: int):
    with db.lock:
        row = db.conn.execute(
            "SELECT m.*, GROUP_CONCAT(DISTINCT p.name) performers, "
            "GROUP_CONCAT(DISTINCT s.name) studios, GROUP_CONCAT(DISTINCT t.name) tags "
            "FROM archive_x_published_media m "
            "LEFT JOIN video_performers vp ON vp.video_id=m.id LEFT JOIN performers p ON p.id=vp.performer_id "
            "LEFT JOIN video_studios vs ON vs.video_id=m.id LEFT JOIN studios s ON s.id=vs.studio_id "
            "LEFT JOIN video_tags vt ON vt.video_id=m.id LEFT JOIN tags t ON t.id=vt.tag_id "
            "WHERE m.id=? GROUP BY m.id", (int(media_id,))
        ).fetchone()
    if not row:
        return None
    d = dict(row)
    d['performers'] = [x for x in (d.get('performers') or '').split(',') if x]
    d['studios'] = [x for x in (d.get('studios') or '').split(',') if x]
    d['tags'] = [x for x in (d.get('tags') or '').split(',') if x]
    d['studio'] = d['studios'][0] if d['studios'] else ''
    d['id'] = int(d.get('source_message_id') or 0)
    d['_cid'] = d.get('source_channel')
    d['name'] = d.get('title') or d.get('filename') or f"video {d['id']}"
    d['thumbnail_id'] = d.get('thumbnail_message_id') or d.get('source_message_id')
    return d


def _entity_favorited(db, entity_type: str, entity_id: int) -> bool:
    with db.lock:
        row = db.conn.execute(
            "SELECT 1 FROM entity_favorites WHERE entity_type=? AND entity_id=?",
            (entity_type, int(entity_id)),
        ).fetchone()
    return bool(row)


def _serialize_entity(db, entity_type: str, entity_id: int):
    table = _entity_table(entity_type)
    with db.lock:
        row = db.conn.execute(f"SELECT * FROM {table} WHERE id=?", (int(entity_id),)).fetchone()
    if not row:
        return None
    out = dict(row)
    out['favorite'] = _entity_favorited(db, entity_type, entity_id)
    return out


_AX_THEME_PRESETS = {'nord', 'nero-gold', 'black-essence', 'pastel-lavender', 'pastel-mint', 'minimal-slate', 'minimal-sand', 'custom'}
_AX_THEME_ALIASES = {'nobuf-dark': 'nobuf', 'nobuf-light': 'nobuf', 'solarized-light': 'solarized'}


def _safe_theme_preset(value, legacy_mode='system'):
    value = str(value or '').strip().lower()
    value = _AX_THEME_ALIASES.get(value, value)
    if value in _AX_THEME_PRESETS:
        return value
    # Migrate pre-preset installs without changing the user's broad light/dark choice.
    # Light/dark are now paired variants of the same family rather than separate themes.
    return 'nord'


def _safe_grid_density(value):
    value = str(value or 'default').strip().lower()
    return value if value in {'compact', 'default', 'spacious'} else 'default'


def _safe_ui_scale(value):
    """Return a supported whole-application scale percentage."""
    try:
        scale = int(value)
    except (TypeError, ValueError):
        return 100
    return max(70, min(130, scale)) if 70 <= scale <= 130 else 100




# ==============================================================================
# SECTION: Route registration
# Experience routes, grouped below.
# ==============================================================================

def register(router, *, db, require_auth, resources=None, playback_state=None, playback_engine=None):
    """Register history, marker, favorite, settings and provider routes."""


    # ------------------------------------------------------------------
    # SUBSECTION: Play history and markers
    # Progress, history and markers.
    # ------------------------------------------------------------------
    @router.post('/api/play/activity')
    async def play_activity(request: Request):
        require_auth(request)
        data = await request.json()
        playing = bool(data.get('playing'))
        if resources is not None:
            if playing:
                await resources.interactive_playback_enter()
            else:
                await resources.interactive_playback_exit()
        if not playing:
            # A paused/closed browser player must not leave the last video
            # projected as an active runtime job after its final HTTP range ends.
            try:
                eng = playback_engine
                if eng is None:
                    ps = playback_state or {}
                    eng = ps.get('engine')
                if eng is not None:
                    eng.clear_current_video_if_idle(reason='browser_activity_false')
            except Exception:
                pass
        return {'ok': True, 'playing': playing}

    @router.post('/api/play/progress')
    async def save_play_progress(request: Request):
        require_auth(request)
        data = await request.json()
        try:
            channel_id = str(data['channel_id'])
            message_id = int(data['message_id'])
            position = max(0.0, float(data.get('position') or 0))
            duration = max(0.0, float(data.get('duration') or 0))
            playing_delta = max(0.0, float(data.get('watch_delta') or 0))
        except Exception:
            raise HTTPException(400, 'channel_id, message_id, position and duration are required')
        with db.lock:
            row = db.conn.execute('SELECT id FROM media WHERE source_channel=? AND source_message_id=?', (channel_id, message_id)).fetchone()
            if not row:
                return {'ok': False, 'stored': False}
            media_id = int(row['id'])
            now = _iso_now()
            existing = db.conn.execute('SELECT play_count,total_watch_seconds FROM watch_history WHERE media_id=?', (media_id,)).fetchone()
            play_count = int(existing['play_count'] or 0) if existing else 0
            total = float(existing['total_watch_seconds'] or 0) if existing else 0.0
            # A small delta is supplied every few seconds; large jumps are ignored to avoid inflating watch time during seeks.
            total += playing_delta if playing_delta <= 20 else 0
            completed = int(bool(duration and position >= max(0.0, duration - 10)))
            if existing is None:
                play_count = 1
            elif position < 5 and playing_delta > 0:
                play_count += 1
            db.conn.execute(
                'INSERT INTO watch_history(media_id,position,duration,play_count,total_watch_seconds,last_played_at,completed,updated_at) '
                'VALUES(?,?,?,?,?,?,?,?) '
                'ON CONFLICT(media_id) DO UPDATE SET position=excluded.position,duration=excluded.duration,play_count=excluded.play_count,total_watch_seconds=excluded.total_watch_seconds,last_played_at=excluded.last_played_at,completed=excluded.completed,updated_at=excluded.updated_at',
                (media_id, position, duration, play_count, total, now, completed, now)
            )
            db.conn.commit()
        return {'ok': True, 'stored': True, 'completed': bool(completed)}

    @router.get('/api/play/history/{channel_id}/{message_id}')
    async def get_play_history(channel_id: int, message_id: int, request: Request):
        require_auth(request)
        def _read_history():
            with db.reading() as rdb:
                row = rdb.execute(
                    'SELECT wh.* FROM watch_history wh JOIN media m ON m.id=wh.media_id WHERE m.source_channel=? AND m.source_message_id=?',
                    (str(channel_id), int(message_id))
                ).fetchone()
                return dict(row) if row else None
        row = await asyncio.to_thread(_read_history)
        return row if row else {'position': 0, 'duration': 0, 'play_count': 0, 'total_watch_seconds': 0, 'completed': False}

    @router.get('/api/media/{media_id}/markers')
    async def get_markers(media_id: int, request: Request):
        """Return saved markers only; provider refresh is an explicit POST action."""
        require_auth(request)
        def _read_markers():
            with db.reading() as rdb:
                return [dict(r) for r in rdb.execute(
                    'SELECT * FROM scene_markers WHERE media_id=? ORDER BY position,id', (int(media_id),)
                ).fetchall()]
        return await asyncio.to_thread(_read_markers)

    @router.post('/api/media/{media_id}/markers/sync')
    async def sync_markers(media_id: int, request: Request):
        """Load provider markers lazily; only an explicit refresh forces a provider requery."""
        require_auth(request)
        from ..metadata.providers import sync_media_markers
        try:
            try:
                body = await request.json()
            except Exception:
                body = {}
            force = bool(body.get('force', False)) if isinstance(body, dict) else False
            result = await sync_media_markers(db, int(media_id), force=force)
            return result
        except Exception as exc:
            db.log(f'Marker provider sync failed for media {int(media_id)}: {exc}', 'WARN', event='metadata.scene_markers')
            raise HTTPException(502, 'Provider marker sync failed') from exc

    @router.post('/api/media/{media_id}/markers')
    async def create_marker(media_id: int, request: Request):
        require_auth(request)
        data = await request.json()
        try:
            position = max(0.0, float(data.get('position') or 0))
        except Exception:
            raise HTTPException(400, 'position is required')
        title = str(data.get('title') or '').strip()[:160]
        note = str(data.get('note') or '').strip()[:500]
        tag = str(data.get('tag') or '').strip()[:80]
        with db.lock:
            if not db.conn.execute('SELECT 1 FROM media WHERE id=?', (int(media_id),)).fetchone():
                raise HTTPException(404, 'Media not found')
            now = _iso_now()
            cur = db.conn.execute('INSERT INTO scene_markers(media_id,position,title,note,tag,created_at,updated_at) VALUES(?,?,?,?,?,?,?)', (int(media_id),position,title,note,tag,now,now))
            db.conn.commit()
            marker_id = int(cur.lastrowid)
        return {'ok': True, 'marker': {'id': marker_id, 'media_id': int(media_id), 'position': position, 'title': title, 'note': note, 'tag': tag, 'created_at': now, 'updated_at': now}}

    @router.delete('/api/media/{media_id}/markers/{marker_id}')
    async def delete_marker(media_id: int, marker_id: int, request: Request):
        require_auth(request)
        with db.lock:
            marker = db.conn.execute('SELECT source FROM scene_markers WHERE id=? AND media_id=?', (int(marker_id), int(media_id))).fetchone()
            db.conn.execute('DELETE FROM scene_markers WHERE id=? AND media_id=?', (int(marker_id), int(media_id)))
            # Keep deliberate provider-marker deletions from being undone by the
            # automatic empty-list repair. The user can still choose Refresh.
            if marker and str(marker['source'] or 'local').casefold() == 'provider':
                now = _iso_now()
                db.conn.execute(
                    "INSERT INTO provider_sync_state(provider,entity_type,last_sync_at,status,error,request_count,updated_at) VALUES(?,?,?,?,?,?,?) "
                    "ON CONFLICT(provider,entity_type) DO UPDATE SET status=excluded.status,error=excluded.error,updated_at=excluded.updated_at",
                    ('theporndb', f'scene_markers:{int(media_id)}', now, 'USER_MODIFIED', 'Provider markers were manually removed; use explicit refresh to re-import them.', 0, now),
                )
            db.conn.commit()
        return {'ok': True}


    # ------------------------------------------------------------------
    # SUBSECTION: Recommendations and favorites
    # Recommendations and entity favorites.
    # ------------------------------------------------------------------
    # Return recommended videos for one media item.
    @router.get('/api/media/{media_id}/recommendations')
    async def media_recommendations(media_id: int, request: Request):
        require_auth(request)
        def _read_recommendations():
            with db.reading() as rdb:
                row = rdb.execute('SELECT * FROM media WHERE id=?', (int(media_id),)).fetchone()
                if not row:
                    return None, [], [], [], []
                performer_ids = [int(r['performer_id']) for r in rdb.execute('SELECT performer_id FROM video_performers WHERE video_id=?', (media_id,)).fetchall()]
                studio_ids = [int(r['studio_id']) for r in rdb.execute('SELECT studio_id FROM video_studios WHERE video_id=?', (media_id,)).fetchall()]
                tag_ids = [int(r['tag_id']) for r in rdb.execute('SELECT tag_id FROM video_tags WHERE video_id=?', (media_id,)).fetchall()]
                clauses, params = [], []
                if performer_ids:
                    ph = ','.join('?' for _ in performer_ids)
                    clauses.append(f"m.id IN (SELECT video_id FROM video_performers WHERE performer_id IN ({ph}))"); params += performer_ids
                if studio_ids:
                    ph = ','.join('?' for _ in studio_ids)
                    clauses.append(f"m.id IN (SELECT video_id FROM video_studios WHERE studio_id IN ({ph}))"); params += studio_ids
                if tag_ids:
                    ph = ','.join('?' for _ in tag_ids)
                    clauses.append(f"m.id IN (SELECT video_id FROM video_tags WHERE tag_id IN ({ph}))"); params += tag_ids
                if not clauses:
                    return dict(row), performer_ids, studio_ids, tag_ids, []
                where = ' OR '.join(f'({c})' for c in clauses)
                rows = rdb.execute(
                    f"SELECT m.*, GROUP_CONCAT(DISTINCT p.name) performers, GROUP_CONCAT(DISTINCT s.name) studios, GROUP_CONCAT(DISTINCT t.name) tags "
                    f"FROM archive_x_published_media m LEFT JOIN video_performers vp ON vp.video_id=m.id LEFT JOIN performers p ON p.id=vp.performer_id "
                    f"LEFT JOIN video_studios vs ON vs.video_id=m.id LEFT JOIN studios s ON s.id=vs.studio_id "
                    f"LEFT JOIN video_tags vt ON vt.video_id=m.id LEFT JOIN tags t ON t.id=vt.tag_id "
                    f"WHERE m.id<>? AND ({where}) GROUP BY m.id ORDER BY COALESCE(m.upload_date,m.updated_at) DESC LIMIT 18",
                    [int(media_id)] + params,
                ).fetchall()
                return dict(row), performer_ids, studio_ids, tag_ids, [dict(r) for r in rows]
        saved, performer_ids, studio_ids, tag_ids, rows = await asyncio.to_thread(_read_recommendations)
        if saved is None:
            raise HTTPException(404, 'Media not found')
        if not performer_ids and not studio_ids and not tag_ids:
            return {'items': []}
        out = []
        for d in rows:
            d['performers']=[x for x in (d.get('performers') or '').split(',') if x]; d['studios']=[x for x in (d.get('studios') or '').split(',') if x]; d['tags']=[x for x in (d.get('tags') or '').split(',') if x]; d['studio']=d['studios'][0] if d['studios'] else ''; d['id']=int(d['source_message_id']); d['_cid']=d['source_channel']; d['name']=d.get('title') or d.get('filename') or f"video {d['id']}"; d['thumbnail_id']=d.get('thumbnail_message_id') or d['source_message_id']; out.append(d)
        return {'items': out}

    @router.post('/api/entities/{kind}/{entity_id}/favorite')
    async def toggle_entity_favorite(kind: str, entity_id: int, request: Request):
        require_auth(request)
        _entity_table(kind)
        with db.lock:
            exists = db.conn.execute('SELECT 1 FROM entity_favorites WHERE entity_type=? AND entity_id=?', (kind, int(entity_id))).fetchone()
            if exists:
                db.conn.execute('DELETE FROM entity_favorites WHERE entity_type=? AND entity_id=?', (kind, int(entity_id)))
                fav = False
            else:
                if not db.conn.execute(f'SELECT 1 FROM {_entity_table(kind)} WHERE id=?', (int(entity_id),)).fetchone():
                    raise HTTPException(404, 'Entity not found')
                db.conn.execute('INSERT INTO entity_favorites(entity_type,entity_id,created_at) VALUES(?,?,?)', (kind, int(entity_id), _iso_now()))
                fav = True
            db.conn.commit()
        return {'ok': True, 'favorite': fav, 'entity_type': kind, 'entity_id': int(entity_id)}

    @router.get('/api/entities/{kind}/{entity_id}/favorite')
    async def entity_favorite_status(kind: str, entity_id: int, request: Request):
        require_auth(request)
        _entity_table(kind)
        return {'favorite': _entity_favorited(db, kind, entity_id)}


    # ------------------------------------------------------------------
    # SUBSECTION: Experience and storage settings
    # UI settings and storage moves.
    # ------------------------------------------------------------------
    @router.get('/api/settings/experience')
    async def experience_settings(request: Request):
        require_auth(request)
        keys = {
            'autoplay': bool(db.setting('ta_autoplay', '1') not in {'0', 'false', 'False'}),
            'autonext': bool(db.setting('ta_autonext', '1') not in {'0', 'false', 'False'}),
            'autonext_mode': db.setting('ta_autonext_mode', 'context'),
            'theme_mode': ('system' if db.setting('ax_theme_mode', 'system') == 'auto-time' else db.setting('ax_theme_mode', 'system')),
            'theme_preset': _safe_theme_preset(db.setting('ax_theme_preset', ''), db.setting('ax_theme_mode', 'system')),
            'grid_density': _safe_grid_density(db.setting('ax_grid_density', 'default')),
            'ui_scale': _safe_ui_scale(db.setting('ax_ui_scale', '100')),
            'live_seek_preview': bool(db.setting('ta_live_seek_preview', '1') not in {'0', 'false', 'False'}),
            'resume_prompt': bool(db.setting('ta_resume_prompt', '1') not in {'0', 'false', 'False'}),
            'partial_cache_prompt': bool(db.setting('ta_partial_cache_prompt', '0') not in {'0', 'false', 'False'}),

        }
        return keys

    # Validate and save allowed UI experience settings.
    @router.post('/api/settings/experience')
    async def save_experience_settings(request: Request):
        require_auth(request)
        data = await request.json()
        allowed = {'autoplay','autonext','autonext_mode','theme_mode','theme_preset','grid_density','ui_scale','live_seek_preview','resume_prompt','partial_cache_prompt'}
        for k in allowed:
            if k not in data:
                continue
            v = data[k]
            if k in {'autoplay','autonext','live_seek_preview','resume_prompt','partial_cache_prompt'}:
                v = '1' if bool(v) else '0'
                db.set_setting(f'ta_{k}', v)
            elif k == 'theme_mode':
                v = str(v).strip().lower()
                if v == 'auto-time': v = 'system'
                if v not in {'dark','light','system'}: raise HTTPException(400, 'Invalid theme mode')
                db.set_setting('ax_theme_mode', v)
            elif k == 'theme_preset':
                v = str(v).strip().lower()
                v = _AX_THEME_ALIASES.get(v, v)
                if v not in _AX_THEME_PRESETS:
                    raise HTTPException(400, 'Invalid theme preset')
                db.set_setting('ax_theme_preset', v)
                # Theme family and light/dark mode are independent preferences.
                # A one-field preset update must not silently change the selected mode.
            elif k == 'grid_density':
                v = str(v).strip().lower()
                if v not in {'compact','default','spacious'}:
                    raise HTTPException(400, 'Invalid grid density')
                db.set_setting('ax_grid_density', v)
            elif k == 'ui_scale':
                try:
                    v = int(v)
                except (TypeError, ValueError):
                    raise HTTPException(400, 'Invalid application zoom level')
                if not 70 <= v <= 130:
                    raise HTTPException(400, 'Invalid application zoom level (70–130%)')
                db.set_setting('ax_ui_scale', str(v))
            else:
                v = str(v)
                if v not in {'context','collection','performer','studio','recommended'}:
                    raise HTTPException(400, 'Invalid autoplay-next mode')
                db.set_setting(f'ta_{k}', v)
        return await experience_settings(request)

    @router.get('/api/settings/storage')
    async def storage_settings(request: Request):
        require_auth(request)
        state = config.get_data_location_state()
        root = Path(state['active_path'])
        try:
            usage = shutil.disk_usage(root)
            disk = {'total': int(usage.total), 'free': int(usage.free), 'used': int(usage.used)}
        except Exception:
            disk = {'total': 0, 'free': 0, 'used': 0}
        def _size():
            count = 0
            total = 0
            try:
                for p in root.rglob('*'):
                    try:
                        if p.is_file():
                            count += 1
                            total += p.stat().st_size
                    except OSError:
                        pass
            except OSError:
                pass
            return count, total
        files, bytes_used = await asyncio.to_thread(_size)
        active_jobs = db.active_jobs()
        blocking_jobs = [j for j in active_jobs if str(j.get('status') or '').upper() in {'QUEUED','RUNNING','PAUSING','CANCEL_REQUESTED'}]
        return {
            'ok': True,
            'current_path': state['active_path'],
            'pending_path': state.get('pending_path') or '',
            'restart_required': bool(state.get('pending_path')),
            'environment_override': bool(state.get('environment_override')),
            'files': files,
            'bytes': bytes_used,
            'disk': disk,
            'active_jobs': len(active_jobs),
            'blocking_jobs': len(blocking_jobs),
            'active_job_types': sorted({str(j.get('job_type') or '') for j in active_jobs}),
        }

    @router.post('/api/settings/storage/browse')
    async def browse_storage_folder(request: Request):
        require_auth(request)
        if (request.client.host if request.client else '') not in {'127.0.0.1', '::1', 'localhost'}:
            raise HTTPException(409, 'The folder picker opens on the Archive X PC. On this device, type the folder path instead.')
        def _pick():
            try:
                import tkinter as tk
                from tkinter import filedialog
                root = tk.Tk()
                root.withdraw()
                root.attributes('-topmost', True)
                root.update()
                initial = str(config.USER_DATA_DIR) if config.USER_DATA_DIR.exists() else str(Path.home())
                selected = filedialog.askdirectory(
                    title='Select Archive X data folder',
                    initialdir=initial,
                    mustexist=False,
                )
                try:
                    root.destroy()
                except Exception:
                    pass
                return str(selected or '').strip()
            except Exception as exc:
                return {'error': f'Native folder picker unavailable: {type(exc).__name__}: {exc}'}
        result = await asyncio.to_thread(_pick)
        if isinstance(result, dict):
            raise HTTPException(503, result['error'])
        return {'ok': True, 'selected_path': result}

    @router.post('/api/settings/storage/move')
    async def move_storage(request: Request):
        require_auth(request)
        data = await request.json()
        destination = str(data.get('destination') or '').strip()
        if not destination:
            raise HTTPException(400, 'Destination folder is required')
        active_jobs = db.active_jobs()
        blocking_jobs = [j for j in active_jobs if str(j.get('status') or '').upper() in {'QUEUED','RUNNING','PAUSING','CANCEL_REQUESTED'}]
        if blocking_jobs:
            raise HTTPException(409, f'Pause or stop {len(blocking_jobs)} running/queued job(s) before moving Archive X data')
        try:
            result = config.request_data_move(destination)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        return {'ok': True, **result, 'message': 'The data move is queued for the next Archive X restart. Existing data will be copied, verified, and the old folder removed only after verification succeeds.'}

    @router.post('/api/settings/storage/cancel-move')
    async def cancel_storage_move(request: Request):
        require_auth(request)
        return {'ok': True, **config.cancel_pending_data_move()}


    # ------------------------------------------------------------------
    # SUBSECTION: Provider dashboard
    # Provider status and provenance.
    # ------------------------------------------------------------------
    @router.get('/api/settings/providers')
    async def provider_dashboard(request: Request):
        require_auth(request)
        def _read_provider_dashboard():
            with db.reading() as rdb:
                states = [dict(r) for r in rdb.execute('SELECT * FROM provider_sync_state ORDER BY provider,entity_type').fetchall()]
                cache_stats = [dict(r) for r in rdb.execute('SELECT * FROM api_cache_stats ORDER BY provider').fetchall()]
                cache_count = rdb.execute('SELECT COUNT(*) n FROM metadata_cache').fetchone()['n']
                artwork_count = rdb.execute('SELECT COUNT(*) n FROM performer_image_cache').fetchone()['n'] + rdb.execute('SELECT COUNT(*) n FROM studio_image_cache').fetchone()['n']
                return states, cache_stats, int(cache_count), int(artwork_count)
        states, cache_stats, cache_count, artwork_count = await asyncio.to_thread(_read_provider_dashboard)
        from ..metadata.api_broker import snapshot as metadata_broker_snapshot
        return {'providers': states, 'cache_stats': cache_stats, 'metadata_cache_entries': cache_count, 'artwork_cache_entries': artwork_count, 'broker': metadata_broker_snapshot()}

    @router.get('/api/metadata/provenance/{media_id}')
    async def metadata_provenance(media_id: int, request: Request):
        require_auth(request)
        def _read_provenance():
            with db.reading() as rdb:
                return [dict(r) for r in rdb.execute('SELECT * FROM provider_field_sources WHERE media_id=? ORDER BY field,provider', (int(media_id),)).fetchall()]
        return await asyncio.to_thread(_read_provenance)

    @router.post('/api/metadata/provider-sync-mark')
    async def provider_sync_mark(request: Request):
        require_auth(request)
        data = await request.json()
        provider = str(data.get('provider') or '').strip().casefold()
        entity_type = str(data.get('entity_type') or 'scene').strip().casefold()
        if not provider:
            raise HTTPException(400, 'provider is required')
        now = _iso_now()
        with db.lock:
            db.conn.execute(
                'INSERT INTO provider_sync_state(provider,entity_type,last_sync_at,status,error,request_count,last_latency_ms,rate_remaining,rate_reset_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?) '
                'ON CONFLICT(provider,entity_type) DO UPDATE SET last_sync_at=excluded.last_sync_at,status=excluded.status,error=excluded.error,request_count=provider_sync_state.request_count+excluded.request_count,last_latency_ms=excluded.last_latency_ms,rate_remaining=excluded.rate_remaining,rate_reset_at=excluded.rate_reset_at,updated_at=excluded.updated_at',
                (provider, entity_type, now, str(data.get('status') or 'OK'), str(data.get('error') or ''), int(data.get('request_count') or 0), float(data.get('latency_ms')) if data.get('latency_ms') is not None else None, int(data.get('rate_remaining')) if data.get('rate_remaining') is not None else None, float(data.get('rate_reset_at')) if data.get('rate_reset_at') is not None else None, now)
            )
            db.conn.commit()
        return {'ok': True}
