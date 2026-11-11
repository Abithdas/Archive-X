/** The "86% match · 5/6 traits" chip on a similar performer (same wording and rounding as the classic card). */
export interface SimilarFields {
  similarity_score?: number | null
  matched_detail_count?: number | null
  compared_detail_count?: number | null
  matched_fields?: string[] | null
  metadata_coverage?: number | null
}
const pct = (v: unknown) => Math.max(0, Math.min(100, Math.round(Number(v) * 100)))

export function matchLabel(x: SimilarFields): string {
  if (x.similarity_score == null || !Number.isFinite(Number(x.similarity_score))) return ''
  return `${pct(x.similarity_score)}% match · ${Number(x.matched_detail_count || 0)}/${Number(x.compared_detail_count || 0)} traits`
}
export function matchTip(x: SimilarFields): string {
  if (!matchLabel(x)) return ''
  const fields = (x.matched_fields ?? []).map((f) => String(f || '').trim()).filter(Boolean).join(', ') || 'gender'
  const cover = Number.isFinite(Number(x.metadata_coverage)) ? pct(x.metadata_coverage) : 0
  return `Matched attributes: ${fields}. Compared ${Number(x.compared_detail_count || 0)} personal/physical attributes; metadata coverage ${cover}%.`
}
