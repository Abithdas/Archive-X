import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useQueryClient } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { toast } from 'sonner';
import { ApiError, api } from '@/api/client';
import { Icon } from '@/design/Icon';
import { cn } from '@/design/cn';
import { favLabel, favText } from './studio';
import { ACTION } from './EntityHero';
/** Star toggle for a performer or studio; shows the server's answer, not a guess. */
export function FavoriteButton({ kind, id, initial }) {
    const qc = useQueryClient();
    const [on, setOn] = useState(initial);
    const [busy, setBusy] = useState(false);
    useEffect(() => setOn(initial), [initial]);
    const toggle = async () => {
        setBusy(true);
        try {
            const r = await api(`/api/entities/${kind}/${id}/favorite`, { method: 'POST' });
            setOn(Boolean(r.favorite));
            void qc.invalidateQueries({ queryKey: [kind === 'studio' ? 'studio' : 'performer', id] });
        }
        catch (e) {
            toast.error(e instanceof ApiError && e.detail ? e.detail : 'Could not change the favourite.');
        }
        finally {
            setBusy(false);
        }
    };
    return (_jsxs("button", { type: "button", disabled: busy, "aria-pressed": on, "aria-label": favLabel(on), title: favLabel(on), onClick: () => void toggle(), className: cn(ACTION, on && 'border-warn text-warn'), children: [_jsx(Icon, { name: "star", className: cn('size-4', on && 'drop-shadow') }), favText(on)] }));
}
