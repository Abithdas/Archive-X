"""Record what Python's `metadata/parser.py` and the OSHASH of `metadata/fingerprint.py` give.

    python tools/parity/gen_scene_parser_vectors.py          # rewrite crates/ax-core/tests/scene_parser_cases.json
    python tools/parity/gen_scene_parser_vectors.py --check  # fail if the file is out of date

The names are real-looking scene file names, the conventions the parser documents, and names built to trip Python's own rules for words,
digits, spaces and case (`ß`, `İ`, `ſ`, the Kelvin sign, Arabic-Indic digits, no-break spaces, U+001C), plus a seeded random mix of the pieces.
The parser runs without `ftfy`, as `requirements.txt` has it (the script stops if ftfy is installed, because the recording would differ).
"""
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "crates" / "ax-core" / "tests" / "scene_parser_cases.json"

NAMES = [
    "", "   ", "a", "Studio.23.05.14.Some.Scene.Title.1080p.mp4", "Studio - 2023-05-14 - Scene Title.mp4", "[Studio] Jane Doe - Great Scene (2021).mp4",
    "[Studio] Jane Doe & John Roe and Mary Major - Great Scene.mp4", "SONE-012.mp4", "SONE.012.mp4", "sone012.mp4", "FC2-PPV-1234567.mp4", "FC2PPV1234567",
    "VID_001.mp4", "Video-0001.mp4", "Opaque_001.mp4", "File-001", "Part_12", "CLIP999", "MiaKhalifa1080PDate_Starring_Title_Here.mp4", "Mia 4K Date 2023-01-02 Title",
    "Studio_23_05_14_Jane_Doe_Great_Scene_Title_Here.mp4", "Studio_23_05_14_Jane_Doe_x264_WEB-DL.mp4", "Studio.2023.05.14.mp4", "Scene_2023-05-14.mp4",
    "Studio 2023 05 14 Jane Doe Title Words Here", "2023-05-14", "23.05.14", "99.13.45 not a date", "Studio.2023.13.14.Title", "Studio.1989.05.14.Title",
    "Studio.2101.05.14.Title", "Studio.2023.02.31.Title", "Title (2019) [1080p]", "Title [2019]", "Title (1899)", "Title 2160p HDR", "Title UHD", "Title FHD", "Title HD",
    "Title hd.mp4", "Title.4K.mp4", "xxx.prt.sample.final", "Title web-dl webrip bluray brrip", "Title split scenes", "Title split-scene", "Title split_scenes 8bit 10bit",
    "Scéne 01.mp4", "Scéne 01.mp4.jpg", "scene_01_mp4.jpg", "scene 01.mp4.jpg", "Scene.mp4.png", "scene.JPG", "Scene.MP4", "Scene.mkv.webp",
    "Studio - Jane Doe - Title", "A very long studio name that goes past the forty character limit - Title", "Studio - ", " - Title", "Studio -  - Title",
    "Studio.1990.05.14.Title", "Studio.2100.12.31.Title", "Studio.2100.12.32.Title", "Studio.1990.00.14.Title", "Studio.1990.01.00.Title", "Studio.2023.5.4.Title",
    "x" * 40 + ".2023.05.14.Title", "x" * 41 + ".2023.05.14.Title", "x" * 40 + " - Title words", "x" * 41 + " - Title words", "y" * 40 + " - " + "z" * 5, "y" * 41 + " - " + "z" * 5,
    "Studio.Name.Extra.2023.05.14.Title", "Studio.Name.2023.05.14.Title", "A.B.C.2023.05.14.Title", "x" * 45 + ".2023.05.14.Title",
    "ÀÉÎÕÜ Studio - Ünïcode Títle", "日本語 - タイトル.mp4", "Студия - Название сцены 2023.05.14", "Studio 🎬 - Title 🔥", "ß straße SONE-012", "straßeSONE-012",
    "ŉ SONE-12", "İSTANBUL 2023.05.14 Title", "ıSTANBUL title", "ſtudio.23.05.14.title", "Kelvin.23.05.14.title", "KELVIN 1080p", "1080K", "title with nbsp",
    "title\x1cwith\x1fsep", "title line sep", "title\u0085next", "title ​ zero", "title᠎Mongolian", "Titlé 1080ṕ", "xxx́ title", "web́dl",
    "٢٠٢٣.٠٥.١٤ Title", "Studio.2023.٠٥.14.Title", "Studio.20٢٣.05.14.Title", "Studio.２０２３.05.14.Title", "Title (２０２１)", "Title ٢٠٢١ (2021)", "S٣NE-012", "SONE-０１２",
    "Studio.23.05.14.Jane.Doe.Great.Scene.Title.mp4", "Studio_23_05_14_JaneDoe_GreatScene.mp4", "Studio_23_05_14_Jane_Doe_x264.mp4", "Studio.23.05.14.AB.CD.title.words",
    "Studio.23.05.14.J.D.title.words.go.here", "Studio.23.05.14.Jane-Doe.Great.Scene.Title", "Studio.23.05.14.Jane.Doe.2.3.4.5", "Studio.23.05.14.JaneDoe.Great.Scene.Title",
    "Studio.23.05.14.O'Brien.Smith.Great.Scene.Title", "Studio.23.05.14.Élodie.Dupont.Great.Scene.Title", "Studio.23.05.14.Jane.Doe.XXX.1080p.x264-GRP.mp4",
    "[Studio]Jane - Title", "[Studio] Jane,Doe - Title", "[Studio] Jane AND John - Title", "[Studio] Sandy Anderson - Title", "[Studio] Brand - Title", "[S] P - T",
    "[" + "x" * 61 + "] P - T", "[Studio] " + "p" * 81 + " - T", "[Studio] Jane Doe - Title SONE-012 2023.05.14", "[Studio] Jane Doe - Title (2020) 1080p",
    "Jane Doe, John Roe - Title", "a - b - c", "a  -  b", "a - b.mp4", "title - 1080p", "1080p", "HD", "hd 4k", "4k4k", "x264", "title_x264_title", "ABC-123 ABC-124",
    "ABCDEFGHIJK-123", "A-123", "AB-1", "AB-12", "AB-123456", "AB-1234567", "AB.12", "AB_12", "AB 12", "ab12cd34", "2023.05.14 SONE-012", "SONE-012 2023.05.14",
    "SONE-2023.05.14", "Studio.2023.05.14.SONE-012.Title", "scene-2023", "scene 2023", "Studio.1080p.2023.05.14", "name.1080pdate.title", "name 720pDate title", "name4kDate",
    "Date", "date", "Title 4K Date", "StudioHDDate.title", "Studio.mp4.mp4", "Studio.jpg.jpg", "Studio.mp4.jpg.png", ".mp4", ".jpg", "..", "-", " - ", "( ) [ ]", "title -", "- title",
    "Studio.23.05.14.title.with.the.and.the", "Studio.23.05.14.Jane.and.John.Great.Scene.Title", "Studio.23.05.14.AND.Great.Scene.Title.Here",
]
PIECES = ["Studio", "Jane", "Doe", "Great", "Scene", "Title", "SONE-012", "1080p", "4K", "x264", "WEB-DL", "2023.05.14", "23.05.14", "(2020)", "[2019]", "[Studio]", " - ", ".", "_",
          " ", "-", "&", "and", ",", "xxx", "Date", "ß", "İ", "ſ", "K", "é", "é", "日本", "٣", "٢٠٢٣", " ", "\x1c", "mp4", ".mp4", ".jpg", "VID_001", "hd", "HD", "web", "prt", "()", "[]"]
GENERIC = ["VID-001", "video_0001", "Clip999", "MOVIE-1", "file-9", "UNKNOWN-00", "opaque_42", "DOWNLOAD-1", "part 12", "chunk-1", "SONE-012", "", None, "x", "VID", "vid-", "ß-1", "FILE", "V-ID1"]


def lcg_file(size, seed):
    return lambda i: (i * 31 + (i >> 8) * 7 + seed) & 0xFF


def main():
    from telegram_archive.metadata import parser, fingerprint
    if parser._HAS_FTFY:
        sys.exit("ftfy is installed here: the recording must be made without it, as requirements.txt has it")
    rng = random.Random(20261120)
    names = list(NAMES)
    for _ in range(1500):
        names.append("".join(rng.choice(PIECES) for _ in range(rng.randint(1, 9))))
    out = {"names": [], "generic": [], "fused": [], "oshash": []}
    for n in names:
        out["names"].append({"name": n, "clean": parser.clean_text(n), "canonical": parser.canonical_key(n), "parsed": parser.parse_scene_name(n)})
    for g in GENERIC:
        out["generic"].append({"value": g, "result": parser.is_generic_scene_code(g)})
    for n in names[:120]:
        out["fused"].append({"value": n, "result": parser.split_fused_quality_before_date(n)})
    for size, seed in [(1, 3), (7, 9), (8, 1), (9, 2), (100, 5), (65535, 7), (65536, 11), (65537, 13), (131071, 17), (131072, 19), (131073, 23), (200000, 29), (1 << 33, 31), (123456789, 37)]:
        f = lcg_file(size, seed)
        want = min(65536, size)
        head = bytes(f(i) for i in range(want))
        tail = bytes(f(i) for i in range(max(0, size - want), size))
        out["oshash"].append({"size": size, "seed": seed, "result": fingerprint.oshash_from_parts(size, head, tail)})
    # refusals
    for size, hl, tl in [(0, 0, 0), (-5, 0, 0), (100, 99, 100), (100, 100, 101), (70000, 65535, 65536)]:
        try:
            r = fingerprint.oshash_from_parts(size, bytes(hl), bytes(tl))
        except ValueError as exc:
            r = {"error": str(exc)}
        out["oshash"].append({"size": size, "head_len": hl, "tail_len": tl, "result": r})
    return json.dumps(out, ensure_ascii=False, indent=0) + "\n"


if __name__ == "__main__":
    text = main()
    if "--check" in sys.argv:
        if not OUT.exists() or OUT.read_text(encoding="utf-8") != text:
            sys.exit(f"{OUT.relative_to(ROOT)} is out of date: run python tools/parity/gen_scene_parser_vectors.py")
        print("up to date")
    else:
        OUT.write_text(text, encoding="utf-8")
        print("wrote", OUT.relative_to(ROOT), f"({len(text) // 1024} KiB)")
