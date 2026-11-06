"""Hardware and network adaptive runtime for Archive X.

Detects local CPU/GPU/RAM/storage capabilities, verifies FFmpeg hardware
encoders when available, and provides an on-demand internet quality test.
The network test is deliberately user-triggered: startup never consumes
bandwidth unexpectedly.
"""

# ------------------------------------------------------------------
# FILE MAP (search for "SECTION:" to jump between sections)
#   - Hardware detection
#   - Network speed test
#   - Profiles and caching
# ------------------------------------------------------------------
from __future__ import annotations

import asyncio, ctypes, json, os, platform, shutil, subprocess, time, urllib.request, urllib.error
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

try:
    import psutil  # optional
except Exception:
    psutil = None

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0




# ==============================================================================
# SECTION: Hardware detection
# CPU, RAM, storage, GPU and FFmpeg acceleration probing.
# ==============================================================================

def _run_sync(cmd: list[str], timeout: float = 5.0) -> tuple[int, str, str]:
    try:
        p=subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, creationflags=_NO_WINDOW)
        return p.returncode, p.stdout or "", p.stderr or ""
    except Exception as exc:
        return 1, "", str(exc)


def _powershell(script: str) -> str:
    if os.name != "nt": return ""
    rc,out,_=_run_sync(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script], 6)
    return out.strip() if rc==0 else ""


def _cpu_info() -> dict[str, Any]:
    logical=os.cpu_count() or 1
    physical=logical
    if psutil:
        try: physical=psutil.cpu_count(logical=False) or logical
        except Exception: pass
    return {"name": platform.processor() or platform.uname().processor or "Unknown CPU",
            "physical_cores": physical, "logical_threads": logical,
            "architecture": platform.machine(), "python_bits": 64 if __import__('struct').calcsize('P')*8==64 else 32}


def _ram_info() -> dict[str, Any]:
    """Return host RAM using psutil first, then OS-native fallbacks.

    The fallback paths are intentionally dependency-free so the Settings page
    does not degrade to 0 GB merely because psutil is not installed.
    """
    if psutil:
        try:
            m=psutil.virtual_memory()
            return {
                "total_bytes": int(m.total),
                "available_bytes": int(m.available),
                "total_gb": round(m.total/2**30, 2),
                "available_gb": round(m.available/2**30, 2),
                "source": "psutil",
            }
        except Exception:
            pass

    try:
        if os.name == "nt":
            class _MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]
            stat = _MEMORYSTATUSEX()
            stat.dwLength = ctypes.sizeof(_MEMORYSTATUSEX)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
                total = int(stat.ullTotalPhys); avail = int(stat.ullAvailPhys)
                return {"total_bytes": total, "available_bytes": avail,
                        "total_gb": round(total/2**30,2), "available_gb": round(avail/2**30,2),
                        "source": "windows-GlobalMemoryStatusEx"}
        if sys_platform := platform.system():
            if sys_platform == "Linux":
                vals = {}
                with open("/proc/meminfo", "r", encoding="utf-8") as fh:
                    for line in fh:
                        if ":" not in line:
                            continue
                        key, raw = line.split(":", 1)
                        parts = raw.strip().split()
                        if parts and parts[0].isdigit():
                            vals[key] = int(parts[0]) * 1024
                total = int(vals.get("MemTotal", 0))
                # MemAvailable is the kernel's best estimate of immediately
                # reclaimable memory; fall back to MemFree if unavailable.
                avail = int(vals.get("MemAvailable", vals.get("MemFree", 0)))
                if total:
                    return {"total_bytes": total, "available_bytes": avail,
                            "total_gb": round(total/2**30,2), "available_gb": round(avail/2**30,2),
                            "source": "linux-/proc/meminfo"}
            if sys_platform == "Darwin":
                rc, out, _ = _run_sync(["sysctl", "-n", "hw.memsize"], 3)
                total = int(out.strip()) if rc == 0 and out.strip().isdigit() else 0
                if total:
                    avail = 0
                    rc2, out2, _ = _run_sync(["vm_stat"], 3)
                    if rc2 == 0:
                        page = 4096
                        import re
                        free = 0
                        for name in ("Pages free", "Pages inactive", "Pages speculative"):
                            m = re.search(rf"^{re.escape(name)}:\s+(\d+)\.", out2, re.M)
                            if m:
                                free += int(m.group(1)) * page
                        avail = free
                    return {"total_bytes": total, "available_bytes": avail,
                            "total_gb": round(total/2**30,2), "available_gb": round(avail/2**30,2),
                            "source": "macOS-sysctl/vm_stat"}
    except Exception:
        pass
    return {"total_bytes":0,"available_bytes":0,"total_gb":0,"available_gb":0,"source":"unavailable"}


def _storage_info(path: Path) -> dict[str, Any]:
    try:
        u=shutil.disk_usage(path); return {"path":str(path),"total_bytes":u.total,"free_bytes":u.free,"free_gb":round(u.free/2**30,2)}
    except Exception: return {"path":str(path),"total_bytes":0,"free_bytes":0,"free_gb":0}


def _gpu_info(ffmpeg: str|None) -> list[dict[str,Any]]:
    gpus=[]
    raw=_powershell("Get-CimInstance Win32_VideoController | Select-Object Name,AdapterRAM,DriverVersion,PNPDeviceID | ConvertTo-Json -Compress")
    if raw:
        try:
            data=json.loads(raw); data=data if isinstance(data,list) else [data]
            for x in data:
                gpus.append({"name":x.get("Name") or "Unknown GPU","memory_bytes":int(x.get("AdapterRAM") or 0),"driver":x.get("DriverVersion") or "","vendor":"nvidia" if 'nvidia' in (x.get('Name') or '').lower() else ('amd' if any(k in (x.get('Name') or '').lower() for k in ('amd','radeon')) else ('intel' if 'intel' in (x.get('Name') or '').lower() else 'other'))})
        except Exception: pass
    if not gpus and shutil.which('nvidia-smi'):
        rc,out,_=_run_sync(['nvidia-smi','--query-gpu=name,memory.total,driver_version','--format=csv,noheader,nounits'],5)
        if rc==0:
            for line in out.splitlines():
                p=[x.strip() for x in line.split(',')]
                if p: gpus.append({"name":p[0],"memory_mb":int(float(p[1])) if len(p)>1 and p[1].replace('.','',1).isdigit() else 0,"driver":p[2] if len(p)>2 else '',"vendor":"nvidia"})
    encoders=[]
    if ffmpeg:
        rc,out,_=_run_sync([ffmpeg,'-hide_banner','-encoders'],10)
        if rc==0: encoders=[e for e in ('h264_nvenc','hevc_nvenc','h264_qsv','hevc_qsv','h264_amf','hevc_amf') if e in out]
    for g in gpus:
        v=g.get('vendor'); g['hardware_encoders']=[e for e in encoders if (('nvenc' in e and v=='nvidia') or ('qsv' in e and v=='intel') or ('amf' in e and v=='amd'))]
    return gpus


def _ffmpeg_hwaccels(ffmpeg: str|None) -> list[str]:
    if not ffmpeg:
        return []
    rc, out, _ = _run_sync([ffmpeg, "-hide_banner", "-hwaccels"], 10)
    if rc != 0:
        return []
    vals = []
    for line in out.splitlines():
        line = line.strip().lower()
        if line and not line.startswith("hardware acceleration methods") and line not in vals:
            vals.append(line)
    return vals


def _verified_decoder(ffmpeg: str|None, method: str) -> bool:
    if not ffmpeg or not method:
        return False
    # Capability smoke test. The test intentionally decodes a tiny synthetic
    # stream so startup never downloads user media or touches Telegram.
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "lavfi",
           "-i", "testsrc2=size=320x180:rate=24:duration=.5"]
    if method == "cuda":
        cmd += ["-hwaccel", "cuda"]
    elif method == "qsv":
        cmd += ["-hwaccel", "qsv"]
    elif method == "d3d11va":
        cmd += ["-hwaccel", "d3d11va"]
    else:
        return False
    cmd += ["-frames:v", "1", "-f", "null", "-"]
    rc, _, _ = _run_sync(cmd, 8)
    return rc == 0


def _verified_encoder(ffmpeg: str|None, enc: str) -> bool:
    if not ffmpeg: return False
    cmd=[ffmpeg,'-hide_banner','-loglevel','error','-f','lavfi','-i','testsrc2=size=320x180:rate=24:duration=.5']
    cmd += ['-c:v',enc,'-f','null','-']
    rc,_,_= _run_sync(cmd,8); return rc==0


def detect_hardware(ffmpeg: str|None=None, package_dir: Path|None=None) -> dict[str,Any]:
    """Probe CPU, RAM, storage, GPU and verified FFmpeg acceleration."""
    if ffmpeg is None:
        ffmpeg=shutil.which('ffmpeg')
        if not ffmpeg and package_dir:
            cand=package_dir/'bin'/'ffmpeg.exe'
            if cand.exists(): ffmpeg=str(cand)
    cpu=_cpu_info(); ram=_ram_info(); storage=_storage_info(Path(package_dir or Path.cwd()))
    gpus=_gpu_info(ffmpeg)
    hwaccels=_ffmpeg_hwaccels(ffmpeg)
    decoder_candidates=[]
    # MPC-HC/MPC Video Renderer uses the D3D11 renderer with D3D11 hardware
    # decoding on modern Windows. Mirror that principle for Archive X HEVC
    # compatibility playback: prefer D3D11VA on Windows, then fall back to the
    # vendor-native CUDA/QSV paths when D3D11VA is unavailable or fails its
    # startup verification.
    # Keep a renderer-oriented D3D11VA preference for native Windows playback,
    # but separately choose a decoder that can stay on the GPU with the chosen
    # H.264 compatibility encoder. FFmpeg documents QSV as the no-system-memory
    # transcoding path and supports D3D11VA surfaces with Windows GPU encoders.
    # See ARCHIVE-X-Documentation.md for the current playback architecture and renderer guidance.
    if os.name == "nt" and "d3d11va" in hwaccels and gpus:
        decoder_candidates.append("d3d11va")
    if any(g.get("vendor") == "nvidia" for g in gpus) and "cuda" in hwaccels:
        decoder_candidates.append("cuda")
    if any(g.get("vendor") == "intel" for g in gpus) and "qsv" in hwaccels:
        decoder_candidates.append("qsv")
    if "d3d11va" in hwaccels and gpus and "d3d11va" not in decoder_candidates:
        decoder_candidates.append("d3d11va")
    verified_decoders={}
    for dec in decoder_candidates:
        verified_decoders[dec]=_verified_decoder(ffmpeg, dec)
        if verified_decoders[dec]:
            break
    usable_decoders=[d for d,v in verified_decoders.items() if v]
    preferred_decoder=usable_decoders[0] if usable_decoders else None

    # Decoder selected specifically for real-time HEVC -> H.264 browser
    # compatibility conversion. Prefer a vendor path that keeps frames on
    # the accelerator all the way into the encoder.
    transcode_decoder = None
    if any(g.get("vendor") == "nvidia" for g in gpus) and "cuda" in hwaccels:
        transcode_decoder = "cuda"
    elif any(g.get("vendor") == "intel" for g in gpus) and "qsv" in hwaccels:
        transcode_decoder = "qsv"
    elif os.name == "nt" and "d3d11va" in hwaccels and gpus:
        transcode_decoder = "d3d11va"
    elif preferred_decoder:
        transcode_decoder = preferred_decoder
    verified={}
    # Startup verification is intentionally bounded: verify only one H.264 hardware
    # encoder (the preferred conversion path). HEVC capability is listed from FFmpeg
    # and is verified lazily by the conversion path when actually requested.
    for enc in ('h264_nvenc','h264_qsv','h264_amf'):
        if any(enc in (g.get('hardware_encoders') or []) for g in gpus):
            verified[enc]=_verified_encoder(ffmpeg,enc)
            if verified[enc]: break
    usable=[e for e,v in verified.items() if v]
    # Prefer a verified hardware backend, otherwise CPU. Keep playback headroom.
    if 'h264_nvenc' in usable: backend='h264_nvenc'
    elif 'h264_qsv' in usable: backend='h264_qsv'
    elif 'h264_amf' in usable: backend='h264_amf'
    else: backend='libx264'
    threads=max(1,int(cpu['logical_threads']))
    ram_gb=ram['total_gb']
    if ram_gb and ram_gb < 8: bg=max(1,min(2,threads//2 or 1))
    elif ram_gb and ram_gb < 16: bg=max(1,min(3,threads//2 or 1))
    else: bg=max(1,min(6,threads//2 or 1))
    # A small, explicit workload policy is shared by streaming compatibility,
    # thumbnail/snapshot generation and background conversion. It is a
    # recommendation, not a promise of parallel throughput.
    gpu_vram = max([int(g.get("memory_bytes") or 0) for g in gpus] + [0])
    if gpu_vram >= 8 * 2**30:
        media_workers = 4
    elif gpu_vram >= 4 * 2**30:
        media_workers = 3
    elif usable_decoders or usable:
        media_workers = 2
    else:
        media_workers = max(1, min(2, int(cpu["physical_cores"] or 1)))
    if ram_gb and ram_gb < 8:
        media_workers = min(media_workers, 1)
    elif ram_gb and ram_gb < 16:
        media_workers = min(media_workers, 2)
    return {"detected_at":time.time(),"cpu":cpu,"ram":ram,"storage":storage,"gpus":gpus,
            "ffmpeg":ffmpeg or '',"verified_encoders":verified,"conversion_backend":backend,
            "hardware_acceleration": {
                "ffmpeg_hwaccels": hwaccels,
                "verified_decoders": verified_decoders,
                "preferred_decoder": preferred_decoder,
                "preferred_transcode_decoder": transcode_decoder,
                "preferred_encoder": backend if backend != "libx264" else None,
                "gpu_vram_bytes": gpu_vram,
            },
            "background_capacity":bg,"media_workers":media_workers,"network":None}




# ==============================================================================
# SECTION: Network speed test
# Download, upload and latency measurement.
# ==============================================================================

def _http_get(url:str, timeout:float, headers=None):
    req=urllib.request.Request(url,headers=headers or {"User-Agent":"Archive-X/1.0"}, method="GET")
    return urllib.request.urlopen(req,timeout=timeout)


def _speed_download(url:str, seconds:float=4.0)->dict[str,Any]:
    started=time.perf_counter(); total=0; chunks=0
    try:
        with _http_get(url,seconds+5) as r:
            while time.perf_counter()-started < seconds:
                b=r.read(512*1024)
                if not b: break
                total+=len(b); chunks+=1
        elapsed=max(time.perf_counter()-started,0.001)
        if total <= 0:
            return {"bytes":0,"seconds":elapsed,"mbps":None,"chunks":chunks,"error":"No bytes received"}
        return {"bytes":total,"seconds":elapsed,"mbps":round(total*8/elapsed/1e6,2),"chunks":chunks}
    except Exception as exc:
        return {"bytes":total,"seconds":max(time.perf_counter()-started,0.001),"mbps":None,"chunks":chunks,"error":f"{type(exc).__name__}: {exc}"}


def _speed_upload(url:str, seconds:float=3.0)->dict[str,Any]:
    # Keep the diagnostic bounded. Upload is optional: some firewalls/proxies
    # permit download/latency but reject POST, so a failure must not zero-out
    # the entire test.
    payload=os.urandom(2*1024*1024)
    started=time.perf_counter(); sent=0
    try:
        while time.perf_counter()-started < seconds and sent < 16*1024*1024:
            req=urllib.request.Request(url,data=payload,method='POST',headers={'User-Agent':'Archive-X/1.0','Content-Type':'application/octet-stream','Cache-Control':'no-cache'})
            with urllib.request.urlopen(req,timeout=seconds+5) as r:
                r.read(64)
            sent+=len(payload)
    except Exception as exc:
        elapsed=max(time.perf_counter()-started,.001)
        return {"bytes":sent,"seconds":elapsed,"mbps":round(sent*8/elapsed/1e6,2) if sent else None,"error":f"{type(exc).__name__}: {exc}"}
    elapsed=max(time.perf_counter()-started,.001)
    return {"bytes":sent,"seconds":elapsed,"mbps":round(sent*8/elapsed/1e6,2) if sent else None}


def _latency(url:str)->dict[str,Any]:
    vals=[]; error=None
    for _ in range(3):
        t=time.perf_counter()
        try:
            with _http_get(url,5) as r: r.read(1)
            vals.append((time.perf_counter()-t)*1000)
        except Exception as exc:
            error=f"{type(exc).__name__}: {exc}"
    return {"samples_ms":[round(x,1) for x in vals],"avg_ms":round(sum(vals)/len(vals),1) if vals else None,"error":error}

async def run_internet_test(duration:float=4.0)->dict[str,Any]:
    """Run a bounded internet diagnostic without converting failed probes to 0 Mbps."""
    # The Cloudflare service is intended for measuring an Internet connection.
    # Use cache-busting query parameters so an intermediary cannot satisfy a
    # speed probe from a local cache. Cloudflare documents speed.cloudflare.com
    # as its Internet speed test service.
    stamp=int(time.time()*1000)
    down_url=f'https://speed.cloudflare.com/__down?bytes=50000000&ax={stamp}'
    up_url=f'https://speed.cloudflare.com/__up?ax={stamp}'
    ping_url=f'https://speed.cloudflare.com/cdn-cgi/trace?ax={stamp}'
    started=time.perf_counter()
    latency=await asyncio.to_thread(_latency,ping_url)
    download=await asyncio.to_thread(_speed_download,down_url,max(2.0,min(duration,6.0)))
    upload=await asyncio.to_thread(_speed_upload,up_url,3.0)
    d=download.get('mbps'); u=upload.get('mbps'); l=latency.get('avg_ms')
    measured=[x for x in (d,) if isinstance(x,(int,float))]
    if measured:
        dv=float(d)
        quality='excellent' if dv>=100 else ('good' if dv>=40 else ('fair' if dv>=15 else 'limited'))
    elif l is not None:
        quality='latency-only'
    else:
        quality='unavailable'
    ok = bool(l is not None or d is not None or u is not None)
    errors=[]
    for name,data in (("latency",latency),("download",download),("upload",upload)):
        if data.get('error'): errors.append(f"{name}: {data['error']}")
    note='On-demand diagnostic; performance varies by server, route, Wi-Fi and time of day.'
    if errors: note += ' ' + ' | '.join(errors)
    return {"ok":ok,"tested_at":time.time(),"seconds":round(time.perf_counter()-started,2),"latency_ms":l,
            "download_mbps":d,"upload_mbps":u,"quality":quality,"download":download,"upload":upload,
            "endpoint":"speed.cloudflare.com","note":note}




# ==============================================================================
# SECTION: Profiles and caching
# Applying the network profile and caching hardware detection.
# ==============================================================================

def apply_network_profile(profile:dict[str,Any], network:dict[str,Any])->dict[str,Any]:
    profile=dict(profile or {}); profile['network']=network
    d=float(network.get('download_mbps') or 0); l=float(network.get('latency_ms') or 0)
    if d < 15 or l > 150: profile['network_mode']='conservative'
    elif d < 40 or l > 80: profile['network_mode']='balanced'
    else: profile['network_mode']='fast'
    return profile


def _hardware_cache_key(ffmpeg: str | None) -> str:
    try:
        st = Path(ffmpeg).stat() if ffmpeg else None
        ff = f"{ffmpeg}|{st.st_size}|{int(st.st_mtime)}" if st else "none"
    except OSError:
        ff = f"{ffmpeg}|missing"
    return f"v1|{ff}|{platform.system()}|{platform.release()}|{platform.version()}|{os.cpu_count()}"


def detect_hardware_cached(ffmpeg: str | None = None, package_dir: Path | None = None) -> dict[str, Any]:
    """Hardware profile without blocking startup on probing.

    v29.4.390: detect_hardware() runs PowerShell CIM queries and FFmpeg test
    decodes/encodes (each with multi-second timeouts) and used to run before the
    web server started on every launch. The last profile is reused when FFmpeg
    and the OS are unchanged; a fresh detection runs in the background and is
    saved for the next start. The first launch still detects synchronously.
    """
    import json, threading
    from .. import config
    cache = Path(config.USER_DATA_DIR) / "state" / "hardware_profile.json"
    key = _hardware_cache_key(ffmpeg)

    def _detect_and_save():
        profile = detect_hardware(ffmpeg, package_dir)
        try:
            cache.parent.mkdir(parents=True, exist_ok=True)
            tmp = cache.with_suffix(".tmp")
            tmp.write_text(json.dumps({"key": key, "profile": profile}, default=str), encoding="utf-8")
            tmp.replace(cache)
        except Exception:
            pass
        return profile

    try:
        saved = json.loads(cache.read_text(encoding="utf-8"))
        if saved.get("key") == key and isinstance(saved.get("profile"), dict):
            threading.Thread(target=_detect_and_save, name="archive-x-hardware-refresh", daemon=True).start()
            return saved["profile"]
    except Exception:
        pass
    return _detect_and_save()
