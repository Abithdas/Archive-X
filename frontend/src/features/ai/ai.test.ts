import { describe, expect, it } from 'vitest'
import { DEFAULT_ENDPOINT, DEFAULT_MODEL, QUICK, chatBody, configBody, configFrom, header, libraryPrompt, messageTime, readiness, testPassed, TABS } from './ai'

describe('ai', () => {
  it('summarises the model status', () => {
    expect(header(null)).toEqual({ ready: false, state: 'Checking local model…', model: '—' })
    expect(header({ configured: true, provider: 'llama.cpp', model: 'Q' })).toEqual({ ready: true, state: 'Local model ready', model: 'llama.cpp · Q' })
    expect(header({ configured: false, runtime: { model: { exists: true } } }).state).toBe('Runtime configured — server not ready')
    expect(header({}).state).toBe('Local model not configured'); expect(header({}).model).toBe('llama.cpp · —')
  })
  it('lists readiness rows', () => {
    expect(readiness({ runtime: { runtime: { exists: true }, model: { exists: false }, server: { reachable: false }, ready: false } })).toEqual([['llama-server', 'Ready'], ['GGUF model', 'Missing'], ['Local endpoint', 'Not running'], ['Inference', 'Not ready'], ['Mode', 'CPU-first'], ['Cloud API', 'Disabled']])
    expect(readiness(null)[0]).toEqual(['llama-server', 'Missing'])
  })
  it('fills and cleans the model form', () => {
    expect(configFrom(null)).toEqual({ runtime_path: '', model_path: '', model_id: DEFAULT_MODEL, endpoint: DEFAULT_ENDPOINT, context: 4096, gpu_layers: 0 })
    expect(configBody({ runtime_path: ' /a ', model_path: ' /m ', model_id: ' id ', endpoint: ' e ', context: 99999, gpu_layers: -4 })).toEqual({ runtime_path: '/a', model_path: '/m', model_id: 'id', endpoint: 'e', context: 16384, gpu_layers: 0 })
    expect(configBody({ ...configFrom(null), context: 10 }).context).toBe(1024); expect(configBody({ ...configFrom(null), context: Number.NaN }).context).toBe(4096)
  })
  it('builds prompts and labels', () => {
    expect(libraryPrompt('cats')).toMatch(/^Search the published Archive X library for: cats\. /); expect(chatBody('hi', '/app-next/ai')).toEqual({ message: 'hi', page: '/app-next/ai', selected: null })
    expect(QUICK).toHaveLength(4); expect(TABS.map((t) => t.value)).toEqual(['assistant', 'library', 'diagnostics', 'jobs', 'recommendations', 'models'])
    expect(testPassed(120, 'ok')).toBe('Model test passed in 120 ms: ok'); expect(messageTime({ role: 'user', created_at: 'x' })).toBe('You · x'); expect(messageTime({ role: 'assistant' })).toBe('Archive X Local AI · ')
  })
})
