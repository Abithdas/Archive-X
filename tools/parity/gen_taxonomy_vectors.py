"""Write the test vectors that pin the Rust tag taxonomy (crates/ax-core/tests/tag_taxonomy_cases.json) to the Python original.

    python tools/parity/gen_taxonomy_vectors.py          # rewrite the file
    python tools/parity/gen_taxonomy_vectors.py --check  # fail if the file is out of date

Each case is a list of rows (name plus a video id or a count) and the total number of videos; the expected answer is what
`metadata/tag_taxonomy.build_tag_taxonomy` returns. Names include accents, case-folding oddities, combining marks, full-width
forms, superscripts, punctuation and the format tags the taxonomy treats as generic.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from telegram_archive.metadata.tag_taxonomy import build_tag_taxonomy, categories_for_tag, is_generic_tag, normalize_tag_key  # noqa: E402

OUT = ROOT / "crates" / "ax-core" / "tests" / "tag_taxonomy_cases.json"

NAMES = [
    "Outdoor", "outdoor", "OUTDOOR ", "Out-door", "Out_door", "public", "Public Place", "pool", "Pool Party", "park", "beach", "amateur",
    "Amateur!", "POV", "p.o.v.", "P O V", "Point of View", "hd", "HD", "Full HD", "4K", "4k", "8K", "1080p", "1080 p", "720P", "2160p HD",
    "480p hevc", "H.264", "h264", "H 264 encode", "x265", "X264 rip", "mp4", "MKV", "webm", "WebDL", "Web-DL", "web dl", "new", "Free",
    "Interview", "Café", "CAFÉ", "Café", "Straße", "STRASSE", "strasse", "İstanbul", "istanbul", "ΣΊΣΥΦΟΣ", "σίσυφος", "ﬁlm", "film",
    "ＦＵＬＬ　ＨＤ", "ｈｄ", "x²", "x2", "Ⅷ", "viii", "日本語", "한국어", "naïve", "NAÏVE", "Tom & Jerry", "tom and jerry", "Tom_Jerry", "a&b", "a_b",
    "  padded  ", "", "---", "___", "&", "emoji 🎬 tag", "🎬", "tab\tname", "new line", "é", "é", "ß", "SS", "İ", "i̇",
    "teacher", "Teacher's Pet", "nurse", "stockings", "cosplay", "dp", " dp ", "69 position", "doggy-style", "Doggy Style", "milf", "bbw",
    "Big_Ass", "big ass", "BIG  ASS", "feet", "toys", "sex toy", "facial", "Solo Play", "solo-play",
]


def cases():
    out = []
    # 1. precomputed counts, several totals
    for total in (0, 1, 2, 3, 10, 57, 1000, 123456):
        rows = [{"name": n, "video_id": None, "video_count": (i * 7) % (total + 3)} for i, n in enumerate(NAMES)]
        out.append({"total": total, "rows": rows})
    # 2. one video per row, spelling variants sharing videos
    for total in (5, 40, 500):
        rows = [{"name": n, "video_id": (i * 3 + j) % max(total, 1) + 1, "video_count": 0} for i, n in enumerate(NAMES) for j in range(1 + i % 4)]
        out.append({"total": total, "rows": rows})
    # 3. the route's real shape: counts first, then (name, video) rows for names that collide
    rows = [{"name": n, "video_id": None, "video_count": 3 + i % 5} for i, n in enumerate(NAMES)]
    rows += [{"name": n, "video_id": 100 + (i + j) % 9, "video_count": 0} for i, n in enumerate(NAMES[:40]) for j in range(3)]
    out.append({"total": 200, "rows": rows})
    # 4. ties and extremes
    out.append({"total": 10, "rows": [{"name": "Tie", "video_id": 1, "video_count": 0}, {"name": "tie", "video_id": 2, "video_count": 0}, {"name": "TIE", "video_id": 3, "video_count": 0}]})
    out.append({"total": 10, "rows": [{"name": "same", "video_id": None, "video_count": -4}, {"name": "same", "video_id": None, "video_count": 0}]})
    out.append({"total": 10, "rows": [{"name": "everywhere", "video_id": v, "video_count": 0} for v in range(1, 11)] + [{"name": "nearly", "video_id": v, "video_count": 0} for v in range(1, 10)]})
    out.append({"total": 0, "rows": []})
    out.append({"total": -5, "rows": [{"name": "x", "video_id": None, "video_count": 4}]})
    return out


def build():
    result = {"normalize": {n: normalize_tag_key(n) for n in NAMES}, "generic": {n: is_generic_tag(n) for n in NAMES},
              "categories": {n: categories_for_tag(n) for n in NAMES}, "taxonomy": []}
    for c in cases():
        rows = [{"name": r["name"], "video_id": r["video_id"], "video_count": r["video_count"]} for r in c["rows"]]
        result["taxonomy"].append({"total": c["total"], "rows": rows, "expected": build_tag_taxonomy(c["total"], rows)})
    return result


def main():
    text = json.dumps(build(), ensure_ascii=False, indent=1, sort_keys=False) + "\n"
    if "--check" in sys.argv:
        if not OUT.exists() or OUT.read_text(encoding="utf-8") != text:
            sys.exit(f"{OUT.relative_to(ROOT)} is out of date: run python tools/parity/gen_taxonomy_vectors.py")
        print("tag taxonomy vectors are up to date")
        return
    OUT.write_text(text, encoding="utf-8")
    print("wrote", OUT.relative_to(ROOT), f"({len(text) // 1024} KiB)")


if __name__ == "__main__":
    main()
