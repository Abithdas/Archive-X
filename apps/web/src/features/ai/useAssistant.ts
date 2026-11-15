import { useCallback, useRef, useState } from 'react'
import { ApiError } from '@/api/client'
import { CHAT_FAILED, chatBody, replyMeta, type Message } from './ai'
import { streamChat } from './stream'

export interface Step { name: string; label: string; state: 'active' | 'done' | 'error' }
/** The exchange being answered right now (and the last one, until the next question). */
export interface LiveTurn { user: string; status: string; steps: Step[]; text: string; done: boolean; error: string; meta: string }

const reason = (e: unknown) => (e instanceof ApiError && e.detail ? e.detail : e instanceof Error && e.message ? e.message : CHAT_FAILED)

/**
 * Chat with the assistant. `ask` sends a question and updates `turn` as things happen: what the assistant is doing, each tool it uses, and the
 * answer word by word. Earlier exchanges move into `rows`. `ask` resolves with the finished answer, or null when it failed.
 */
export function useAssistant(seed: Message[] | undefined) {
  const [rows, setRows] = useState<Message[]>([])
  const [turn, setTurn] = useState<LiveTurn | null>(null)
  const seeded = useRef(false)
  const live = useRef<LiveTurn | null>(null)
  if (!seeded.current && seed) { seeded.current = true; setRows(seed) }

  const put = (t: LiveTurn | null) => { live.current = t; setTurn(t) }
  const ask = useCallback(async (message: string): Promise<string | null> => {
    const text = message.trim()
    if (!text) return null
    const previous = live.current
    if (previous) setRows((r) => [...r, { role: 'user', content: previous.user, created_at: '' }, ...(previous.text ? [{ role: 'assistant', content: previous.text, created_at: previous.meta }] : previous.error ? [{ role: 'assistant', content: previous.error, created_at: '' }] : [])])
    let t: LiveTurn = { user: text, status: 'Thinking…', steps: [], text: '', done: false, error: '', meta: '' }
    put(t)
    const set = (patch: Partial<LiveTurn>) => { t = { ...t, ...patch }; put(t) }
    try {
      for await (const e of streamChat(chatBody(text, location.pathname + location.search))) {
        if (e.t === 'status') set({ status: e.text })
        else if (e.t === 'tool') {
          const steps = e.state === 'start' ? [...t.steps, { name: e.name, label: e.label, state: 'active' as const }] : t.steps.map((s, i) => (i === t.steps.map((x) => x.name).lastIndexOf(e.name) ? { ...s, state: e.state === 'done' ? ('done' as const) : ('error' as const) } : s))
          set({ steps })
        } else if (e.t === 'delta') set({ text: t.text + e.text })
        else if (e.t === 'done') { set({ text: e.text, done: true, status: '', meta: replyMeta(e.model).replace('Archive X Local AI · ', '') }); return e.text }
        else if (e.t === 'error') { set({ error: e.detail || CHAT_FAILED, done: true, status: '' }); return null }
      }
      set({ done: true, status: '', error: t.text ? '' : CHAT_FAILED })
      return t.text || null
    } catch (e) {
      set({ error: reason(e), done: true, status: '' })
      return null
    }
  }, [])
  return { rows, turn, ask, busy: Boolean(turn && !turn.done) }
}
