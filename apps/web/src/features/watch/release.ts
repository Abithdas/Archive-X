/** Tell the server the viewer left this video. Best effort: a failure only means its cache is cleared a little later. */
export function releasePlayback(channelId: string, messageId: number, reason = 'viewer_left') {
  const id = Number(channelId)
  if (!Number.isFinite(id) || !Number.isFinite(messageId)) return
  try {
    void fetch('/api/play/release', {
      method: 'POST', keepalive: true, credentials: 'same-origin', headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
      body: JSON.stringify({ channel_id: id, message_id: messageId, reason }),
    }).catch(() => undefined)
  } catch { /* nothing to do */ }
}
