/** Folders on this PC are channels of their own with a number far below any Telegram id (the same line the backend draws: telegram_archive/engines/local_scanner.py). */
const LOCAL_BASE = -9_000_000_000_000

export const isLocalId = (id: string | number | null | undefined): boolean => {
  const n = Number(id)
  return Number.isFinite(n) && n < LOCAL_BASE
}

export const isLocalChannel = (c: { telegram_id: string | number; role?: string | null }): boolean => c.role === 'local' || isLocalId(c.telegram_id)

export const LOCAL_LABEL = 'Folder on this PC'
