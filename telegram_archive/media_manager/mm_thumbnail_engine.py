"""Telegram Media Manager: Telegram-to-local range bridge and smart MP4 thumbnail extractor.

Split out of ``telegram_media_manager.py`` in v29.4.387 without behaviour
changes. The main module executes the parts, in order, inside its own module
namespace, so every function, constant and ``globals()`` lookup is exactly as
when this was one file. Do not import this file directly.
"""

# ------------------------------------------------------------------
# FILE MAP (search for "SECTION:" to jump between sections)
#   - MP4 box parsing
#   - MP4 sample tables
#   - Codec config and GOP decode
#   - Smart thumbnail from Telegram
#   - Local HTTP range bridge
#   - Target planning and stream decode
#   - FFmpeg snapshot helpers
#   - Thumbnail generators
#   - Telegram document operations
#   - Message thumbnail entry points
# ------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Telegram -> local HTTP range bridge.
# This is the same architecture used by the direct-to-VLC player: VLC/PyAV
# asks for byte ranges, and Telethon retrieves only those ranges from Telegram.
# The complete video is never downloaded to disk.
# ---------------------------------------------------------------------------

from ..core.telegram_media_range_resource import DirectTelegramRangeSource


# ---------------------------------------------------------------------------
# Smart MP4 thumbnail extractor.
# ---------------------------------------------------------------------------
# The old architecture asked FFmpeg to seek through a localhost HTTP view of
# the entire Telegram file.  For every one of the 36 thumbnails FFmpeg had to
# reopen/probe/seek the MP4, causing many HTTP cancellations and repeated
# Telegram range requests.  This path instead reads the MP4's own sample
# tables, identifies the nearest preceding keyframe, and fetches only the
# encoded video samples needed for that thumbnail.  H.264/AVC and H.265/HEVC
# are decoded from a compact in-memory elementary stream; no video file is
# created and no localhost HTTP server is involved.

_SMART_SUPPORTED_CODECS = {b"avc1", b"avc3", b"hvc1", b"hev1"}


# ==============================================================================
# SECTION: MP4 box parsing
# Locates and parses BMFF boxes and the moov atom.
# ==============================================================================

def _bmff_boxes(data, start=0, end=None):
    """Yield (type, start, end, payload_start) for valid ISO-BMFF boxes."""
    if end is None:
        end = len(data)
    pos = int(start)
    end = int(end)
    while pos + 8 <= end:
        size32 = unpack_from(">I", data, pos)[0]
        typ = bytes(data[pos + 4:pos + 8])
        header = 8
        if size32 == 1:
            if pos + 16 > end:
                break
            size = unpack_from(">Q", data, pos + 8)[0]
            header = 16
        elif size32 == 0:
            size = end - pos
        else:
            size = size32
        if size < header or pos + size > end:
            break
        yield typ, pos, pos + size, pos + header
        pos += size


def _bmff_child(data, parent, wanted_type):
    if parent is None:
        return None
    for box in _bmff_boxes(data, parent[3], parent[2]):
        if box[0] == wanted_type:
            return box
    return None


def _bmff_payload(data, box):
    return data[box[3]:box[2]]


def _find_box_signature(data, parent, wanted_types):
    """Find avcC/hvcC inside a visual sample-entry without assuming offsets."""
    p0, p1 = parent[1] + 8, parent[2]
    for typ in wanted_types:
        q = data.find(typ, p0, p1)
        while q >= 4:
            try:
                size = unpack_from(">I", data, q - 4)[0]
            except Exception:
                break
            start = q - 4
            if size >= 8 and start + size <= p1:
                return typ, start, start + size, q + 4
            q = data.find(typ, q + 4, p1)
    return None


def _find_moov_from_window(window, base_offset, total_size):
    """Find a moov box header in a complete or partial remote window."""
    # Normal top-level scan when the window starts at a file boundary.
    for typ, a, b, payload_start in _bmff_boxes(window):
        if typ == b"moov":
            declared = b - a
            if base_offset + b <= total_size:
                return base_offset + a, declared
            return base_offset + a, declared

    # Tail windows may start in the middle of a top-level box. Search for the
    # literal box type and validate the preceding size field.
    candidates = []
    pos = 0
    while True:
        q = window.find(b"moov", pos)
        if q < 4:
            if q < 0:
                break
            pos = q + 4
            continue
        start = q - 4
        try:
            size32 = unpack_from(">I", window, start)[0]
            header = 8
            if size32 == 1:
                if start + 16 > len(window):
                    pos = q + 4
                    continue
                size = unpack_from(">Q", window, start + 8)[0]
                header = 16
            elif size32 == 0:
                size = total_size - (base_offset + start)
            else:
                size = size32
            abs_start = base_offset + start
            if size >= header and abs_start >= 0 and abs_start + size <= total_size:
                candidates.append((abs_start, int(size)))
        except Exception:
            pass
        pos = q + 4
    return candidates[-1] if candidates else None


class MoovNotFoundError(RuntimeError):
    """The MP4 index (moov box) could not be located in the remote file.

    ``definitive`` is True when the file was read completely enough to know
    there is no usable index (so no other reader can do better).
    """

    def __init__(self, message, *, definitive=False):
        super().__init__(message)
        self.definitive = bool(definitive)


# Largest moov box we are willing to download for the direct snapshot path.
# A two-hour 60 fps HEVC file with audio is typically 5-15 MiB.
MOOV_MAX_BYTES = 96 * 1024 * 1024
_BOX_WALK_MAX_BOXES = 64


def _plausible_box_type(typ):
    return len(typ) == 4 and all(32 <= c < 127 or c == 0xA9 for c in typ)


def _locate_moov_by_box_walk(source, total):
    """Find moov exactly by walking the file's top-level boxes (V29.4.130).

    The same idea NoBuf (``scan_atom_layout``) and telegram-media-downloader
    (``_peekSecondAtom``) use: every top-level ISO-BMFF box starts with its
    own size, so ``ftyp`` tells us where the next box starts, an ``mdat``
    header tells us where the media data ends -- which is exactly where a
    trailing ``moov`` begins. Only the few box headers and the moov itself
    are read, whatever the file size or moov size, instead of guessing with
    fixed 4/8 MiB windows at both ends.

    Returns ``(moov_bytes, offset, boxes_seen, clean)``. ``moov_bytes`` is
    None when no moov was found; ``clean`` is True when the walk reached the
    end of the file through valid boxes (so a bounded window probe cannot
    find a moov either).
    """
    pos = 0
    seen = []
    while pos + 8 <= total and len(seen) < _BOX_WALK_MAX_BOXES:
        hdr = source.get_range_sync(pos, min(16, total - pos))
        if len(hdr) < 8:
            return None, None, seen, False
        size32 = unpack_from(">I", hdr, 0)[0]
        typ = bytes(hdr[4:8])
        header = 8
        if size32 == 1:
            if len(hdr) < 16:
                return None, None, seen, False
            size = unpack_from(">Q", hdr, 8)[0]
            header = 16
        elif size32 == 0:
            size = total - pos
        else:
            size = size32
        if not _plausible_box_type(typ) or size < header or pos + size > total:
            return None, None, seen, False
        seen.append(typ.decode("latin1"))
        if typ == b"moov":
            if size > MOOV_MAX_BYTES:
                raise MoovNotFoundError(
                    f"MP4 index is {size / 1048576:.1f} MiB, larger than the "
                    f"{MOOV_MAX_BYTES // 1048576} MiB snapshot limit",
                    definitive=True,
                )
            data = source.get_range_sync(pos, size)
            if len(data) == size:
                return data, pos, seen, True
            return None, None, seen, False
        pos += size
    return None, None, seen, pos >= total


def _load_moov(source):
    """Locate the MP4 moov atom.

    V29.4.130: the exact top-level box walk above runs first. The bounded
    window probe below is kept only as a fallback for files whose box chain
    is damaged.

    Original note -- locate the MP4 moov atom with a strict low-bandwidth probe.

    We intentionally do not progressively probe 2/4/8/16/32/64 MB on both
    ends of the file. That pattern can generate dozens of Telegram file-range
    RPCs and can saturate or destabilize a home connection. Most MP4 moov
    atoms are comfortably inside an 8 MiB tail (or front for fast-start files),
    so we probe only 4 MiB and then 8 MiB at each end.
    """
    total = int(source.size)
    if total <= 0:
        raise RuntimeError("Invalid Telegram video size")

    walked = []
    try:
        moov, offset, walked, clean = _locate_moov_by_box_walk(source, total)
    except TelegramRangeFloodError:
        raise
    except MoovNotFoundError:
        raise
    except Exception:
        moov, offset, clean = None, None, False
    if moov is not None:
        return moov, offset
    if clean:
        raise MoovNotFoundError(
            "MP4 has no moov index (top-level boxes: " + ", ".join(walked or ["none"]) + ")",
            definitive=True,
        )

    for mb in (4, 8):
        window_size = min(total, mb * 1024 * 1024)

        # Tail first: the common non-faststart MP4 layout stores moov at EOF.
        base = max(0, total - window_size)
        try:
            print(f"  Thumbnail probe: reading last {mb} MiB of video...", flush=True)
            tail = source.get_range_sync(base, window_size)
            hit = _find_moov_from_window(tail, base, total)
            if hit:
                start_off, size = hit
                if base <= start_off and start_off + size <= base + len(tail):
                    return tail[start_off - base:start_off - base + size], start_off
                if 0 < size <= 2 * 1024 * 1024:
                    return source.get_range_sync(start_off, size), start_off
        except TelegramRangeFloodError:
            raise
        except Exception:
            pass

        # Front: faststart MP4s put moov near the beginning.
        try:
            print(f"  Thumbnail probe: reading first {mb} MiB of video...", flush=True)
            front = source.get_range_sync(0, window_size)
            hit = _find_moov_from_window(front, 0, total)
            if hit:
                start_off, size = hit
                if start_off + size <= len(front):
                    return front[start_off:start_off + size], start_off
                if 0 < size <= 2 * 1024 * 1024:
                    return source.get_range_sync(start_off, size), start_off
        except TelegramRangeFloodError:
            raise
        except Exception:
            pass

    raise MoovNotFoundError(
        "Could not locate a complete MP4/MOV moov atom within the bounded 4/8 MiB front/tail probes"
        + (" (top-level boxes: " + ", ".join(walked) + ")" if walked else "")
    )


# ==============================================================================
# SECTION: MP4 sample tables
# Timescale, sample sizes, offsets and PTS.
# ==============================================================================

def _parse_mdhd_timescale(data, mdia):
    mdhd = _bmff_child(data, mdia, b"mdhd")
    if mdhd is None:
        raise RuntimeError("MP4 video track has no mdhd")
    p = _bmff_payload(data, mdhd)
    version = p[0]
    if version == 1:
        if len(p) < 32:
            raise RuntimeError("Invalid version-1 mdhd")
        return unpack_from(">I", p, 20)[0]
    if len(p) < 20:
        raise RuntimeError("Invalid mdhd")
    return unpack_from(">I", p, 12)[0]


def _parse_stts(data, stbl):
    box = _bmff_child(data, stbl, b"stts")
    if box is None:
        raise RuntimeError("MP4 video track has no stts")
    p = _bmff_payload(data, box)
    if len(p) < 8:
        raise RuntimeError("Invalid stts")
    count = unpack_from(">I", p, 4)[0]
    out = []
    off = 8
    for _ in range(count):
        if off + 8 > len(p):
            raise RuntimeError("Truncated stts")
        out.append(unpack_from(">II", p, off))
        off += 8
    return out


def _parse_ctts(data, stbl):
    box = _bmff_child(data, stbl, b"ctts")
    if box is None:
        return []
    p = _bmff_payload(data, box)
    if len(p) < 8:
        return []
    version = p[0]
    count = unpack_from(">I", p, 4)[0]
    out = []
    off = 8
    for _ in range(count):
        if off + 8 > len(p):
            break
        n, val = unpack_from(">II", p, off)
        if version == 1 and val & 0x80000000:
            val -= 1 << 32
        out.append((n, val))
        off += 8
    return out


def _parse_sync_samples(data, stbl, sample_count):
    box = _bmff_child(data, stbl, b"stss")
    if box is None:
        # No stss means every sample is a sync sample for ordinary MP4 tracks.
        return list(range(sample_count))
    p = _bmff_payload(data, box)
    if len(p) < 8:
        return list(range(sample_count))
    count = unpack_from(">I", p, 4)[0]
    out = []
    for i in range(count):
        off = 8 + i * 4
        if off + 4 > len(p):
            break
        one_based = unpack_from(">I", p, off)[0]
        if 1 <= one_based <= sample_count:
            out.append(one_based - 1)
    return out or [0]


def _parse_sample_sizes(data, stbl):
    """Read per-sample sizes from the stsz box."""
    box = _bmff_child(data, stbl, b"stsz")
    if box is not None:
        p = _bmff_payload(data, box)
        if len(p) < 12:
            raise RuntimeError("Invalid stsz")
        fixed = unpack_from(">I", p, 4)[0]
        count = unpack_from(">I", p, 8)[0]
        if fixed:
            return [int(fixed)] * int(count)
        sizes = []
        off = 12
        for _ in range(count):
            if off + 4 > len(p):
                raise RuntimeError("Truncated stsz")
            sizes.append(unpack_from(">I", p, off)[0])
            off += 4
        return sizes

    # Compact sample-size table used by some writers.
    box = _bmff_child(data, stbl, b"stz2")
    if box is None:
        raise RuntimeError("MP4 video track has no stsz/stz2")
    p = _bmff_payload(data, box)
    if len(p) < 12:
        raise RuntimeError("Invalid stz2")
    field_size = p[3]
    count = unpack_from(">I", p, 4)[0]
    sizes = []
    off = 8
    if field_size == 4:
        for i in range(count):
            if off >= len(p):
                raise RuntimeError("Truncated stz2")
            byte = p[off + i // 2]
            sizes.append((byte >> 4) & 0xF if i % 2 == 0 else byte & 0xF)
        return sizes
    if field_size == 8:
        return [int(x) for x in p[off:off + count]]
    if field_size == 16:
        for i in range(count):
            q = off + i * 2
            if q + 2 > len(p):
                raise RuntimeError("Truncated stz2")
            sizes.append(unpack_from(">H", p, q)[0])
        return sizes
    raise RuntimeError(f"Unsupported stz2 field size {field_size}")


def _parse_chunk_map(data, stbl):
    stsc = _bmff_child(data, stbl, b"stsc")
    if stsc is None:
        raise RuntimeError("MP4 video track has no stsc")
    p = _bmff_payload(data, stsc)
    count = unpack_from(">I", p, 4)[0]
    entries = []
    off = 8
    for _ in range(count):
        if off + 12 > len(p):
            raise RuntimeError("Truncated stsc")
        entries.append(unpack_from(">III", p, off))
        off += 12

    co = _bmff_child(data, stbl, b"co64") or _bmff_child(data, stbl, b"stco")
    if co is None:
        raise RuntimeError("MP4 video track has no stco/co64")
    p = _bmff_payload(data, co)
    count = unpack_from(">I", p, 4)[0]
    offsets = []
    off = 8
    if co[0] == b"co64":
        for _ in range(count):
            if off + 8 > len(p):
                raise RuntimeError("Truncated co64")
            offsets.append(unpack_from(">Q", p, off)[0])
            off += 8
    else:
        for _ in range(count):
            if off + 4 > len(p):
                raise RuntimeError("Truncated stco")
            offsets.append(unpack_from(">I", p, off)[0])
            off += 4
    return entries, offsets


def _build_sample_offsets(sizes, stsc_entries, chunk_offsets):
    sample_count = len(sizes)
    out = [0] * sample_count
    sample_index = 0
    entry_index = 0
    for chunk_no, chunk_offset in enumerate(chunk_offsets, 1):
        while entry_index + 1 < len(stsc_entries) and chunk_no >= stsc_entries[entry_index + 1][0]:
            entry_index += 1
        _, samples_per_chunk, sample_description_index = stsc_entries[entry_index]
        if sample_description_index <= 0:
            raise RuntimeError("Invalid stsc sample description index")
        cursor = int(chunk_offset)
        for _ in range(int(samples_per_chunk)):
            if sample_index >= sample_count:
                break
            out[sample_index] = cursor
            cursor += int(sizes[sample_index])
            sample_index += 1
        if sample_index >= sample_count:
            break
    if sample_index != sample_count:
        raise RuntimeError(f"Could not map all video samples to chunks ({sample_index}/{sample_count})")
    return out


def _build_pts(stts_runs, ctts_runs, sample_count):
    pts = []
    dts = 0
    cidx = 0
    cleft = ctts_runs[0][0] if ctts_runs else 0
    coff = ctts_runs[0][1] if ctts_runs else 0
    for count, delta in stts_runs:
        for _ in range(int(count)):
            if len(pts) >= sample_count:
                break
            pts.append(dts + coff)
            dts += int(delta)
            if ctts_runs:
                cleft -= 1
                if cleft <= 0:
                    cidx += 1
                    if cidx < len(ctts_runs):
                        cleft, coff = ctts_runs[cidx]
    if len(pts) != sample_count:
        raise RuntimeError(f"stts sample count mismatch ({len(pts)}/{sample_count})")
    duration_ticks = max(pts) + 1 if pts else dts
    return pts, dts, duration_ticks


# ==============================================================================
# SECTION: Codec config and GOP decode
# Parameter sets, Annex-B conversion and frame decode.
# ==============================================================================

def _parse_visual_config(moov):
    """Build a sample index and codec config from a moov atom."""
    # Locate first video trak and parse the compact MP4 sample tables.
    moov_box = next(_bmff_boxes(moov), None)
    if moov_box is None or moov_box[0] != b"moov":
        return None
    for trak in _bmff_boxes(moov, moov_box[3], moov_box[2]):
        if trak[0] != b"trak":
            continue
        mdia = _bmff_child(moov, trak, b"mdia")
        hdlr = _bmff_child(moov, mdia, b"hdlr")
        if hdlr is None:
            continue
        hp = _bmff_payload(moov, hdlr)
        if len(hp) < 12 or hp[8:12] != b"vide":
            continue
        minf = _bmff_child(moov, mdia, b"minf")
        stbl = _bmff_child(moov, minf, b"stbl")
        stsd = _bmff_child(moov, stbl, b"stsd")
        if stsd is None:
            continue
        sp = _bmff_payload(moov, stsd)
        if len(sp) < 8:
            continue
        entry_count = unpack_from(">I", sp, 4)[0]
        if not entry_count:
            continue
        first_entry = next(iter(_bmff_boxes(sp, 8, len(sp))), None)
        if first_entry is None or first_entry[0] not in _SMART_SUPPORTED_CODECS:
            return None
        codec = first_entry[0]
        width = unpack_from(">H", sp, first_entry[1] + 32)[0]
        height = unpack_from(">H", sp, first_entry[1] + 34)[0]
        cfg = _find_box_signature(sp, first_entry, {b"avcC", b"hvcC"})
        if cfg is None:
            return None
        config = sp[cfg[3]:cfg[2]]
        if codec in (b"avc1", b"avc3"):
            if len(config) < 7:
                return None
            nal_length_size = (config[4] & 3) + 1
        else:
            if len(config) < 23:
                return None
            nal_length_size = (config[21] & 3) + 1
        stts = _parse_stts(moov, stbl)
        ctts = _parse_ctts(moov, stbl)
        sizes = _parse_sample_sizes(moov, stbl)
        stsc, chunk_offsets = _parse_chunk_map(moov, stbl)
        offsets = _build_sample_offsets(sizes, stsc, chunk_offsets)
        pts, decode_duration, duration_ticks = _build_pts(stts, ctts, len(sizes))
        sync = _parse_sync_samples(moov, stbl, len(sizes))
        timescale = _parse_mdhd_timescale(moov, mdia)
        if timescale <= 0 or not offsets:
            return None
        return {
            "codec": codec,
            "codec_name": "h264" if codec in (b"avc1", b"avc3") else "hevc",
            "raw_format": "h264" if codec in (b"avc1", b"avc3") else "hevc",
            "width": int(width),
            "height": int(height),
            "config": bytes(config),
            "nal_length_size": int(nal_length_size),
            "timescale": int(timescale),
            "sizes": sizes,
            "offsets": offsets,
            "pts": pts,
            "sync": sync,
            "duration": float(decode_duration / timescale),
            "sample_count": len(sizes),
        }
    return None


def _avcc_parameter_sets(config):
    out = []
    if len(config) < 7:
        return out
    count = config[5] & 0x1F
    off = 6
    for _ in range(count):
        if off + 2 > len(config):
            break
        n = unpack_from(">H", config, off)[0]
        off += 2
        if off + n > len(config):
            break
        out.append(config[off:off + n])
        off += n
    if off < len(config):
        pps_count = config[off]
        off += 1
        for _ in range(pps_count):
            if off + 2 > len(config):
                break
            n = unpack_from(">H", config, off)[0]
            off += 2
            if off + n > len(config):
                break
            out.append(config[off:off + n])
            off += n
    return out


def _hvcc_parameter_sets(config):
    out = []
    if len(config) < 23:
        return out
    count = config[22]
    off = 23
    for _ in range(count):
        if off + 3 > len(config):
            break
        off += 1  # array_completeness + reserved + NAL unit type
        nals = unpack_from(">H", config, off)[0]
        off += 2
        for _ in range(nals):
            if off + 2 > len(config):
                break
            n = unpack_from(">H", config, off)[0]
            off += 2
            if off + n > len(config):
                break
            out.append(config[off:off + n])
            off += n
    return out


def _sample_to_annexb(sample, nal_length_size):
    out = bytearray()
    pos = 0
    nls = int(nal_length_size)
    while pos + nls <= len(sample):
        n = int.from_bytes(sample[pos:pos + nls], "big")
        pos += nls
        if n <= 0 or pos + n > len(sample):
            raise RuntimeError("Invalid MP4 NAL length")
        out += b"\x00\x00\x00\x01"
        out += sample[pos:pos + n]
        pos += n
    if pos != len(sample):
        raise RuntimeError("Invalid MP4 NAL tail")
    return bytes(out)


def _make_annexb_stream(index, blob, start_sample, end_sample):
    codec = index["codec"]
    if codec in (b"avc1", b"avc3"):
        params = _avcc_parameter_sets(index["config"])
    else:
        params = _hvcc_parameter_sets(index["config"])
    out = bytearray()
    for nal in params:
        out += b"\x00\x00\x00\x01"
        out += nal
    base = index["offsets"][start_sample]
    for i in range(start_sample, end_sample + 1):
        a = index["offsets"][i] - base
        b = a + index["sizes"][i]
        if a < 0 or b > len(blob):
            raise RuntimeError("Fetched GOP range does not contain all requested samples")
        out += _sample_to_annexb(blob[a:b], index["nal_length_size"])
    return bytes(out)


def _decode_gop_image(index, annexb, target_rank, tw, th):
    """Decode a compact GOP in memory and select the requested presentation frame.

    PyAV is preferred when it is available. The application deliberately keeps
    PyAV out of the main process on some Windows installations, so FFmpeg is
    the native fallback here. Importantly, this fallback receives only the
    compact GOP already fetched from Telegram; it does NOT seek the remote
    Telegram HTTP stream again.
    """
    if av is not None:
        container = None
        try:
            container = av.open(io.BytesIO(annexb), mode="r", format=index["raw_format"])
            chosen = None
            count = 0
            for frame in container.decode(video=0):
                if count == int(target_rank):
                    chosen = frame
                    break
                count += 1
            if chosen is None:
                for frame in container.decode(video=0):
                    chosen = frame
            if chosen is None:
                raise RuntimeError("PyAV produced no decoded frame")
            try:
                return chosen.reformat(width=tw, height=th, format="rgb24", interpolation="LANCZOS").to_image()
            except Exception:
                return chosen.to_image().resize((tw, th), Image.Resampling.LANCZOS)
        finally:
            if container is not None:
                try:
                    container.close()
                except Exception:
                    pass

    if Image is None:
        raise RuntimeError("Pillow is required for in-memory GOP thumbnail decoding")
    ffmpeg = find_app_ffmpeg()
    if not ffmpeg:
        raise RuntimeError("FFmpeg is required when PyAV is unavailable")

    raw_format = str(index.get("raw_format") or "")
    if raw_format not in {"h264", "hevc"}:
        raise RuntimeError(f"Unsupported raw codec for FFmpeg GOP decode: {raw_format}")

    rank = max(0, int(target_rank))
    # select() makes FFmpeg decode only until the requested frame is reached;
    # the input is an in-memory elementary stream, so no Telegram HTTP seeks
    # can be triggered by this operation.
    vf = f"select=eq(n\\,{rank}),scale={int(tw)}:{int(th)}:flags=lanczos"
    cmd = [
        ffmpeg, "-hide_banner", "-loglevel", "error",
        "-f", raw_format, "-i", "pipe:0",
        "-vf", vf, "-frames:v", "1",
        "-f", "image2pipe", "-vcodec", "mjpeg", "-q:v", "5",
        "-threads", "1", "pipe:1",
    ]
    try:
        proc = subprocess.run(
            cmd, input=annexb, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=30, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"FFmpeg in-memory GOP decode timeout at frame {rank}") from exc
    if proc.returncode != 0 or not proc.stdout:
        err = proc.stderr.decode("utf-8", "replace").strip()
        raise RuntimeError(err[-1600:] or f"FFmpeg GOP decode exited with code {proc.returncode}")
    try:
        with Image.open(io.BytesIO(proc.stdout)) as im:
            return im.convert("RGB").copy()
    except Exception as exc:
        raise RuntimeError("FFmpeg returned invalid JPEG for GOP frame") from exc


# ==============================================================================
# SECTION: Smart thumbnail from Telegram
# Range-based thumbnail extraction.
# ==============================================================================

def _smart_plan_target(index, target_seconds, *, keyframe_only=None):
    """Choose the samples to decode for a target time."""
    times = [int(x) for x in index["pts"]]
    target_ticks = int(max(0.0, float(target_seconds)) * index["timescale"])

    if "sorted_pts" not in index:
        order = sorted(range(index["sample_count"]), key=lambda i: times[i])
        index["sorted_pts"] = [times[i] for i in order]
        index["sorted_indices"] = order
    sorted_pts = index["sorted_pts"]
    order = index["sorted_indices"]
    k = bisect_left(sorted_pts, target_ticks)
    if k <= 0:
        target_idx = order[0]
    elif k >= len(order):
        target_idx = order[-1]
    else:
        a_idx, b_idx = order[k - 1], order[k]
        target_idx = a_idx if abs(times[a_idx] - target_ticks) <= abs(times[b_idx] - target_ticks) else b_idx

    sync = index["sync"]
    if not sync:
        raise RuntimeError("MP4 has no sync-sample table")

    use_keyframe_only = DirectTelegramRangeSource.KEYFRAME_ONLY if keyframe_only is None else bool(keyframe_only)
    if use_keyframe_only:
        # Choose the sync sample whose presentation timestamp is closest to the
        # desired target. A sync sample is independently decodable, so we do not
        # need to fetch/decode the complete GOP leading up to the target.
        sk = min(sync, key=lambda i: abs(times[i] - target_ticks))
        start_idx = end_idx = int(sk)
        rank = 0
    else:
        sk = bisect_right(sync, target_idx) - 1
        start_idx = sync[max(0, sk)]
        end_idx = min(index["sample_count"] - 1, target_idx + 8)
        selected = list(range(start_idx, end_idx + 1))
        presentation_order = sorted(selected, key=lambda i: times[i])
        rank = presentation_order.index(target_idx)

    start_byte = index["offsets"][start_idx]
    end_byte = index["offsets"][end_idx] + index["sizes"][end_idx]
    return target_idx, start_idx, end_idx, rank, int(start_byte), int(end_byte)


def _read_cached_moov_for_source(source):
    """Return ``(moov_bytes, offset, parsed_index)`` from permanent scan data.

    This helper is strictly local-only. It only reads index manifests and
    permanent byte segments written during a previous explicit scan; missing
    or partial cache data returns None so the caller can take its ordinary
    scan-time range path.
    """
    key = getattr(source, "archive_x_cache_key", None)
    size = int(getattr(source, "size", 0) or 0)
    if not key or len(key) < 2 or size <= 0:
        return None
    try:
        from ..core.playback_index_cache import read_manifest, load_segment, SEGMENT_SIZE
        manifest = read_manifest(int(key[0]), int(key[1]), size)
        if not isinstance(manifest, dict):
            return None
        moov_offset = int(manifest.get("moov_offset", -1))
        moov_size = int(manifest.get("moov_size", 0))
        if moov_offset < 0 or moov_size <= 8 or moov_offset + moov_size > size:
            return None
        first = moov_offset // int(SEGMENT_SIZE)
        last = (moov_offset + moov_size - 1) // int(SEGMENT_SIZE)
        pieces = []
        for segment_no in range(first, last + 1):
            segment = load_segment(int(key[0]), int(key[1]), size, segment_no, int(SEGMENT_SIZE))
            if segment is None:
                return None
            segment_start = segment_no * int(SEGMENT_SIZE)
            begin = max(moov_offset, segment_start)
            finish = min(moov_offset + moov_size, segment_start + len(segment))
            if finish <= begin:
                return None
            pieces.append(segment[begin - segment_start:finish - segment_start])
        blob = b"".join(pieces)
        if len(blob) != moov_size:
            return None
        parsed = _parse_visual_config(blob)
        if not parsed or not parsed.get("sync") or not parsed.get("offsets") or not parsed.get("sizes"):
            return None
        return blob, moov_offset, parsed
    except Exception:
        return None


def _smart_thumbnail_from_telegram_once(source, display_name, output_path, frames_dir=None, *, keyframe_only=None, worker_count=2, progress_callback=None, poster_callback=None, frame_width=None, frame_quality=None, prepared_index=None, prepared_moov_bytes=None, quiet=False):
    """Extract individual, video-derived JPEG frames and a representative still.

    Frames stay independently addressable; no composite image, grid, or sprite
    is created by this frame-extraction routine. MP4 range extraction uses sample tables and bounded
    workers, then persists each target frame to ``frames_dir``.
    """
    t0 = time.perf_counter()
    if prepared_index is None:
        # Prefer the MP4 moov atom saved by an earlier Metadata & Assets scan.
        # Re-parse its local sample tables rather than requesting the index
        # ranges from Telegram again. If the saved cache is absent/incomplete,
        # use the normal bounded moov reader below.
        cached_index = _read_cached_moov_for_source(source)
        if cached_index is not None:
            moov_bytes, moov_offset, index = cached_index
        else:
            moov_bytes, moov_offset = _load_moov(source)
            index = _parse_visual_config(moov_bytes)
    else:
        index = prepared_index
        moov_bytes = bytes(prepared_moov_bytes or b"")
        moov_offset = 0
    if not index:
        return False

    width, height = index["width"], index["height"]
    duration = float(index["duration"])
    if width <= 0 or height <= 0 or duration <= 0:
        return False
    # If the sync-sample/sample-byte tables are incomplete, let the caller take
    # its existing FFmpeg/container fallback instead of issuing speculative
    # keyframe range requests.
    if keyframe_only is True:
        try:
            if (not index.get("sync") or not index.get("offsets") or not index.get("sizes")
                    or not index.get("pts") or len(index["offsets"]) != len(index["sizes"])
                    or len(index["pts"]) != len(index["sizes"])):
                return False
        except Exception:
            return False

    # Metadata & Assets scans already have the MP4's moov atom and sample tables
    # in memory. Persist those index/start ranges before frame decoding so the
    # actual Play request can use them without immediately re-downloading the
    # MP4 index. This writes only to the dedicated permanent cache, not the
    # releasable per-play sparse cache.
    try:
        from ..core.playback_index_cache import persist_index_segments
        persist_index_segments(source, getattr(source, "archive_x_cache_key", None),
                               index, moov_bytes, moov_offset)
    except Exception:
        pass

    frame_jpeg_quality = JPEG_QUALITY if frame_quality is None else max(50, min(100, int(frame_quality)))
    targets = target_times(duration)
    if len(targets) != COUNT:
        raise RuntimeError(f"Snapshot target count is {len(targets)}; expected {COUNT}")

    plans = []
    for idx, target in enumerate(targets):
        try:
            plan = _smart_plan_target(index, target, keyframe_only=keyframe_only)
            plans.append((idx, target, plan))
        except Exception as exc:
            if keyframe_only is True:
                # Missing/inconsistent MP4 sample tables are an expected
                # reason to use the container fallback, not a failed 36-frame
                # keyframe scan.
                return False
            raise RuntimeError(f"Could not plan snapshot frame {idx + 1}: {exc}") from exc

    if not quiet:
        print("Individual MP4 snapshot extraction enabled.")
        print("36 independent video-derived frames sampled across the full duration.")
        print("No Telegram preview images are used. No full video download is created.")
        print(f"Metadata: {human_size(len(moov_bytes))} moov | {width}x{height} | {index['codec_name']} | {duration/60:.2f} min")
    worker_count = max(1, min(4, int(worker_count or 2)))
    if not quiet:
        print(f"36 targets -> up to {worker_count} concurrent keyframe fetch/decode workers")

    results = [None] * COUNT
    failures = []
    decoded_cache = {}
    decoded_cache_lock = threading.Lock()

    def job(item):
        idx, target, plan = item
        _, start_idx, end_idx, rank, start_byte, end_byte = plan
        try:
            # Multiple targets can resolve to the same keyframe on low-keyframe-
            # density media. Reuse the decoded image instead of doing duplicate
            # Telegram reads and duplicate HEVC/H.264 decode work.
            cache_key = (int(start_idx), int(end_idx), int(rank))
            with decoded_cache_lock:
                cached_img = decoded_cache.get(cache_key)
            if cached_img is not None:
                return idx, cached_img.copy(), 0, 0, None

            blob = source.get_range_sync(start_byte, end_byte - start_byte)
            annexb = _make_annexb_stream(index, blob, start_idx, end_idx)
            tw, th = target_size(width, height, frame_width=frame_width)
            img = _decode_gop_image(index, annexb, rank, tw, th)
            with decoded_cache_lock:
                decoded_cache[cache_key] = img.copy()
            return idx, img, len(blob), end_byte - start_byte, None
        except Exception as exc:
            return idx, None, 0, end_byte - start_byte, f"{type(exc).__name__}: {exc}"

    worker_count = min(worker_count, COUNT)
    done = 0
    total_payload = 0
    poster_published = False

    def publish_provisional_poster(image):
        nonlocal poster_published
        if poster_published or poster_callback is None or image is None:
            return
        try:
            published = poster_callback(image.copy())
            poster_published = published is not False
        except Exception as exc:
            # Early poster publication is an optimization; a failure must not
            # invalidate the full 36-frame extraction or block later frames.
            if not quiet:
                print(f"Provisional poster publish failed: {type(exc).__name__}: {exc}")

    with ThreadPoolExecutor(max_workers=worker_count) as ex:
        futures = [ex.submit(job, item) for item in plans]
        for fut in as_completed(futures):
            idx, img, received, requested, err = fut.result()
            done += 1
            total_payload += received
            if img is not None:
                results[idx] = img
                publish_provisional_poster(img)
                if progress_callback is not None:
                    try:
                        progress_callback(sum(result is not None for result in results), COUNT)
                    except Exception:
                        pass
            else:
                failures.append((idx, err))
            if not quiet:
                print(
                    f"\rFrames: {done:2d}/{COUNT} | Telegram video data: {human_size(total_payload):>9} | "
                    f"Elapsed: {time.perf_counter() - t0:5.1f}s",
                    end="",
                    flush=True,
                )
    if not quiet:
        print()

    if failures:
        if not quiet:
            print(f"Snapshot decoder failures: {len(failures)} frame(s). Retrying once...")
        for idx, err in failures:
            try:
                item = plans[idx]
                _, _, plan = item
                _, start_idx, end_idx, rank, start_byte, end_byte = plan
                cache_key = (int(start_idx), int(end_idx), int(rank))
                with decoded_cache_lock:
                    cached_img = decoded_cache.get(cache_key)
                if cached_img is not None:
                    results[idx] = cached_img.copy()
                    if progress_callback is not None:
                        try:
                            progress_callback(sum(result is not None for result in results), COUNT)
                        except Exception:
                            pass
                    continue
                blob = source.get_range_sync(start_byte, end_byte - start_byte)
                annexb = _make_annexb_stream(index, blob, start_idx, end_idx)
                tw, th = target_size(width, height, frame_width=frame_width)
                results[idx] = _decode_gop_image(index, annexb, rank, tw, th)
                publish_provisional_poster(results[idx])
                if progress_callback is not None:
                    try:
                        progress_callback(sum(result is not None for result in results), COUNT)
                    except Exception:
                        pass
            except Exception as exc:
                if not quiet:
                    print(f"Frame {idx + 1} retry failed: {type(exc).__name__}: {exc}")

    good = sum(img is not None for img in results)
    # The Archive X frame set is atomic and always contains exactly 36 target
    # timestamps. Accepting 85% here caused the extractor to claim partial
    # success before the later write loop failed (or, without a frame directory,
    # publish a representative image from an incomplete set).
    if good != COUNT:
        missing = [str(idx + 1) for idx, image in enumerate(results) if image is None]
        preview = ", ".join(missing[:8]) + ("…" if len(missing) > 8 else "")
        raise RuntimeError(f"Snapshot extraction incomplete: {good}/{COUNT} frames; missing frame indexes: {preview}")

    # Publish only independent JPEGs; failed targets are retried above and are
    # never filled with a duplicate neighbour frame.
    rep = results[COUNT // 2]
    if rep is None:
        raise RuntimeError("The representative snapshot frame could not be generated")
    if frames_dir is not None:
        frame_root = Path(frames_dir)
        frame_root.mkdir(parents=True, exist_ok=True)
        for old_frame in frame_root.glob("frame_*.jpg"):
            old_frame.unlink(missing_ok=True)
        for idx, img in enumerate(results):
            if img is None:
                raise RuntimeError(f"Snapshot frame {idx + 1} is unavailable")
            img.save(frame_root / f"frame_{idx + 1:02d}.jpg", "JPEG", quality=frame_jpeg_quality, optimize=False)
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    rep.save(output_path, "JPEG", quality=frame_jpeg_quality, optimize=False)

    elapsed = time.perf_counter() - t0
    if not quiet:
        print(f"Individual snapshots captured: {good}/{COUNT} frames")
        print(f"Telegram video data received: {human_size(source.bytes_received)}")
        print(f"Telegram range RPCs: {source.rpc_requests}")
        print(f"Snapshot capture time: {elapsed:.2f}s")
        print(f"Representative snapshot saved: {output_path}")
    return True


def _snapshot_frame_diversity(frames_dir):
    """Count visually distinct saved frames, ignoring JPEG metadata/noise."""
    try:
        from pathlib import Path
        from PIL import Image
        paths=sorted(Path(frames_dir).glob("frame_*.jpg"))
        fingerprints=[]
        for path in paths:
            with Image.open(path) as image:
                sample=image.convert("RGB").resize((24,14),Image.Resampling.BILINEAR)
                fingerprints.append(__import__("hashlib").sha256(sample.tobytes()).digest())
        return len(paths),len(set(fingerprints))
    except Exception:
        return 0,0


def _smart_thumbnail_from_telegram(source, display_name, output_path, frames_dir=None, *, keyframe_only=None, worker_count=2, progress_callback=None, poster_callback=None, frame_width=None, frame_quality=None):
    """Build snapshots from indexed sync-sample bytes when keyframe-only mode is enabled.

    Do not silently fall back to GOP reads in indexed keyframe-only mode: the
    caller's container fallback handles files without a usable sample table.
    """
    from pathlib import Path
    import os
    import shutil
    initial = _smart_thumbnail_from_telegram_once(
        source, display_name, output_path, frames_dir=frames_dir,
        keyframe_only=keyframe_only, worker_count=worker_count,
        progress_callback=progress_callback, poster_callback=poster_callback,
        frame_width=frame_width, frame_quality=frame_quality,
    )
    if not initial or keyframe_only is True:
        return initial
    if frames_dir is None:
        return initial
    frame_count, unique_count = _snapshot_frame_diversity(frames_dir)
    # Trigger only on a strong collapse, not naturally repetitive footage.
    threshold=max(4,int(frame_count*0.25))
    if frame_count < 8 or unique_count > threshold:
        return initial

    base_frames=Path(frames_dir)
    base_output=Path(output_path)
    retry_frames=base_frames.with_name(base_frames.name+".gop-retry")
    retry_output=base_output.with_name(base_output.stem+".gop-retry"+base_output.suffix)
    shutil.rmtree(retry_frames,ignore_errors=True)
    retry_output.unlink(missing_ok=True)
    try:
        retried=_smart_thumbnail_from_telegram_once(
            source, display_name, str(retry_output), frames_dir=str(retry_frames),
            keyframe_only=False, worker_count=worker_count,
            progress_callback=progress_callback, poster_callback=poster_callback,
            frame_width=frame_width, frame_quality=frame_quality,
        )
        retry_count,retry_unique=_snapshot_frame_diversity(retry_frames)
        if retried and retry_count>=frame_count and retry_unique>unique_count:
            shutil.rmtree(base_frames,ignore_errors=True)
            os.replace(str(retry_frames),str(base_frames))
            os.replace(str(retry_output),str(base_output))
            print(f"Preview frame diversity recovered: {unique_count}/{frame_count} -> {retry_unique}/{retry_count} distinct frames")
            return True
        print(f"Preview GOP fallback did not improve diversity ({unique_count}/{frame_count} -> {retry_unique}/{retry_count}); retaining first usable snapshot set")
        return initial
    except Exception as exc:
        print(f"Preview GOP fallback failed; retaining first usable snapshot set: {type(exc).__name__}: {exc}")
        return initial
    finally:
        shutil.rmtree(retry_frames,ignore_errors=True)
        retry_output.unlink(missing_ok=True)


# ==============================================================================
# SECTION: Local HTTP range bridge
# Temporary HTTP server exposing Telegram ranges to FFmpeg.
# ==============================================================================

class RangeHandler(BaseHTTPRequestHandler):
    """VLC-style seekable HTTP byte-range endpoint.

    VLC's HTTP access code treats HTTP as a seekable file when the server
    advertises Accept-Ranges: bytes and answers seeks with 206 + Content-Range.
    We mirror that behavior here.  The server is bound to 127.0.0.1 only and
    exists only for the duration of thumbnail generation.
    """
    server_version = "TelegramVLCBridge/1.0"
    protocol_version = "HTTP/1.0"
    MAX_RESPONSE = 16 * 1024 * 1024

    def log_message(self, fmt, *args):
        pass

    def _send_range(self, start, end, status=206):
        src = self.server.source
        if start < 0 or start >= src.size or end < start:
            self.send_response(416)
            self.send_header("Content-Range", f"bytes */{src.size}")
            self.send_header("Connection", "close")
            self.end_headers()
            return

        max_response = max(1024, int(getattr(self.server, "max_response", self.MAX_RESPONSE) or self.MAX_RESPONSE))
        end = min(end, src.size - 1, start + max_response - 1)
        length = end - start + 1

        # Fetch outside the HTTP header write so a Telegram failure cannot
        # leave FFmpeg waiting for a malformed response.
        payload = src.get_range_sync(start, length)
        if not payload:
            raise RuntimeError(f"empty Telegram range {start}-{end}")
        end = start + len(payload) - 1

        self.send_response(status)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Type", src.mime)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Content-Range", f"bytes {start}-{end}/{src.size}")
        self.send_header("Connection", "close")
        self.end_headers()
        try:
            self.wfile.write(payload)
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
            return

    def do_HEAD(self):
        src = self.server.source
        if src.size <= 0:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Type", src.mime)
        self.send_header("Content-Length", str(src.size))
        self.send_header("Connection", "close")
        self.end_headers()

    def do_GET(self):
        """Serve the Telegram source to a local client over HTTP ranges."""
        src = self.server.source
        if src.size <= 0:
            self.send_error(404)
            return

        rh = self.headers.get("Range", "")
        if not rh:
            # Sequential mode intentionally streams the video through the local
            # decoder. Nothing is written to disk; the Telegram range cache is
            # bounded by TelegramRangeSource.CACHE_BYTES and old chunks are
            # evicted as the stream advances.
            if self.path.rstrip("/").endswith("/stream"):
                try:
                    self.send_response(200)
                    self.send_header("Accept-Ranges", "none")
                    self.send_header("Content-Type", src.mime)
                    self.send_header("Content-Length", str(src.size))
                    self.send_header("Connection", "close")
                    self.end_headers()
                    total = (src.size + src.CHUNK - 1) // src.CHUNK
                    for idx in range(total):
                        data = src._get_chunk_sync(idx)
                        if not data:
                            break
                        self.wfile.write(data)
                        self.wfile.flush()
                    return
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
                    return
                except Exception as exc:
                    print(f"HTTP STREAM ERROR: {type(exc).__name__}: {exc}", flush=True)
                    return

            # Legacy/range endpoint: serve only a bounded first window.
            try:
                max_response = max(1024, int(getattr(self.server, "max_response", self.MAX_RESPONSE) or self.MAX_RESPONSE))
                self._send_range(0, min(src.size - 1, max_response - 1), 206)
            except Exception:
                try:
                    self.send_error(502, "Telegram range backend failed")
                except Exception:
                    pass
            return

        m = re.match(r"^bytes=(\d+)-(\d*)$", rh.strip())
        if not m:
            self.send_response(416)
            self.send_header("Content-Range", f"bytes */{src.size}")
            self.send_header("Connection", "close")
            self.end_headers()
            return

        start = int(m.group(1))
        requested_end = int(m.group(2)) if m.group(2) else src.size - 1
        end = min(requested_end, src.size - 1)
        try:
            self._send_range(start, end, 206)
        except Exception as exc:
            print(f"HTTP RANGE ERROR {start}-{end}: {type(exc).__name__}: {exc}", flush=True)
            try:
                self.send_error(502, "Telegram range backend failed")
            except Exception:
                pass


class TelegramHTTPServer(ThreadingHTTPServer):
    """Threaded local HTTP server for one Telegram range source."""
    daemon_threads = True
    allow_reuse_address = True

    def handle_error(self, request, client_address):
        # FFmpeg/PyAV can close a persistent HTTP connection immediately
        # after receiving enough bytes for a seek. Windows reports this as
        # WinError 10054. It is a normal client-side cancellation.
        exc = sys.exc_info()[1]
        if isinstance(exc, (ConnectionResetError, BrokenPipeError, ConnectionAbortedError, TimeoutError, OSError)):
            winerr = getattr(exc, "winerror", None)
            errno = getattr(exc, "errno", None)
            # Expected client-side disconnects/cancellations on Windows.
            if winerr in (64, 995, 10053, 10054) or errno in (54, 104, 103):
                return
            if isinstance(exc, (ConnectionResetError, BrokenPipeError, ConnectionAbortedError, TimeoutError)):
                return
        super().handle_error(request, client_address)


def start_temp_http_server(source, *, max_response: int | None = None):
    """Start a localhost byte-range server; per-server limits leave other users unchanged."""
    server = TelegramHTTPServer(("127.0.0.1", 0), RangeHandler)
    server.source = source
    if max_response is not None:
        server.max_response = max(1024, int(max_response))
    thread = threading.Thread(
        target=server.serve_forever,
        name="TelegramThumbHTTP",
        daemon=True,
    )
    thread.start()
    host, port = server.server_address
    url = f"http://127.0.0.1:{port}/video"
    return server, thread, url


# ==============================================================================
# SECTION: Target planning and stream decode
# Picks capture times and decodes targets from a stream.
# ==============================================================================

def target_times(duration):
    return [duration * (0.02 + 0.96 * i / (COUNT - 1)) for i in range(COUNT)] if duration > 0 else []


def target_size(width, height, frame_width=None):
    if frame_width is None:
        tw = max(1, int((OUT_WIDTH - PADDING * (COLS - 1)) / COLS))
    else:
        # Snapshot thumbnails use the requested independent-frame width;
        # contact-sheet and live seek-preview paths keep their separate smaller sizes.
        tw = min(max(1, int(frame_width)), max(1, int(width)))
    th = max(1, int(tw * height / max(1, width)))
    return tw, th


# ==============================================================================
# SECTION: FFmpeg snapshot helpers
# Filter escaping, remote probing, capture and validation.
# ==============================================================================

def _ffprobe_remote_video_metadata(ffprobe: str, url: str) -> dict:
    """Probe a Telegram range-backed HTTP URL using the external ffprobe."""
    cmd = [
        ffprobe, "-v", "error", "-print_format", "json",
        "-show_streams", "-show_format", url,
    ]
    proc = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=20,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if proc.returncode != 0:
        err = proc.stderr.decode("utf-8", "replace").strip()
        raise RuntimeError(err[-1200:] or f"ffprobe exited with code {proc.returncode}")
    try:
        data = json.loads(proc.stdout.decode("utf-8", "replace") or "{}")
    except Exception as exc:
        raise RuntimeError("ffprobe returned invalid JSON") from exc
    streams = data.get("streams", []) or []
    video = next((s for s in streams if s.get("codec_type") == "video"), {})
    fmt = data.get("format", {}) or {}
    width = int(video.get("width") or 0)
    height = int(video.get("height") or 0)
    codec = str(video.get("codec_name") or "unknown")
    rate = str(video.get("avg_frame_rate") or video.get("r_frame_rate") or "0/1")
    fps = 0.0
    try:
        if "/" in rate:
            a, b = rate.split("/", 1)
            if float(b or 0):
                fps = float(a) / float(b)
        else:
            fps = float(rate or 0)
    except Exception:
        fps = 0.0
    duration = 0.0
    try:
        duration = float(video.get("duration") or fmt.get("duration") or 0.0)
    except Exception:
        duration = 0.0
    start_time = 0.0
    try:
        start_time = float(video.get("start_time") or fmt.get("start_time") or 0.0)
    except Exception:
        start_time = 0.0
    if width <= 0 or height <= 0 or duration <= 0:
        raise RuntimeError("ffprobe did not return usable video dimensions/duration")
    return {
        "width": width,
        "height": height,
        "codec": codec,
        "fps": fps,
        "duration": duration,
        "start_time": start_time,
        # Container identification from probe data is more reliable than the
        # filename alone (Telegram uploads are often renamed or extensionless).
        "format_name": str(fmt.get("format_name") or "").strip().lower(),
    }


def _ffmpeg_snapshot_frames_file(source, display_name, output_path, frames_dir=None, metadata=None,
                                 worker_count=4, progress_callback=None, frame_width=None, frame_quality=95,
                                 control=None, poster_callback=None):
    """Extract independently addressable timeline frames with FFmpeg fallback.

    This function intentionally never tiles or merges images. Each target must
    produce its own validated JPEG. Failed captures are retried at the target
    timestamp and are not replaced by copies of neighbouring frames.
    """
    server = thread = None
    cleanup_frames = False
    try:
        # Telegram document uploads often have a generic MIME type and no
        # DocumentAttributeVideo, so duration/width/height can all be zero even
        # though the payload is a valid MKV/AVI/WebM/TS/etc. Only trust supplied
        # metadata when it is complete; otherwise probe the range-backed source.
        metadata_complete = isinstance(metadata, dict) and (
            float(metadata.get("duration") or 0) > 0
            and int(metadata.get("width") or 0) > 0
            and int(metadata.get("height") or 0) > 0
        )
        # Container timestamps are not always zero-based (MPEG-TS is a common
        # example). Trust supplied metadata only when its timestamp origin was
        # probed too; Telegram file attributes do not include start_time.
        supplied = metadata_complete and "start_time" in metadata
        ffmpeg = find_app_ffmpeg()
        ffprobe = find_app_ffprobe() if not supplied else None
        if not ffmpeg or (not supplied and not ffprobe):
            raise RuntimeError("FFmpeg and ffprobe are required to generate snapshots when source metadata is incomplete or container timing must be resolved")
        server, thread, url = start_temp_http_server(source, max_response=2 * 1024 * 1024)
        if supplied:
            meta = {"duration": float(metadata.get("duration") or 0),
                    "width": int(metadata.get("width") or 0),
                    "height": int(metadata.get("height") or 0),
                    "codec": str(metadata.get("codec") or "unknown"),
                    "fps": float(metadata.get("fps") or 0)}
        else:
            meta = _ffprobe_remote_video_metadata(ffprobe, url)
            if isinstance(metadata, dict):
                metadata.update(meta)
        duration = float(meta["duration"])
        if duration <= 0 or int(meta["width"]) <= 0 or int(meta["height"]) <= 0:
            raise RuntimeError("video metadata is not usable for snapshot generation")
        targets = target_times(duration)
        if len(targets) != COUNT:
            raise RuntimeError(f"Snapshot target count is {len(targets)}; expected {COUNT}")
        # The keyframe-only fast extractor is tuned for MP4-family keyframe
        # indexing. In AVI/WebM/TS/other containers it can repeatedly select the
        # same initial keyframe for several early timestamps, especially short
        # videos. Use accurate timestamp seeks for those formats instead.
        # Prefer the Telegram filename when the caller's display title has no
        # extension, and use ffprobe's container signature when the filename is
        # generic or misleading.
        source_name = str(display_name or "").strip()
        source_suffix = Path(source_name).suffix.lower()
        if not source_suffix:
            try:
                telegram_name = str(getattr(getattr(getattr(source, "message", None), "file", None), "name", "") or "").strip()
                if telegram_name:
                    source_name = telegram_name
                    source_suffix = Path(source_name).suffix.lower()
            except Exception:
                pass
        probed_format = str(meta.get("format_name") or "").lower().split(",")
        is_mpegts = source_suffix in {".ts", ".mts", ".m2ts", ".m2t"} or any(
            item.strip() == "mpegts" for item in probed_format
        )
        is_mp4_family = source_suffix in {".mp4", ".mov", ".m4v"} or any(
            item.strip() in {"mov", "mp4", "m4a", "3gp", "3g2", "mj2"} for item in probed_format
        )
        exact_timestamp_mode = not is_mp4_family
        forced_input_format = "mpegts" if is_mpegts else None
        if frames_dir is None:
            frame_dir = Path(output_path).with_name(Path(output_path).stem + ".frames")
            cleanup_frames = True
        else:
            frame_dir = Path(frames_dir)
        frame_dir.mkdir(parents=True, exist_ok=True)
        for old in frame_dir.glob("frame_*.jpg"):
            old.unlink(missing_ok=True)

        # The player grid uses individual frames, not contact-sheet-sized cells.
        # Use a bounded higher-resolution source frame and preserve aspect ratio.
        tw, th = target_size(int(meta["width"]), int(meta["height"]), frame_width=frame_width)
        quality = max(50, min(100, int(frame_quality or 95)))
        ffmpeg_q = "2" if quality >= 94 else "3" if quality >= 90 else "5"
        workers = max(1, min(2, int(worker_count or 1)))
        captured = [False] * COUNT
        errors = []
        poster_published = False
        def capture_one(item):
            if control is not None:
                control.check()
            idx, target = item
            frame_path = frame_dir / f"frame_{idx + 1:02d}.jpg"
            try:
                if exact_timestamp_mode:
                    _ffmpeg_capture_exact_target_file(ffmpeg, url, target, tw, th, frame_path,
                                                      quality=quality, qscale=ffmpeg_q, control=control,
                                                      seek_timestamp=True, input_format=forced_input_format)
                else:
                    _ffmpeg_capture_target_file(ffmpeg, url, target, tw, th, frame_path,
                                                quality=quality, qscale=ffmpeg_q, control=control)
                _validate_snapshot_jpeg(frame_path)
                return idx, True, None
            except Exception as first_exc:
                if control is not None and control.cancelled.is_set():
                    raise
                try:
                    _ffmpeg_capture_exact_target_file(ffmpeg, url, target, tw, th, frame_path,
                                                      quality=quality, qscale=ffmpeg_q, control=control,
                                                      seek_timestamp=exact_timestamp_mode, input_format=forced_input_format)
                    _validate_snapshot_jpeg(frame_path)
                    return idx, True, None
                except Exception as retry_exc:
                    if control is not None and control.cancelled.is_set():
                        raise
                    frame_path.unlink(missing_ok=True)
                    return idx, False, f"{type(first_exc).__name__}: {first_exc}; retry {type(retry_exc).__name__}: {retry_exc}"
        if forced_input_format == "mpegts":
            # MPEG-TS random byte-range seeks are unreliable because TS packets
            # must be resynchronized and often carry non-zero PTS. Stream it once
            # sequentially and let FFmpeg's fps filter produce the evenly spaced
            # 36 frames. This avoids 36 failed/duplicated seeks and reads the file
            # only once from Telegram.
            stream_url = url.rsplit("/", 1)[0] + "/stream"
            sample_rate = max(0.001, float(COUNT) / max(0.1, duration))
            sequence_pattern = str(frame_dir / "frame_%02d.jpg")
            cmd = [
                ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin",
                "-f", "mpegts", "-i", stream_url, "-an", "-sn",
                "-vf", f"fps=fps={sample_rate:.12g},scale={int(tw)}:{int(th)}:flags=lanczos,format=yuvj420p",
                "-frames:v", str(COUNT), "-q:v", str(ffmpeg_q), "-start_number", "1",
                "-y", sequence_pattern,
            ]
            proc = _run_snapshot_ffmpeg(cmd, timeout=900, control=control)
            if proc.returncode != 0:
                err = proc.stderr.decode("utf-8", "replace").strip()
                raise RuntimeError(err[-1200:] or f"FFmpeg sequential MPEG-TS extraction exited with code {proc.returncode}")
            for idx in range(COUNT):
                frame_path = frame_dir / f"frame_{idx + 1:02d}.jpg"
                _validate_snapshot_jpeg(frame_path)
                captured[idx] = True
            if progress_callback is not None:
                try:
                    progress_callback(COUNT, COUNT)
                except Exception:
                    pass
            if poster_callback is not None:
                try:
                    from PIL import Image
                    with Image.open(frame_dir / f"frame_{COUNT // 2 + 1:02d}.jpg") as frame:
                        poster_callback(frame.convert("RGB").copy())
                except Exception as exc:
                    _LOG.info("Sequential MPEG-TS poster publish failed for %s: %s: %s",
                              display_name, type(exc).__name__, str(exc)[:240])
        else:
            with ThreadPoolExecutor(max_workers=workers) as ex:
                # Run targets in batches of four. If the first four all fail, do not
                # queue 32 more seeks for a video whose decoder/range path is broken.
                first_batch_failures = 0
                for batch_start in range(0, COUNT, 4):
                    batch = list(enumerate(targets[batch_start:batch_start + 4], start=batch_start))
                    futures = [ex.submit(capture_one, item) for item in batch]
                    for fut in as_completed(futures):
                        idx, ok, err = fut.result()
                        captured[idx] = bool(ok)
                        if ok and not poster_published and poster_callback is not None:
                            try:
                                from PIL import Image
                                with Image.open(frame_dir / f"frame_{idx + 1:02d}.jpg") as frame:
                                    published = poster_callback(frame.convert("RGB").copy())
                                poster_published = published is not False
                            except Exception as exc:
                                _LOG.info("Provisional poster publish failed for %s: %s: %s",
                                          display_name, type(exc).__name__, str(exc)[:240])
                        if not ok:
                            errors.append((idx, err))
                            short_reason = " ".join(str(err or "capture failed").split())[:300]
                            _LOG.info("Snapshot frame %02d/36 failed for %s: %s", idx + 1, display_name, short_reason)
                        elif progress_callback is not None:
                            # A failed attempt is not activity and must not reset
                            # the snapshot watchdog. Only validated JPEGs count.
                            try:
                                progress_callback(sum(captured), COUNT)
                            except Exception:
                                pass
                    if batch_start == 0 and not any(captured[:4]):
                        first_batch_failures = len([1 for idx in range(4) if not captured[idx]])
                        raise RuntimeError(
                            f"Snapshot extraction stopped after first {first_batch_failures} targets failed; "
                            f"first failure: {next((err for idx, err in errors if idx < 4), 'unknown error')}"
                        )
        good = sum(captured)
        if good != COUNT:
            raise RuntimeError(f"Only {good}/{COUNT} individual snapshot frames could be captured; {len(errors)} failed")
        # Reject complete collapse from bad time seeking. A genuinely static
        # video may have repeated frames, so use a conservative diversity floor.
        unique = _count_distinct_jpegs(frame_dir)
        if unique < 4:
            raise RuntimeError(f"Snapshot frame extraction collapsed to only {unique} distinct frames")
        middle = frame_dir / f"frame_{COUNT // 2 + 1:02d}.jpg"
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        if Path(output_path).resolve(strict=False) != middle.resolve(strict=False):
            import shutil
            shutil.copyfile(middle, output_path)
        print(f"Individual FFmpeg snapshots created: {good}/{COUNT}; {unique} distinct; {Path(output_path).name}")
        return Path(output_path)
    finally:
        if cleanup_frames:
            try:
                import shutil
                shutil.rmtree(frame_dir, ignore_errors=True)
            except Exception:
                pass
        if server is not None:
            try: server.shutdown()
            except Exception: pass
            try: server.server_close()
            except Exception: pass


def _validate_snapshot_jpeg(path):
    from PIL import Image
    p = Path(path)
    if not p.is_file() or p.stat().st_size < 256:
        raise RuntimeError("snapshot JPEG is missing or too small")
    with Image.open(p) as image:
        image.verify()


def _count_distinct_jpegs(frames_dir):
    from hashlib import sha256
    from PIL import Image
    signatures = set()
    for p in sorted(Path(frames_dir).glob("frame_*.jpg")):
        try:
            with Image.open(p) as image:
                sample = image.convert("RGB").resize((24, 14), Image.Resampling.BILINEAR)
                signatures.add(sha256(sample.tobytes()).digest())
        except Exception:
            continue
    return len(signatures)


def _ffmpeg_capture_exact_target_file(ffmpeg, http_url, target, tw, th, output_path, *, quality=90, qscale=None,
                                     control=None, seek_timestamp=False, input_format=None):
    """Accurately decode a requested timestamp; optionally set the input demuxer."""
    target = max(0.0, float(target))
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin"]
    if input_format:
        cmd.extend(["-f", str(input_format)])
    if seek_timestamp:
        # MPEG-TS can start with non-zero PTS. Treat -ss as a media timestamp
        # instead of letting FFmpeg add the container start_time again.
        cmd.extend(["-seek_timestamp", "1"])
    cmd.extend(["-ss", f"{target:.3f}", "-i", http_url, "-an", "-sn", "-frames:v", "1", "-vf",
           f"scale={int(tw)}:{int(th)}:flags=lanczos,format=yuvj420p", "-q:v", str(qscale or ("2" if int(quality) >= 94 else "3" if int(quality) >= 90 else "5")), "-y", str(output_path)])
    proc = _run_snapshot_ffmpeg(cmd, timeout=45, control=control)
    if proc.returncode != 0:
        err = proc.stderr.decode("utf-8", "replace").strip()
        raise RuntimeError(err[-1200:] or f"FFmpeg exited with code {proc.returncode}")

def _ffmpeg_capture_target_file(ffmpeg, http_url, target, tw, th, output_path, *, quality=90, qscale=None,
                                control=None):
    """Capture a fast, keyframe-near JPEG for one independent video snapshot.

    The cache stores individual frame files, not a timeline sheet. HEVC random
    access is expensive when the decoder must reconstruct a long GOP, so seek
    slightly before the target and decode only keyframes. This bounds the cost
    of snapshot generation on high-bitrate 1080p/4K media. Live seek previews
    remain a separate playback path.
    """
    target = max(0.0, float(target))
    anchor = max(0.0, target - 5.0)
    cmd = [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin",
        "-hwaccel", "auto",
        "-ss", f"{anchor:.3f}",
        "-skip_frame", "nokey",
        "-probesize", "1M", "-analyzeduration", "500000",
        "-i", http_url, "-an", "-sn", "-frames:v", "1",
        "-vf", f"scale={int(tw)}:{int(th)}:flags=lanczos",
        "-q:v", str(qscale or ("2" if int(quality) >= 94 else "3" if int(quality) >= 90 else "5")), "-y", str(output_path),
    ]
    try:
        proc = _run_snapshot_ffmpeg(cmd, timeout=45, control=control)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"FFmpeg timeout at {target:.2f}s") from exc
    if proc.returncode != 0 or not Path(output_path).is_file() or Path(output_path).stat().st_size <= 0:
        err = proc.stderr.decode("utf-8", "replace").strip()
        raise RuntimeError(err[-1200:] or f"FFmpeg exited with code {proc.returncode}")
    return Path(output_path)


def _run_snapshot_ffmpeg(cmd, *, timeout=45, control=None):
    """Run a snapshot FFmpeg process that the watchdog can terminate promptly."""
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    if control is None:
        return subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout,
                              creationflags=flags)
    process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=flags)
    deadline = time.monotonic() + max(0.1, float(timeout))
    while True:
        if getattr(getattr(control, "cancelled", None), "is_set", lambda: False)():
            try:
                process.kill()
            except Exception:
                pass
            try:
                process.communicate(timeout=5)
            except Exception:
                pass
            raise RuntimeError("Snapshot FFmpeg stopped by the per-video watchdog")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            try:
                process.kill()
            except Exception:
                pass
            try:
                process.communicate(timeout=5)
            except Exception:
                pass
            raise subprocess.TimeoutExpired(cmd, timeout)
        try:
            stdout, stderr = process.communicate(timeout=min(0.25, remaining))
            return subprocess.CompletedProcess(cmd, process.returncode, stdout, stderr)
        except subprocess.TimeoutExpired:
            continue


def _ffmpeg_single_still(source, display_name, output_path, *, target_seconds=None, metadata=None):
    """Create one representative still from a Telegram-backed media source."""
    server = thread = None
    try:
        ffmpeg = find_app_ffmpeg()
        ffprobe = find_app_ffprobe() if metadata is None else None
        if not ffmpeg:
            raise RuntimeError("FFmpeg is required for thumbnail generation")
        server, thread, url = start_temp_http_server(source)
        duration = float((metadata or {}).get("duration") or 0)
        if duration <= 0 and ffprobe:
            probe = _ffprobe_remote_video_metadata(ffprobe, url)
            duration = float(probe.get("duration") or 0)
        target = max(0.0, float(target_seconds if target_seconds is not None else (duration * 0.35 if duration > 0 else 1.0)))
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-ss", f"{target:.3f}",
               "-i", url, "-an", "-sn", "-frames:v", "1", "-vf", "scale=640:360:force_original_aspect_ratio=decrease",
               "-q:v", "5", "-y", str(out)]
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if proc.returncode != 0 or not out.is_file() or out.stat().st_size < 256:
            err = proc.stderr.decode("utf-8", "replace").strip()
            raise RuntimeError(err[-1200:] or "FFmpeg produced no representative frame")
        from PIL import Image
        with Image.open(out) as image:
            image.verify()
        return out
    finally:
        if server is not None:
            try: server.shutdown()
            except Exception: pass
            try: server.server_close()
            except Exception: pass
        if thread is not None:
            try: thread.join(timeout=2)
            except Exception: pass


# ==============================================================================
# SECTION: Thumbnail generators
# Legacy and stream-based generation.
# ==============================================================================

def generate_fast_legacy_thumbnail(source, display_name, output_path):
    """Generate a single representative frame for legacy containers."""
    return _ffmpeg_single_still(source, display_name, output_path)


def generate_v20_from_stream(source, display_name, output_path, worker_count=4, progress_callback=None):
    """Compatibility entry point; timeline imagery is individual, never composited."""
    return _ffmpeg_single_still(source, display_name, output_path)


# ==============================================================================
# SECTION: Telegram document operations
# Forward, upload and delete operations.
# ==============================================================================

async def forward_without_tag(client, entity, message):
    while True:
        try:
            # drop_author=True makes this a copied message rather than a visible
            # forwarded-from message. It does not download/re-upload the video.
            return await client.forward_messages(entity, message.id, from_peer=entity, drop_author=True)
        except FloodWaitError as e:
            print(f"Telegram requested {e.seconds}s wait. Waiting...")
            await asyncio.sleep(e.seconds + 2)


async def delete_messages(client, entity, ids):
    while True:
        try:
            await client.delete_messages(entity, ids)
            return
        except Exception as exc:
            wait = flood_wait_seconds(exc)
            if wait:
                print(f"Telegram flood wait detected: {wait}s. Waiting {wait + 2}s...")
                await asyncio.sleep(wait + 2)
                continue
            raise


THUMBNAIL_TIMEOUT_SECONDS = 90


# ==============================================================================
# SECTION: Message thumbnail entry points
# Creates and applies a thumbnail for a message.
# ==============================================================================

async def try_embedded_telegram_thumbnail(client, message, output_path):
    """Use Telegram's own media thumbnail when no companion image message exists.

    This is deliberately attempted before byte-range decoding. It requires only
    the thumbnail media, not the full video, and avoids Telegram GetFile flood
    waits for legacy containers that already have an embedded preview.
    """
    try:
        media = getattr(message, "media", None)
        document = getattr(media, "document", None) if media else None
        thumbs = list(getattr(document, "thumbs", None) or []) if document else []
        if not thumbs:
            return False

        print("  Telegram embedded thumbnail found; using it instead of byte-range decoding...")
        target = Path(output_path)
        downloaded = await _download_media_governed(client, 
            message,
            file=str(target),
            thumb=-1,
        )
        if not downloaded or not Path(downloaded).exists() or Path(downloaded).stat().st_size <= 0:
            return False
        from ..engines.thumbnail_policy import validate_image
        ok, reason = validate_image(downloaded)
        if not ok:
            print(f"  Embedded Telegram thumbnail rejected: {reason}; falling back to source video frame")
            try:
                Path(downloaded).unlink(missing_ok=True)
            except Exception:
                pass
            return False

        # Normalize Telegram's thumbnail to the same JPEG output used elsewhere.
        try:
            with Image.open(downloaded) as im:
                rgb = im.convert("RGB")
                rgb.save(target, "JPEG", quality=JPEG_QUALITY, optimize=False)
        except Exception:
            return False

        print(f"  Embedded thumbnail saved: {target}")
        return True
    except Exception as exc:
        wait = flood_wait_seconds(exc)
        if wait:
            print(f"  Telegram thumbnail flood wait: {wait}s; waiting once before retry...")
            await asyncio.sleep(min(wait + 2, 60))
            try:
                downloaded = await _download_media_governed(client, 
                    message, file=str(output_path), thumb=-1
                )
                if downloaded and Path(downloaded).exists() and Path(downloaded).stat().st_size > 0:
                    with Image.open(downloaded) as im:
                        im.convert("RGB").save(output_path, "JPEG", quality=JPEG_QUALITY, optimize=False)
                    return True
            except Exception:
                pass
        return False


async def create_thumbnail_for_message(client, entity, message, tempdir):
    """Generate a thumbnail file for a Telegram video message."""
    if THUMBNAILS_UNAVAILABLE_REASON:
        raise RuntimeError(THUMBNAILS_UNAVAILABLE_REASON)
    loop = asyncio.get_running_loop()
    source = DirectTelegramRangeSource(client, message, loop)
    if source.size <= 0:
        raise RuntimeError("Telegram did not report a usable video size")

    out = Path(tempdir) / thumbnail_name_for_video(safe_video_name(message))
    try:
        # IMPORTANT: Do NOT use Telegram's embedded document thumbnail as the
        # generated video thumbnail. Telegram can replace the real preview with
        # its generic NSFW/placeholder image (the black "NSFW" image seen in
        # the channel). The thumbnail for this workflow must be decoded from
        # the actual video bytes. The helper try_embedded_telegram_thumbnail()
        # is intentionally not called here.

        # Legacy containers (WMV/AVI/MKV/FLV/etc.) should NOT enter the
        # MP4-specific index parser first. That parser can spend up to the
        # global timeout probing for an MP4 moov atom that cannot exist.
        # Go directly to the bounded sequential path for legacy containers.
        suffix = Path(safe_video_name(message)).suffix.lower()
        if suffix not in {".mp4", ".mov", ".m4v"}:
            print("  Using fast sequential thumbnail mode for legacy container...")
            try:
                await asyncio.to_thread(
                    generate_fast_legacy_thumbnail, source, safe_video_name(message), out
                )
                return out
            except Exception as exc:
                print(f"  Fast legacy thumbnail failed: {type(exc).__name__}: {exc}")

        # MP4/MOV/M4V: use the indexed MP4/GOP path whenever Pillow is
        # available. PyAV is optional here: the compact fetched GOP is decoded
        # locally by FFmpeg when PyAV is intentionally disabled in the main
        # process. This avoids remote FFmpeg seeks and prevents the old
        # 36-process/range-burst fallback.
        if Image is None:
            print("  Pillow unavailable; using FFmpeg single-frame fallback...")
            await asyncio.to_thread(_ffmpeg_single_still, source, safe_video_name(message), out)
            return out

        try:
            smart_ok = await asyncio.to_thread(
                _smart_thumbnail_from_telegram, source, safe_video_name(message), out
            )
        except TelegramRangeFloodError as exc:
            # Never fall back to FFmpeg random-access mode after a Telegram
            # range flood. That would immediately repeat the same network load.
            raise RuntimeError(
                "Telegram rate-limited video byte-range reads; thumbnail generation stopped "
                "for this video without using a placeholder"
            ) from exc
        except Exception as exc:
            smart_ok = False
            print(f"Smart MP4 path unavailable: {type(exc).__name__}: {exc}")

        if smart_ok:
            return out

        # Final fallback for MP4/MOV when the direct MP4 path fails for a
        # non-flood reason.
        print("Falling back to FFmpeg HTTP-range mode for this video...")
        await asyncio.to_thread(
            generate_v20_from_stream, source, safe_video_name(message), out
        )
        return out
    finally:
        try:
            source.close()
        except Exception:
            pass

