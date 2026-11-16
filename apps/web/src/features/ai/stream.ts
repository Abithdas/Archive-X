import { ApiError } from '@/api/client'

/** One thing that happens while the assistant answers (the server sends one JSON object per line). */
export type ChatEvent =
  | { t: 'status'; phase: 'starting' | 'thinking' | 'writing'; text: string }
  | { t: 'tool'; name: string; label: string; state: 'start' | 'done' | 'error' }
  | { t: 'reasoning'; text: string }
  | { t: 'delta'; text: string }
  | { t: 'done'; text: string; model: string; latency_ms: number; tool_calls: number }
  | { t: 'error'; detail: string }

/** Takes what has arrived so far, returns the complete events in it and the unfinished line to keep for next time. */
export function splitEvents(buffer: string): { events: ChatEvent[]; rest: string } {
  const lines = buffer.split('\n')
  const rest = lines.pop() ?? ''
  const events: ChatEvent[] = []
  for (const line of lines) {
    const s = line.trim()
    if (!s) continue
    try {
      const v = JSON.parse(s) as ChatEvent
      if (v && typeof v === 'object' && typeof (v as { t?: unknown }).t === 'string') events.push(v)
    } catch { /* a damaged line is skipped, not fatal */ }
  }
  return { events, rest }
}

/** Asks one question and yields the events as they arrive. Throws ApiError when the request itself is refused. */
export async function* streamChat(body: { message: string; page: string; selected: null }, signal?: AbortSignal): AsyncGenerator<ChatEvent> {
  const response = await fetch('/api/ai/chat/stream', {
    method: 'POST', credentials: 'same-origin', cache: 'no-store', signal,
    headers: { Accept: 'application/x-ndjson', 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  })
  if (!response.ok || !response.body) {
    let detail = ''
    try { const j = (await response.json()) as { detail?: unknown }; if (typeof j.detail === 'string') detail = j.detail } catch { /* not JSON */ }
    throw new ApiError(response.status, detail)
  }
  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  for (;;) {
    const { done, value } = await reader.read()
    if (value) buffer += decoder.decode(value, { stream: !done })
    const out = splitEvents(done ? buffer + '\n' : buffer)
    buffer = out.rest
    for (const e of out.events) yield e
    if (done) return
  }
}
