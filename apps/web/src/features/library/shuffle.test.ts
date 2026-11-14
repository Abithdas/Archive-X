import { describe, expect, it } from 'vitest'
import { downloadedMessage, externalBody, failedMessage, isAndroid, isIOS, isRemoteHost, mobileVlcUrl, playable, playerName, playlistItems, routeFor, shuffled, startedMessage, startingMessage } from './shuffle'

const v = (id: number, name = '') => ({ _cid: '-1', id, name })

describe('shuffle', () => {
  it('keeps every video exactly once and does not touch the input', () => {
    const src = [1, 2, 3, 4, 5, 6, 7, 8]
    const out = shuffled(src)
    expect([...out].sort()).toEqual(src)
    expect(src).toEqual([1, 2, 3, 4, 5, 6, 7, 8])
  })
  it('is deterministic for a given random source', () => {
    const seq = [0.1, 0.9, 0.5, 0.3]
    const make = () => { let i = 0; return () => seq[i++ % seq.length]! }
    expect(shuffled([1, 2, 3, 4, 5], make())).toEqual(shuffled([1, 2, 3, 4, 5], make()))
    expect(shuffled([1, 2, 3, 4, 5], () => 0)).toEqual([2, 3, 4, 5, 1])   // always swapping with index 0
    expect(shuffled([], () => 0.5)).toEqual([])
    expect(shuffled([1], () => 0.5)).toEqual([1])
  })
  it('drops videos that cannot be played', () => {
    expect(playable([v(1), { _cid: null as unknown as string, id: 2 }, { _cid: '-1', id: null as unknown as number }]).map((x) => x.id)).toEqual([1])
  })
  it('builds the playlist and the request the server expects', () => {
    const items = playlistItems([v(7, 'Scene'), v(8)])
    expect(items).toEqual([{ channel_id: '-1', message_id: 7, name: 'Scene' }, { channel_id: '-1', message_id: 8, name: 'video 8' }])
    expect(externalBody('mpc', items)).toEqual({ player: 'mpc', random: true, playlist: items })
  })
  it('decides how the playlist reaches the player', () => {
    expect(routeFor('vlc', false, false)).toBe('launch')
    expect(routeFor('mpc', false, true)).toBe('launch')
    expect(routeFor('vlc', true, true)).toBe('mobile-vlc')
    expect(routeFor('vlc', true, false)).toBe('download')
    expect(routeFor('mpc', true, true)).toBe('download')
    expect(isRemoteHost('127.0.0.1')).toBe(false)
    expect(isRemoteHost('localhost')).toBe(false)
    expect(isRemoteHost('192.168.1.20')).toBe(true)
  })
  it('recognises iOS (including iPadOS posing as a Mac) and Android', () => {
    expect(isIOS('Mozilla/5.0 (iPhone; CPU iPhone OS 17)', 'iPhone', 5)).toBe(true)
    expect(isIOS('Mozilla/5.0 (Macintosh)', 'MacIntel', 5)).toBe(true)
    expect(isIOS('Mozilla/5.0 (Macintosh)', 'MacIntel', 0)).toBe(false)
    expect(isAndroid('Mozilla/5.0 (Linux; Android 14)')).toBe(true)
  })
  it('builds the mobile VLC hand-off', () => {
    expect(mobileVlcUrl('http://192.168.1.2:8443/p.m3u', true, false)).toBe('intent://192.168.1.2:8443/p.m3u#Intent;scheme=http;type=audio/x-mpegurl;package=org.videolan.vlc;end')
    expect(mobileVlcUrl('http://h/p.m3u', false, true)).toBe('vlc-x-callback://x-callback-url/stream?url=http%3A%2F%2Fh%2Fp.m3u')
    expect(mobileVlcUrl('http://h/p.m3u', false, false)).toBe('')
  })
  it('words the messages like the classic page', () => {
    expect(playerName('mpc')).toBe('MPC-HC')
    expect(playerName('vlc')).toBe('VLC')
    expect(startingMessage('mpc', 1)).toBe('Starting random MPC-HC playlist: 1 video…')
    expect(startingMessage('vlc', 2500)).toBe(`Starting random VLC playlist: ${(2500).toLocaleString()} videos…`)
    expect(failedMessage('vlc', 'VLC was not found on this PC')).toBe('VLC shuffle could not start: VLC was not found on this PC')
    expect(failedMessage('mpc')).toBe('MPC-HC shuffle could not start: unknown error')
    expect(startedMessage({ player: 'VLC', count: 3 }, 'vlc', 9, true)).toBe('VLC shuffled playlist started: 3 videos (on the PC)')
    expect(startedMessage({}, 'mpc', 1, false)).toBe('MPC-HC shuffled playlist started: 1 video')
    expect(downloadedMessage('mpc')).toBe('Random playlist downloaded — open it in MPC-HC on this device.')
  })
})
