import { describe, expect, it } from 'vitest'
import { bytesText, percentOf, ramNote, speedText, setupTotal, DEFAULT_ENDPOINT, isInstalled, DEFAULT_MODEL, QUICK, chatBody, configBody, configFrom, header, messageTime, readiness, testPassed, TABS } from './ai'

describe('setup wording', () => {
  it('turns bytes, speed and progress into short words', () => {
    expect(percentOf(50, 200)).toBe(25); expect(percentOf(5, 0)).toBe(0); expect(percentOf(999, 100)).toBe(100)
    expect(bytesText(1223 * 1024 ** 2)).toBe('1.2 GB'); expect(bytesText(24 * 1024 ** 2)).toBe('24 MB')
    expect(speedText(23 * 1024 ** 2)).toBe('23 MB/s'); expect(speedText(0)).toBe('')
    const m = { id: 'strong', label: 'Strong', model: 'x', size_mb: 4794, min_ram_gb: 16, blurb: '', recommended: false, fits: false } as const
    expect(ramNote(8, m)).toBe('Wants 16 GB of memory; this PC has 8 GB.'); expect(ramNote(32, m)).toBe(''); expect(ramNote(0, m)).toBe(''); expect(ramNote(15.7, m)).toBe('')   // a 16 GB PC reports 15.7
    expect(setupTotal({ ...m, size_mb: 2381 }, { id: 'win-cpu', label: 'Processor', size_mb: 24, gpu: false })).toBe('2.3 GB')
  })
})

describe('ai', () => {
  it('summarises the model status', () => {
    expect(header(null)).toEqual({ ready: false, state: 'Checking local model…', model: '—' })
    const files = { runtime: { exists: true }, model: { exists: true } }
    expect(header({ provider: 'llama.cpp', model: 'Q', runtime: { ...files, server: { reachable: true } } })).toEqual({ ready: true, state: 'Assistant ready', model: 'llama.cpp · Q' })
    expect(header({ runtime: { ...files, server: { reachable: false, starting: true } } }).state).toBe('Starting the assistant…')
    expect(header({ runtime: { ...files, server: { reachable: false } } }).state).toBe('Asleep — starts when you ask')   // it starts by itself on the first question
    expect(header({ runtime: { model: { exists: true } } }).state).toBe('Not set up yet')
    expect(header({}).state).toBe('Not set up yet'); expect(header({}).model).toBe('llama.cpp · —')
    expect(isInstalled({ runtime: { runtime: { exists: true }, model: { exists: true } } })).toBe(true); expect(isInstalled({ runtime: { model: { exists: true } } })).toBe(false); expect(isInstalled(undefined)).toBe(false)
  })
  it('lists readiness rows', () => {
    expect(readiness({ runtime: { runtime: { exists: true }, model: { exists: false }, server: { reachable: false }, ready: false } })).toEqual([['llama-server', 'Ready'], ['GGUF model', 'Missing'], ['Local endpoint', 'Not running'], ['Inference', 'Not ready'], ['Mode', 'CPU-first'], ['Cloud API', 'Disabled']])
    expect(readiness(null)[0]).toEqual(['llama-server', 'Missing'])
  })
  it('fills and cleans the model form', () => {
    expect(configFrom(null)).toEqual({ runtime_path: '', model_path: '', model_id: DEFAULT_MODEL, endpoint: DEFAULT_ENDPOINT, context: 8192, gpu_layers: 0, thinking: false })
    expect(configBody({ runtime_path: ' /a ', model_path: ' /m ', model_id: ' id ', endpoint: ' e ', context: 99999, gpu_layers: -4, thinking: true })).toEqual({ runtime_path: '/a', model_path: '/m', model_id: 'id', endpoint: 'e', context: 16384, gpu_layers: 0, thinking: true })
    expect(configBody({ ...configFrom(null), context: 10 }).context).toBe(1024); expect(configBody({ ...configFrom(null), context: Number.NaN }).context).toBe(8192)
  })
  it('builds prompts and labels', () => {
    expect(chatBody('hi', '/ai')).toEqual({ message: 'hi', page: '/ai', selected: null })
    expect(QUICK).toHaveLength(4); expect(TABS.map((t) => t.value)).toEqual(['issues', 'assistant', 'findings', 'models'])
    expect(testPassed(120, 'ok')).toBe('Model test passed in 120 ms: ok'); expect(messageTime({ role: 'user', created_at: 'x' })).toBe('You · x'); expect(messageTime({ role: 'assistant' })).toBe('Archive X Local AI · ')
  })
})
