const pct = (v) => Math.max(0, Math.min(100, Math.round(Number(v) * 100)));
export function matchLabel(x) {
    if (x.similarity_score == null || !Number.isFinite(Number(x.similarity_score)))
        return '';
    return `${pct(x.similarity_score)}% match · ${Number(x.matched_detail_count || 0)}/${Number(x.compared_detail_count || 0)} traits`;
}
export function matchTip(x) {
    if (!matchLabel(x))
        return '';
    const fields = (x.matched_fields ?? []).map((f) => String(f || '').trim()).filter(Boolean).join(', ') || 'gender';
    const cover = Number.isFinite(Number(x.metadata_coverage)) ? pct(x.metadata_coverage) : 0;
    return `Matched attributes: ${fields}. Compared ${Number(x.compared_detail_count || 0)} personal/physical attributes; metadata coverage ${cover}%.`;
}
