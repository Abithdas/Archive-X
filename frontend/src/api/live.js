import { useEffect, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { queryKeys } from './hooks';
/** Parse one /ws frame; the server sends the same JSON as /api/status. Returns null for anything else. */
export function parseStatusFrame(data) {
    if (typeof data !== 'string')
        return null;
    try {
        const parsed = JSON.parse(data);
        return parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? parsed : null;
    }
    catch {
        return null;
    }
}
/** Reconnect delay with exponential backoff, capped. */
export const backoffMs = (attempt) => Math.min(15_000, 500 * 2 ** Math.min(attempt, 5));
/**
 * Bridge the /ws stream into the TanStack Query cache so every component reads live data through useStatus().
 * Enabled only while signed in. Replaces the legacy startOpsSocket/checkOpsSocketHealth/retryOpsSocket trio.
 */
export function useLiveStatus(enabled) {
    const client = useQueryClient();
    const [state, setState] = useState('connecting');
    useEffect(() => {
        if (!enabled)
            return;
        let socket = null;
        let timer;
        let attempt = 0;
        let stopped = false;
        const connect = () => {
            if (stopped)
                return;
            setState('connecting');
            const scheme = location.protocol === 'https:' ? 'wss' : 'ws';
            socket = new WebSocket(`${scheme}://${location.host}/ws`);
            socket.onopen = () => {
                attempt = 0;
                setState('live');
            };
            socket.onmessage = (event) => {
                const frame = parseStatusFrame(event.data);
                if (frame)
                    client.setQueryData(queryKeys.status, frame);
            };
            socket.onclose = () => {
                if (stopped)
                    return;
                setState('offline');
                timer = setTimeout(connect, backoffMs(attempt++));
            };
            socket.onerror = () => socket?.close();
        };
        connect();
        return () => {
            stopped = true;
            if (timer)
                clearTimeout(timer);
            socket?.close();
        };
    }, [enabled, client]);
    return state;
}
