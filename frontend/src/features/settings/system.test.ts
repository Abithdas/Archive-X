import { describe, expect, it } from 'vitest'
import { browserName, browserRows, hardwareRows, osName, speedResult } from './system'

describe('hardware summary', () => {
  it('lists CPU, GPU, RAM, storage and conversion', () => {
    const rows = hardwareRows({ cpu: { name: 'Ryzen', physical_cores: 8, logical_threads: 16 }, gpus: [], ram: { total_gb: 15.71, available_gb: 15.01 }, storage: { free_gb: 13.26 }, conversion_backend: 'libx264', verified_encoders: {} })
    expect(rows).toEqual([
      { label: 'CPU', value: 'Ryzen · 8 cores / 16 threads' }, { label: 'GPU', value: 'No dedicated GPU detected' },
      { label: 'RAM', value: '15.71 GB · 15.01 GB free' }, { label: 'Storage', value: '13.26 GB free' }, { label: 'Conversion', value: 'libx264' },
    ])
  })
  it('adds verified hardware encoders and GPU encoders', () => {
    const rows = hardwareRows({ gpus: [{ name: 'RTX', hardware_encoders: ['h264_nvenc', 'hevc_nvenc'] }], verified_encoders: { h264_nvenc: true, hevc_nvenc: false }, conversion_backend: 'h264_nvenc' })
    expect(rows.find((r) => r.label === 'GPU')!.value).toBe('RTX — h264_nvenc, hevc_nvenc')
    expect(rows.find((r) => r.label === 'Conversion')!.value).toBe('h264_nvenc · verified')
    expect(rows.at(-1)).toEqual({ label: 'Hardware encoding', value: 'h264_nvenc' })
  })
  it('shows Unavailable and ? for missing values instead of 0 or NaN', () => {
    const rows = hardwareRows({})
    expect(rows[0]!.value).toBe('Unknown · ? cores / ? threads')
    expect(rows[2]!.value).toBe('Unavailable · Unavailable free')
    expect(rows[3]!.value).toBe('Unavailable free')
  })
})
describe('speed test result', () => {
  it('formats numbers and the network note', () => {
    const r = speedResult({ ok: true, download_mbps: 94.2, upload_mbps: 11, latency_ms: 23, quality: 'good', network_mode: 'balanced', background_capacity: 3, note: 'ok' })
    expect(r.status).toBe('Test complete')
    expect(r.rows.map((x) => x.value)).toEqual(['94.2 Mbps', '11 Mbps', '23 ms', 'good'])
    expect(r.note).toBe('Network mode: balanced · Background capacity: 3 · ok')
  })
  it('handles an offline run', () => {
    const r = speedResult({ ok: false })
    expect(r.status).toBe('No network result')
    expect(r.rows.map((x) => x.value)).toEqual(['Unavailable', 'Unavailable', 'Unavailable', 'unknown'])
    expect(r.note).toBe('Network mode: adaptive · Background capacity: auto')
  })
})
describe('browser detection', () => {
  const edge = 'Mozilla/5.0 (Windows NT 10.0) AppleWebKit/537.36 Chrome/120 Safari/537.36 Edg/120'
  it('names the browser', () => {
    expect(browserName(edge)).toBe('Edge')
    expect(browserName('Mozilla/5.0 Chrome/120 Safari/537.36')).toBe('Chromium')
    expect(browserName('Mozilla/5.0 Chrome/120 Safari/537.36 OPR/100')).toBe('Safari')
    expect(browserName('Mozilla/5.0 Firefox/121.0')).toBe('Firefox')
    expect(browserName('Mozilla/5.0 Version/17 Safari/605')).toBe('Safari')
    expect(browserName('curl/8')).toBe('Other')
  })
  it('names the OS, treating a touch Mac as iPadOS', () => {
    expect(osName(edge, 'Win32', 0)).toBe('Windows')
    expect(osName('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15)', 'MacIntel', 5)).toBe('iOS/iPadOS')
    expect(osName('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15)', 'MacIntel', 0)).toBe('macOS')
    expect(osName('Mozilla/5.0 (Linux; Android 14)', 'Linux armv8l', 5)).toBe('Android')
    expect(osName('Mozilla/5.0 (X11; Linux x86_64)', 'Linux x86_64', 0)).toBe('Linux')
  })
  it('words capability rows as the classic page does', () => {
    const rows = browserRows({ ua: edge, platform: 'Win32', touchPoints: 0, mp4: true, webm: false, hls: false, fetch: true, abort: true, pointer: false })
    expect(rows.map((r) => `${r.label}=${r.value}`)).toEqual(['Browser=Edge', 'OS=Windows', 'MP4 / H.264=Supported', 'WebM=Fallback', 'HLS=App fallback', 'Fetch=Available', 'Abort=Available', 'Pointer input=Fallback'])
  })
})
