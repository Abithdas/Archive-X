"""Put back the gender of performers that lost it.

Until a fix in 30.2.2, saving a performer's picture also saved an empty gender, so the performer dropped out of the Female, Male and Trans
lists (they were still under All). The providers' answers for the performer's videos are kept with each video, and they say the gender, so
the lost value can be read back from there. Only blank genders are filled in; nothing that has a gender is touched.
"""
from __future__ import annotations

import json
from collections import Counter

from ..engines.entity_identity import _clean_gender, normalized_name


def missing_gender_count(db) -> int:
    with db.reading() as rdb:
        return int(rdb.execute("SELECT COUNT(*) FROM performers WHERE COALESCE(gender,'')='' AND lower(trim(name))<>'unknown performer'").fetchone()[0])


def _details(payload: dict):
    raw = payload.get("performer_details") or payload.get("performers") or []
    return [x for x in raw if isinstance(x, dict) and x.get("name")]


def restore_missing_genders(db) -> dict:
    """Fill blank genders from the provider details stored with the performer's videos. Returns what was done."""
    with db.reading() as rdb:
        blank = {int(r["id"]): normalized_name(r["name"]) for r in rdb.execute(
            "SELECT id,name FROM performers WHERE COALESCE(gender,'')='' AND lower(trim(name))<>'unknown performer'").fetchall()}
        rows = rdb.execute(
            "SELECT vp.performer_id, m.provider_payload FROM video_performers vp JOIN media m ON m.id=vp.video_id "
            "WHERE m.provider_payload IS NOT NULL AND m.provider_payload<>'' "
            "AND vp.performer_id IN (SELECT id FROM performers WHERE COALESCE(gender,'')='')").fetchall()
    seen: dict[int, Counter] = {}
    for r in rows:
        pid = int(r["performer_id"])
        want = blank.get(pid)
        if not want:
            continue
        try:
            payload = json.loads(r["provider_payload"])
        except Exception:
            continue
        if not isinstance(payload, dict):
            continue
        for person in _details(payload):
            if normalized_name(person.get("name")) != want:
                continue
            g = _clean_gender(person.get("gender"))
            if g:
                seen.setdefault(pid, Counter())[g] += 1
    # The provider records stored by "Collect performer data" carry the gender too, also for performers whose videos have no stored details.
    with db.reading() as rdb:
        stored = rdb.execute(
            "SELECT entity_id, payload FROM entity_metadata WHERE entity_type='performer' "
            "AND entity_id IN (SELECT id FROM performers WHERE COALESCE(gender,'')='')").fetchall()
    for r in stored:
        pid = int(r["entity_id"])
        if pid not in blank:
            continue
        try:
            payload = json.loads(r["payload"])
        except Exception:
            continue
        g = _clean_gender(payload.get("gender")) if isinstance(payload, dict) else ""
        if g:
            seen.setdefault(pid, Counter())[g] += 1
    restored = 0
    with db.lock:
        for pid, counts in seen.items():
            gender = counts.most_common(1)[0][0]
            cur = db.conn.execute("UPDATE performers SET gender=? WHERE id=? AND COALESCE(gender,'')=''", (gender, pid))
            restored += cur.rowcount
        db.conn.commit()
    return {"checked": len(blank), "restored": restored, "still_blank": len(blank) - restored}
