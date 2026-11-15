"""Split volume recognition, validation, and random-access reading.

Supports two volume naming conventions:

- **Byte-split** (legacy): ``original.ext.001``, ``original.ext.002`` — raw byte
  boundaries, uploaded as documents.
- **Playable-part** (v29.4.380+): ``original.part01.ext`` — FFmpeg lossless
  stream-copy at keyframe boundaries, each part is an independently playable
  video uploaded with ``DocumentAttributeVideo``.

Both conventions share the same DB schema (``split_file_groups`` /
``split_file_parts``) and the same ``SplitVolumeSet`` / ``SplitDeliveryResult``
data structures.
"""

# ------------------------------------------------------------------
# FILE MAP (search for "SECTION:" to jump between sections)
#   - Volume naming and discovery
#   - Random-access reading
#   - ZIP member access
#   - Creating raw volumes
#   - Creating playable parts
#   - Persistence and delivery results
# ------------------------------------------------------------------
from __future__ import annotations

import bisect
import json
import mimetypes
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


# Require an extension before the numbered volume marker and at least three
# digits, so filenames like "Show Part 01.mkv" and "Movie CD01.mkv" never
# match. The original filename may itself contain dots (e.g. .tar.gz or .zip).
_SPLIT_VOLUME_RE = re.compile(
    r"^(?P<logical>.+\.[A-Za-z0-9][A-Za-z0-9_-]{0,31})\.(?P<index>[0-9]{3,})$",
    re.IGNORECASE,
)

# Playable-part naming: ``stem.partNN.ext`` where each part is independently
# playable. The logical filename is ``stem.ext`` (the extension moves to the
# end so Telegram recognises the MIME type).
_PLAYABLE_PART_RE = re.compile(
    r"^(?P<stem>.+)\.part(?P<index>[0-9]{2,})\.(?P<ext>[A-Za-z0-9][A-Za-z0-9_-]{0,31})$",
    re.IGNORECASE,
)


# ==============================================================================
# SECTION: Volume naming and discovery
# Recognizes, validates and discovers split volumes.
# ==============================================================================

class SplitFileError(ValueError):
    """The named split-volume sequence is absent, invalid, or unsupported."""


@dataclass(frozen=True)
class SplitVolumeName:
    """Parsed parts of a split volume filename."""
    logical_filename: str
    index: int
    suffix_width: int


def recognize_split_volume(filename: str | os.PathLike[str]) -> SplitVolumeName | None:
    """Parse the byte-split suffix or return ``None`` for an ordinary filename."""
    name = Path(filename).name
    match = _SPLIT_VOLUME_RE.fullmatch(name)
    if not match:
        return None
    suffix = match.group("index")
    index = int(suffix)
    if index < 1 or suffix != f"{index:03d}":
        # Enforce the same canonical zero-padding emitted by Archive X. This
        # excludes ambiguous spellings such as .0001 for volume 001.
        return None
    return SplitVolumeName(match.group("logical"), index, len(suffix))


@dataclass(frozen=True)
class SplitVolume:
    """One validated volume on disk."""
    index: int
    path: Path
    size: int


@dataclass(frozen=True)
class SplitVolumeSet:
    """An ordered, complete set of volumes."""
    logical_filename: str
    volumes: tuple[SplitVolume, ...]
    total_size: int

    @property
    def paths(self) -> tuple[Path, ...]:
        return tuple(volume.path for volume in self.volumes)

    @property
    def sizes(self) -> tuple[int, ...]:
        return tuple(volume.size for volume in self.volumes)


def discover_telegram_split_groups(records: Iterable[dict]) -> list[dict]:
    """Reconstruct complete split groups from Telegram document inventory rows.

    Rows should include ``id``, ``name`` and ``size``; ``record`` may carry the
    original message metadata used by the Telegram document registry. Identical
    logical filenames may be uploaded more than once, so groups are separated
    at each new ``.001`` message rather than merged solely by filename.

    The existing Archive X upload flow publishes a ``<stem>.jpg`` contact sheet
    after each sequence. It is associated only when it falls after all volumes
    and before the next sequence with the same logical filename. A missing
    contact sheet does not invalidate a byte-complete volume sequence.
    """
    supplied = [dict(item) for item in records if isinstance(item, dict)]
    volume_rows: list[tuple[dict, SplitVolumeName]] = []
    images: list[dict] = []
    volume_ids: set[int] = set()
    for row in supplied:
        try:
            message_id = int(row.get("id") or row.get("message_id") or 0)
            size = int(row.get("size") or row.get("size_bytes") or 0)
        except (TypeError, ValueError):
            continue
        if message_id <= 0:
            continue
        name = Path(str(row.get("name") or row.get("filename") or "")).name
        parsed = recognize_split_volume(name)
        if parsed is not None:
            volume_ids.add(message_id)
            volume_rows.append(({**row, "id": message_id, "name": name, "size": size}, parsed))
        elif Path(name).suffix.casefold() in {".jpg", ".jpeg"} and size > 0:
            images.append({**row, "id": message_id, "name": name, "size": size})

    # Index repeated uploads of the same logical filename independently.
    by_logical: dict[str, list[tuple[dict, SplitVolumeName]]] = {}
    for row, parsed in volume_rows:
        by_logical.setdefault(parsed.logical_filename, []).append((row, parsed))

    raw_groups: list[dict] = []
    for logical_filename, entries in by_logical.items():
        entries.sort(key=lambda entry: (int(entry[0]["id"]), entry[1].index))
        candidates: list[list[tuple[dict, SplitVolumeName]]] = []
        current: list[tuple[dict, SplitVolumeName]] | None = None
        for entry in entries:
            _row, parsed = entry
            if parsed.index == 1:
                current = [entry]
                candidates.append(current)
            elif current is not None and int(entry[0]["id"]) > int(current[-1][0]["id"]):
                current.append(entry)
            # A .002 without a preceding .001 is deliberately left orphaned.

        for candidate in candidates:
            ordered = sorted(candidate, key=lambda entry: entry[1].index)
            indices = [parsed.index for _row, parsed in ordered]
            ids_in_index_order = [int(row["id"]) for row, _parsed in ordered]
            sizes = [int(row.get("size") or 0) for row, _parsed in ordered]
            if (len(indices) < 2
                    or indices != list(range(1, len(indices) + 1))
                    or len(set(indices)) != len(indices)
                    or any(size <= 0 for size in sizes)
                    or ids_in_index_order != sorted(ids_in_index_order)):
                continue
            primary_row = ordered[0][0]
            primary_id = int(primary_row["id"])
            last_part_id = max(ids_in_index_order)
            # Repeated identical filenames need separate contact sheets. Bound
            # a group's sheet search by the next .001 of that same filename.
            next_primary = min(
                (int(other[0][0]["id"]) for other in candidates
                 if other is not candidate and int(other[0][0]["id"]) > last_part_id),
                default=None,
            )
            stem = Path(logical_filename).stem
            expected_sheets = [
                image for image in images
                if str(image["name"]).casefold() in {f"{stem}.jpg".casefold(), f"{stem}.jpeg".casefold()}
                and int(image["id"]) > last_part_id
                and (next_primary is None or int(image["id"]) < next_primary)
            ]
            expected_sheets.sort(key=lambda item: int(item["id"]))
            thumbnail = expected_sheets[0] if expected_sheets else None
            primary_raw_record = dict(primary_row.get("record") or {})
            mime = (mimetypes.guess_type(logical_filename)[0]
                    or str(primary_row.get("mime") or primary_raw_record.get("mime") or "application/octet-stream"))
            parts = []
            for row, parsed in ordered:
                parts.append({
                    "part_index": int(parsed.index),
                    "message_id": int(row["id"]),
                    "logical_filename": logical_filename,
                    "telegram_filename": str(row["name"]),
                    "size_bytes": int(row["size"]),
                    "record": dict(row.get("record") or {}),
                })
            raw_groups.append({
                "id": primary_id,
                "name": logical_filename,
                "size": sum(sizes),
                "duration": float(primary_row.get("duration") or primary_raw_record.get("duration") or 0),
                "mime": mime,
                "caption": str(primary_row.get("caption") or primary_raw_record.get("caption") or ""),
                "thumbnail_message_id": int(thumbnail["id"]) if thumbnail else None,
                "is_split_group": True,
                "_split_group_parts": parts,
                "_split_group_part_ids": ids_in_index_order,
                # All part IDs came from a completed history traversal.
                "_split_group_seen_part_ids": ids_in_index_order,
                "_split_group_part_count": len(parts),
                "width": int(primary_row.get("width") or primary_raw_record.get("width") or 0),
                "height": int(primary_row.get("height") or primary_raw_record.get("height") or 0),
                "fps": float(primary_row.get("fps") or primary_raw_record.get("fps") or 0),
                "bitrate": int(primary_row.get("bitrate") or primary_raw_record.get("bitrate") or 0),
                "codec": str(primary_row.get("codec") or primary_raw_record.get("codec") or ""),
                "audio_codec": str(primary_row.get("audio_codec") or primary_raw_record.get("audio_codec") or ""),
            })
    raw_groups.sort(key=lambda item: int(item["id"]), reverse=True)
    return raw_groups


# ==============================================================================
# SECTION: Random-access reading
# File-like reader and byte range mapping across volumes.
# ==============================================================================


@dataclass(frozen=True)
class SplitByteRange:
    """One local byte-range slice contributing to a logical-file range."""
    part_index: int
    message_id: int
    logical_start: int
    logical_end: int
    local_start: int
    local_end: int
    telegram_filename: str


def map_split_range(parts: Iterable[dict], start: int, end: int, *, total_size: int | None = None) -> tuple[SplitByteRange, ...]:
    """Map an inclusive logical byte range to ordered per-volume local ranges.

    This is the shared range calculation used by Telegram-backed playback and
    tests. It rejects gaps, duplicate indexes, invalid message IDs/sizes, and a
    group byte total that disagrees with its registered logical size.
    """
    rows = sorted((dict(part) for part in parts), key=lambda part: int(part.get("part_index") or 0))
    if not rows:
        raise SplitFileError("Split-file range has no registered volumes")
    indices = [int(part.get("part_index") or 0) for part in rows]
    if indices != list(range(1, len(rows) + 1)):
        raise SplitFileError("Split-file range has missing or duplicate volume indices")
    sizes = [int(part.get("size_bytes") or 0) for part in rows]
    if any(size <= 0 for size in sizes):
        raise SplitFileError("Split-file range contains an empty or invalid volume")
    summed_size = sum(sizes)
    if total_size is not None and summed_size != int(total_size):
        raise SplitFileError(f"Split-file part sizes total {summed_size}; expected logical size {int(total_size)}")
    logical_size = int(total_size if total_size is not None else summed_size)
    start, end = int(start), int(end)
    if start < 0 or end < start or start >= logical_size:
        raise ValueError("Invalid split-file byte range")
    end = min(end, logical_size - 1)
    mapped: list[SplitByteRange] = []
    # Build prefix-sum array of part start offsets for O(log N) lookup
    prefix_starts: list[int] = []
    offset_acc = 0
    for size in sizes:
        prefix_starts.append(offset_acc)
        offset_acc += size
    # Binary search: find the first part that contains `start`
    first_part = bisect.bisect_right(prefix_starts, start) - 1
    # Iterate only through parts that overlap [start, end]
    for i in range(first_part, len(rows)):
        part_start = prefix_starts[i]
        part_end = part_start + sizes[i] - 1
        if part_start > end:
            break  # no more overlapping parts
        overlap_start = max(start, part_start)
        overlap_end = min(end, part_end)
        part = rows[i]
        message_id = int(part.get("message_id") or 0)
        if message_id <= 0:
            raise SplitFileError(f"Split volume {part.get('part_index')} has no valid Telegram message ID")
        mapped.append(SplitByteRange(
            part_index=int(part["part_index"]), message_id=message_id,
            logical_start=overlap_start, logical_end=overlap_end,
            local_start=overlap_start - part_start, local_end=overlap_end - part_start,
            telegram_filename=Path(str(part.get("telegram_filename") or "")).name,
        ))
    expected_length = end - start + 1
    actual_length = sum(item.local_end - item.local_start + 1 for item in mapped)
    if actual_length != expected_length:
        raise SplitFileError(f"Split-file range maps to {actual_length} bytes; expected {expected_length}")
    return tuple(mapped)


# ==============================================================================
# SECTION: ZIP member access
# Stored ZIP validation and member range reads.
# ==============================================================================


# ---------------------------------------------------------------------------
# Container policy: mp4/m4v/mov stay as mp4; everything else → matroska (.mkv)
# ---------------------------------------------------------------------------

_MP4_EXTENSIONS = {".mp4", ".m4v", ".mov"}


# ==============================================================================
# SECTION: Creating playable parts
# Splits video into independently playable parts.
# ==============================================================================

def _playable_container(source_ext: str) -> tuple[str, str]:
    """Return (ffmpeg_format, file_extension) for the playable-part container."""
    if source_ext.lower() in _MP4_EXTENSIONS:
        return "mp4", ".mp4"
    return "matroska", ".mkv"


def _make_playable_part_name(stem: str, ext: str, index: int) -> str:
    """Build a ``stem.partNN.ext`` filename."""
    return f"{stem}.part{index:02d}{ext}"


def _playable_part_telegram_name(logical_filename: str, index: int) -> str:
    """Build the Telegram filename for a playable part from the logical name."""
    p = Path(logical_filename)
    stem = p.stem            # e.g. "Movie"
    _, ext = _playable_container(p.suffix)  # e.g. ".mkv"
    return _make_playable_part_name(stem, ext, index)


def legacy_volume_name(logical_filename: str, index: int) -> str:
    """Name of a raw byte volume from older builds: ``Movie.mkv.001``."""
    return f"{Path(str(logical_filename or '')).name}.{int(index):03d}"


def split_part_expected_name(logical_filename: str, index: int, recorded_name: str = "") -> str:
    """Expected Telegram filename of volume ``index`` for this group.

    Groups uploaded by current builds use playable parts (``Movie.part01.mp4``);
    older groups used raw byte volumes (``Movie.mp4.001``). The recorded part
    name decides which scheme a group uses, so verification, Library moves and
    channel moves accept both instead of assuming the old one.
    """
    name = Path(str(recorded_name or "")).name
    playable = _playable_part_telegram_name(logical_filename, index)
    legacy = legacy_volume_name(logical_filename, index)
    if name in (playable, legacy):
        return name
    return playable if _PLAYABLE_PART_RE.fullmatch(name) else legacy


def _size_packed_cut_times(ffprobe: str, source: Path, target_bytes: float) -> list[float]:
    """Choose keyframe cut times so each part holds close to, but under, ``target_bytes``.

    Reads packet timestamps/sizes for all streams (one sequential read of the
    file) and greedily packs keyframe-aligned parts. Returns [] when packet data
    is unavailable; the caller then falls back to fixed-length segments.
    """
    try:
        vprobe = subprocess.run(
            [ffprobe, "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=index", "-of", "csv=p=0", str(source)],
            capture_output=True, text=True, check=True,
        )
        video_index = int((vprobe.stdout or "0").strip().splitlines()[0])
        r = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "packet=stream_index,pts_time,dts_time,size,flags",
             "-of", "csv=p=0", str(source)],
            capture_output=True, text=True, check=True,
        )
    except (subprocess.CalledProcessError, OSError, ValueError, IndexError):
        return []
    packets = []          # (time, size, is_video_keyframe)
    for line in (r.stdout or "").splitlines():
        f = line.split(",")
        if len(f) < 5:
            continue
        try:
            idx = int(f[0]); ts = f[1] if f[1] not in ("", "N/A") else f[2]
            packets.append((float(ts), int(f[3]), idx == video_index and "K" in f[4]))
        except ValueError:
            continue
    if not packets:
        return []
    packets.sort(key=lambda x: x[0])
    keyframes = [t for t, _, k in packets if k]
    if len(keyframes) < 2:
        return []
    # cumulative bytes before each keyframe time
    cum_at = {}
    total = 0; ki = 0
    for t, size, _ in packets:
        while ki < len(keyframes) and keyframes[ki] <= t:
            cum_at[keyframes[ki]] = total; ki += 1
        total += size
    while ki < len(keyframes):
        cum_at[keyframes[ki]] = total; ki += 1
    cuts, start_bytes, prev = [], 0, None
    for kf in keyframes[1:]:
        if cum_at[kf] - start_bytes > target_bytes and prev is not None and prev > (cuts[-1] if cuts else 0):
            cuts.append(prev); start_bytes = cum_at[prev]
        prev = kf
    return cuts


def split_video_into_playable_parts(
    source_path: str | os.PathLike[str],
    output_dir: str | os.PathLike[str],
    max_part_bytes: int,
    *,
    logical_filename: str | None = None,
    ffmpeg_path: str | None = None,
    ffprobe_path: str | None = None,
) -> SplitVolumeSet:
    """Split a video into independently playable parts using FFmpeg lossless stream-copy.

    Each part is cut at keyframe boundaries (``-c copy``) so there is no
    re-encoding and no quality loss. Parts are named ``stem.part01.ext``,
    ``stem.part02.ext``, etc.

    The source is never modified. Returns a ``SplitVolumeSet`` whose
    ``logical_filename`` is the original name (e.g. ``Movie.mkv``) and whose
    volumes point to the generated part files.
    """
    source = Path(source_path)
    output = Path(output_dir)
    limit = int(max_part_bytes)
    if limit <= 0:
        raise ValueError("max_part_bytes must be positive")
    if not source.is_file() or source.stat().st_size <= 0:
        raise SplitFileError("Split-upload source must be a non-empty regular file")

    logical_name = Path(str(logical_filename or source.name)).name
    if not logical_name or logical_name in {".", ".."}:
        raise SplitFileError("A safe logical filename is required for split upload")

    # ---- resolve FFmpeg / ffprobe ----
    ffmpeg = ffmpeg_path
    ffprobe = ffprobe_path
    if not ffmpeg or not ffprobe:
        from ..engines.uploader import ffmpeg_tools
        _ff, _fp = ffmpeg_tools()
        ffmpeg = ffmpeg or _ff
        ffprobe = ffprobe or _fp
    if not ffmpeg or not ffprobe:
        raise SplitFileError("FFmpeg is required for playable-part splitting but was not found")

    # ---- probe source ----
    try:
        probe_result = subprocess.run(
            [ffprobe, "-v", "error", "-show_format", "-show_streams", "-of", "json", str(source)],
            capture_output=True, text=True, check=True,
        )
        probe_data = json.loads(probe_result.stdout or "{}")
    except (subprocess.CalledProcessError, json.JSONDecodeError) as exc:
        raise SplitFileError(f"FFprobe failed on {source.name}: {exc}") from exc

    duration = float(probe_data.get("format", {}).get("duration") or 0)
    if duration <= 0:
        raise SplitFileError(f"Cannot split {source.name}: no duration detected")
    streams = list(probe_data.get("streams") or [])
    if not any(st.get("codec_type") == "video" for st in streams):
        raise SplitFileError(f"Cannot split {source.name}: no video stream")

    total_size = int(source.stat().st_size)
    fmt, ext = _playable_container(source.suffix)
    stem = Path(logical_name).stem
    output.mkdir(parents=True, exist_ok=True)

    # v29.4.390: one-pass segmenting. The previous loop cut by time with input
    # seeking on every part, which (a) never checked part sizes, so variable
    # bitrate video produced parts above the Telegram limit, (b) mapped every
    # stream (-map 0), so the MP4 muxer rejected timecode/data tracks common in
    # camera and phone .MOV/.MP4 files, and (c) overlapped neighbouring parts.
    # The segment muxer cuts once at keyframes with no overlap; any part over
    # the limit triggers a re-split with proportionally shorter segments.
    if fmt == "mp4":
        maps = ["-map", "0:v:0", "-map", "0:a?", "-sn", "-dn"]
        mux_opts = ["-segment_format", "mp4", "-segment_format_options", "movflags=+faststart"]
    else:
        maps = ["-map", "0:v", "-map", "0:a?", "-map", "0:s?", "-dn"]
        mux_opts = ["-segment_format", "matroska"]
    target = limit * 0.92                                   # headroom for container overhead / VBR
    segment_time = max(2.0, duration * target / max(total_size, 1))
    created: list[Path] = []

    def _clear(paths):
        for part in paths:
            try:
                part.unlink(missing_ok=True)
            except OSError:
                pass

    try:
        cut_times = _size_packed_cut_times(ffprobe, source, target)
        for attempt in range(8):
            _clear(created); created = []
            work = output / f".segments_{attempt}"
            work.mkdir(parents=True, exist_ok=True)
            pattern = work / f"seg%05d{ext}"
            seg_spec = (["-segment_times", ",".join(f"{t:.6f}" for t in cut_times)] if cut_times
                        else ["-segment_time", f"{segment_time:.3f}"])
            cmd = [ffmpeg, "-y", "-nostdin", "-loglevel", "error", "-i", str(source), *maps,
                   "-c", "copy", "-avoid_negative_ts", "make_zero",
                   "-f", "segment", *seg_spec, "-reset_timestamps", "1",
                   "-break_non_keyframes", "0", *mux_opts, str(pattern)]
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode != 0:
                raise SplitFileError(f"FFmpeg split failed: {result.stderr.strip()[:500]}")
            segments = sorted(p for p in work.iterdir() if p.is_file() and p.stat().st_size > 0)
            if not segments:
                raise SplitFileError("FFmpeg produced no output parts")
            biggest = max(p.stat().st_size for p in segments)
            if biggest <= limit:
                for i, seg in enumerate(segments, 1):
                    final = output / _make_playable_part_name(stem, ext, i)
                    seg.replace(final)
                    created.append(final)
                try:
                    work.rmdir()
                except OSError:
                    pass
                break
            # Too large: one keyframe interval can be long, so shrink proportionally.
            _clear(segments)
            try:
                work.rmdir()
            except OSError:
                pass
            if cut_times:
                # Packed cut points overshot (container overhead); shrink the target and re-pack.
                target *= 0.95 * limit / biggest
                cut_times = _size_packed_cut_times(ffprobe, source, target)
                if cut_times:
                    continue
            new_time = segment_time * (target / biggest) * 0.9
            if new_time < 1.0 or new_time >= segment_time:
                raise SplitFileError(
                    f"Cannot split {source.name} into parts below {limit} bytes: a single keyframe interval "
                    f"is larger than the limit ({biggest} bytes). Re-encode with shorter keyframe spacing or use Optimize."
                )
            segment_time = new_time
        else:
            raise SplitFileError(f"Could not split {source.name} under the {limit}-byte part limit")

        # ---- build the SplitVolumeSet ----
        volumes: list[SplitVolume] = []
        for i, path in enumerate(created, 1):
            volumes.append(SplitVolume(index=i, path=path, size=int(path.stat().st_size)))

        return SplitVolumeSet(
            logical_filename=logical_name,
            volumes=tuple(volumes),
            total_size=sum(v.size for v in volumes),
        )

    except Exception:
        for part in created:
            try:
                part.unlink(missing_ok=True)
            except OSError:
                pass
        raise


# ==============================================================================
# SECTION: Persistence and delivery results
# Saving and loading split groups and delivery results.
# ==============================================================================

def store_split_group(db, *, group_id: str, destination_channel: str | int,
                      logical_filename: str, primary_message_id: int | None,
                      thumbnail_message_id: int | None, total_size: int,
                      parts: Iterable[dict], metadata: dict | None = None,
                      identity_key: str = "", status: str = "PREPARING") -> None:
    """Persist an upload group before exposing it to the Library."""
    from .. import config
    from .resource_registry import ensure_resource_tables

    ensure_resource_tables(db)
    group_id = str(group_id or "").strip()
    channel = str(destination_channel or "").strip()
    logical_filename = Path(str(logical_filename or "")).name
    part_rows = [dict(part) for part in parts]
    metadata = dict(metadata or {})
    if not group_id or not channel or not channel.lstrip("-").isdigit():
        raise SplitFileError("Split group requires an ID and a numeric Telegram channel ID")
    if not logical_filename or not part_rows:
        raise SplitFileError("Split group requires an original filename and all uploaded parts")
    if str(status or "PREPARING").upper() not in {"PREPARING", "VERIFIED", "FAILED", "INCOMPLETE"}:
        raise SplitFileError("Invalid split group status")
    part_rows.sort(key=lambda row: int(row.get("part_index") or 0))
    expected = list(range(1, len(part_rows) + 1))
    indices = [int(row.get("part_index") or 0) for row in part_rows]
    if indices != expected:
        raise SplitFileError("Cannot register an incomplete split group; part indices must be continuous from 001")
    if any(int(row.get("message_id") or 0) <= 0 or int(row.get("size_bytes") or 0) <= 0 for row in part_rows):
        raise SplitFileError("Cannot register split group with missing Telegram IDs or empty parts")
    for row in part_rows:
        idx = int(row['part_index'])
        byte_split_name = f"{logical_filename}.{idx:03d}"
        playable_name = _playable_part_telegram_name(logical_filename, idx)
        row_logical_name = Path(str(row.get("logical_filename") or logical_filename)).name
        telegram_name = Path(str(row.get("telegram_filename") or byte_split_name)).name
        if row_logical_name != logical_filename or telegram_name not in (byte_split_name, playable_name):
            raise SplitFileError(
                f"Cannot register split volume part {idx}: logical filename or Telegram filename does not match the sequence"
            )
    computed_total = sum(int(row["size_bytes"]) for row in part_rows)
    # Playable-part (FFmpeg stream-copy) splits won't match the source size
    # exactly due to keyframe snapping and container overhead. Accept both
    # exact match (byte-split) and the computed total as the canonical size.
    if int(total_size) != computed_total:
        # Check if any part uses playable naming — if so, use computed total
        has_playable = any(
            _PLAYABLE_PART_RE.fullmatch(Path(str(row.get("telegram_filename") or "")).name)
            for row in part_rows
        )
        if has_playable:
            total_size = computed_total
        else:
            raise SplitFileError(f"Split group size mismatch: source {int(total_size)} bytes, uploaded parts {computed_total} bytes")
    if primary_message_id is not None and int(primary_message_id) != int(part_rows[0]["message_id"]):
        raise SplitFileError("Split group primary message must be its first volume")
    requested_status = str(status or "PREPARING").upper()
    if requested_status == "VERIFIED":
        thumbnail_id = int(thumbnail_message_id or 0)
        if thumbnail_id and thumbnail_id <= max(int(row["message_id"]) for row in part_rows):
            raise SplitFileError("Cannot register a verified split group unless its contact sheet follows every volume")
    now = config.now_iso()
    with db.lock:
        db.conn.execute(
            "INSERT INTO split_file_groups(group_id,destination_channel,logical_filename,primary_message_id,thumbnail_message_id,total_size,part_count,mime_type,duration,width,height,fps,bitrate,codec,audio_codec,caption,identity_key,status,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(group_id) DO UPDATE SET destination_channel=excluded.destination_channel,logical_filename=excluded.logical_filename,primary_message_id=excluded.primary_message_id,thumbnail_message_id=excluded.thumbnail_message_id,total_size=excluded.total_size,part_count=excluded.part_count,mime_type=excluded.mime_type,duration=excluded.duration,width=excluded.width,height=excluded.height,fps=excluded.fps,bitrate=excluded.bitrate,codec=excluded.codec,audio_codec=excluded.audio_codec,caption=excluded.caption,identity_key=excluded.identity_key,status=excluded.status,updated_at=excluded.updated_at",
            (group_id, channel, logical_filename, int(primary_message_id) if primary_message_id else None,
             int(thumbnail_message_id) if thumbnail_message_id else None, int(total_size), len(part_rows),
             str(metadata.get("mime_type") or "application/octet-stream"), float(metadata.get("duration") or 0),
             int(metadata.get("width") or 0), int(metadata.get("height") or 0), float(metadata.get("fps") or 0),
             int(metadata.get("bitrate") or 0), str(metadata.get("video_codec") or metadata.get("codec") or ""),
             str(metadata.get("audio_codec") or ""), str(metadata.get("caption") or ""), str(identity_key or ""),
             str(status or "PREPARING").upper(), now, now),
        )
        db.conn.execute("DELETE FROM split_file_parts WHERE group_id=?", (group_id,))
        db.conn.executemany(
            "INSERT INTO split_file_parts(group_id,part_index,destination_channel,message_id,logical_filename,telegram_filename,size_bytes,created_at) VALUES(?,?,?,?,?,?,?,?)",
            [(group_id, int(row["part_index"]), channel, int(row["message_id"]), logical_filename,
              Path(str(row.get("telegram_filename") or row.get("_telegram_filename") or f"{logical_filename}.{int(row['part_index']):03d}")).name,
              int(row["size_bytes"]), now) for row in part_rows],
        )
        db.conn.commit()


def finalize_split_group(db, group_id: str, *, status: str = "VERIFIED") -> None:
    """Mark a group ready only when persisted parts are complete and size-consistent."""
    from .. import config
    status = str(status or "").upper()
    if status not in {"VERIFIED", "FAILED", "INCOMPLETE"}:
        raise ValueError("Invalid split group status")
    with db.lock:
        group = db.conn.execute("SELECT * FROM split_file_groups WHERE group_id=?", (str(group_id),)).fetchone()
        if not group:
            raise SplitFileError(f"Split group {group_id} was not registered")
        parts = db.conn.execute(
            "SELECT part_index,size_bytes,message_id,logical_filename,telegram_filename,destination_channel "
            "FROM split_file_parts WHERE group_id=? ORDER BY part_index",
            (str(group_id),),
        ).fetchall()
        if status == "VERIFIED":
            indices = [int(row["part_index"]) for row in parts]
            if indices != list(range(1, int(group["part_count"]) + 1)):
                raise SplitFileError("Cannot verify split group: part sequence is incomplete")
            parts_total = sum(int(row["size_bytes"]) for row in parts)
            # Playable-part splits store the combined parts size as total_size,
            # which may differ from the original source. Accept if stored total
            # matches the parts total (which was set at store time).
            if parts_total != int(group["total_size"]):
                raise SplitFileError("Cannot verify split group: logical byte size does not match its parts")
            if any(int(row["size_bytes"] or 0) <= 0 or int(row["message_id"] or 0) <= 0 for row in parts):
                raise SplitFileError("Cannot verify split group: a volume is empty or missing its Telegram message ID")
            for row in parts:
                idx = int(row['part_index'])
                byte_split_name = f"{str(group['logical_filename'])}.{idx:03d}"
                playable_name = _playable_part_telegram_name(str(group['logical_filename']), idx)
                if (str(row["logical_filename"]) != str(group["logical_filename"])
                        or str(row["telegram_filename"]) not in (byte_split_name, playable_name)
                        or str(row["destination_channel"]) != str(group["destination_channel"])):
                    raise SplitFileError("Cannot verify split group: volume names or channel identity are inconsistent")
            if not parts or int(parts[0]["message_id"]) != int(group["primary_message_id"] or 0):
                raise SplitFileError("Cannot verify split group: primary message is not volume 001")
            if int(group["thumbnail_message_id"] or 0) and int(group["thumbnail_message_id"]) <= max(int(row["message_id"]) for row in parts):
                raise SplitFileError("Cannot verify split group before the contact sheet follows every volume")
        db.conn.execute("UPDATE split_file_groups SET status=?,updated_at=? WHERE group_id=?",
                        (status, config.now_iso(), str(group_id)))
        db.conn.commit()


def load_split_group(db, destination_channel: str | int, primary_message_id: int) -> dict | None:
    """Return a persisted split group for any volume message without rebuilding views on every range."""
    from .resource_registry import ensure_resource_tables
    # This function runs on every playback/range request. Avoid calling
    # ensure_resource_tables unconditionally: it intentionally rebuilds the
    # resource views for migrations, which is far too expensive per byte range.
    with db.lock:
        existing = {str(row[0]) for row in db.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name IN ('split_file_groups','split_file_parts')"
        ).fetchall()}
    if existing != {'split_file_groups', 'split_file_parts'}:
        ensure_resource_tables(db)
    channel, primary = str(destination_channel), int(primary_message_id)
    with db.lock:
        group = db.conn.execute(
            "SELECT sg.* FROM split_file_groups sg WHERE sg.destination_channel=? AND (sg.primary_message_id=? OR EXISTS ("
            "SELECT 1 FROM split_file_parts sp WHERE sp.group_id=sg.group_id AND sp.destination_channel=? AND sp.message_id=?)) LIMIT 1",
            (channel, primary, channel, primary),
        ).fetchone()
        if not group:
            return None
        parts = db.conn.execute(
            "SELECT part_index,message_id,destination_channel,logical_filename,telegram_filename,size_bytes FROM split_file_parts "
            "WHERE group_id=? ORDER BY part_index", (str(group["group_id"]),),
        ).fetchall()
    result = dict(group)
    result["parts"] = [dict(row) for row in parts]
    if str(result.get("status") or "").upper() == "VERIFIED":
        indices = [int(row["part_index"]) for row in result["parts"]]
        parts_total = sum(int(row["size_bytes"]) for row in result["parts"])
        if indices != list(range(1, int(result.get("part_count") or 0) + 1)) or parts_total != int(result.get("total_size") or 0):
            raise SplitFileError("Registered split group is incomplete or has inconsistent sizes")
        if not result["parts"] or int(result["parts"][0]["message_id"]) != int(result.get("primary_message_id") or 0):
            raise SplitFileError("Registered split group primary volume is invalid")
        for row in result["parts"]:
            idx = int(row['part_index'])
            logical = str(result.get('logical_filename') or '')
            byte_split_name = f"{logical}.{idx:03d}"
            playable_name = _playable_part_telegram_name(logical, idx) if logical else ""
            if (int(row.get("size_bytes") or 0) <= 0 or int(row.get("message_id") or 0) <= 0
                    or str(row.get("logical_filename") or "") != logical
                    or str(row.get("telegram_filename") or "") not in (byte_split_name, playable_name)
                    or str(row.get("destination_channel") or "") != channel):
                raise SplitFileError("Registered split group contains an invalid or mismatched volume")
        if int(result.get("thumbnail_message_id") or 0) and int(result.get("thumbnail_message_id") or 0) <= max(int(row["message_id"]) for row in result["parts"]):
            raise SplitFileError("Registered split group contact sheet does not follow every volume")
    return result


@dataclass(frozen=True)
class UploadedSplitPart:
    """One split part after upload to Telegram."""
    part_index: int
    message_id: int
    logical_filename: str
    telegram_filename: str
    size_bytes: int


@dataclass(frozen=True)
class SplitDeliveryResult:
    """Outcome of delivering all parts of a split group."""
    primary_message: object
    parts: tuple[UploadedSplitPart, ...]
    group_id: str
    logical_filename: str
    total_size: int
    mime_type: str

    @property
    def message_ids(self) -> tuple[int, ...]:
        return tuple(part.message_id for part in self.parts)

    def to_dict(self) -> dict:
        return {
            "group_id": self.group_id,
            "logical_filename": self.logical_filename,
            "total_size": int(self.total_size),
            "mime_type": self.mime_type,
            "parts": [
                {"part_index": int(part.part_index), "message_id": int(part.message_id),
                 "logical_filename": part.logical_filename, "telegram_filename": part.telegram_filename,
                 "size_bytes": int(part.size_bytes)} for part in self.parts
            ],
        }
