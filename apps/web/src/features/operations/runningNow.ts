type Obj = Record<string, unknown>

export interface ProcessRow { pid: number; label: string; kind: string; seconds: number; progress: number | null; alive: boolean }
export interface RunningNow {
  /** The Media Manager thread is working but no job row is shown for it. */
  orphanTransfer: { operation: string; quietSeconds: number } | null
  processes: ProcessRow[]
}

const num = (v: unknown) => (typeof v === 'number' && Number.isFinite(v) ? v : 0)

/** What is really running: helper processes (FFmpeg, PyAV worker) and a transfer thread that has no visible job. */
export function runningNow(payload: { processes?: unknown; media_manager?: unknown } | undefined, jobs: Obj[]): RunningNow {
  const procs = (Array.isArray(payload?.processes) ? payload.processes : []) as Obj[]
  const mm = (payload?.media_manager ?? {}) as Obj
  const jobIds = new Set(jobs.map((j) => String(j.id ?? j.job_id ?? '')))
  const hasJob = mm.job_id ? jobIds.has(String(mm.job_id)) : false
  return {
    orphanTransfer: mm.running === true && !hasJob ? { operation: String(mm.live || mm.operation || 'Working'), quietSeconds: num(mm.heartbeat_age) } : null,
    processes: procs.map((p) => ({ pid: num(p.pid), label: String(p.label ?? 'Process'), kind: String(p.kind ?? ''), seconds: num(p.running_seconds), progress: typeof p.progress === 'number' ? p.progress : null, alive: p.alive !== false })),
  }
}

export function clock(seconds: number): string {
  const s = Math.max(0, Math.round(seconds))
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  return h ? `${h}h ${String(m).padStart(2, '0')}m` : `${m}m ${String(s % 60).padStart(2, '0')}s`
}
