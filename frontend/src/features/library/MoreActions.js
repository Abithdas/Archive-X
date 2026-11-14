import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useQueryClient } from '@tanstack/react-query';
import { useChannels } from '@/api/hooks';
import { Dialog } from '@/design/Dialog';
import { Icon } from '@/design/Icon';
import { toast } from 'sonner';
import { ENTER_HINT, PICK_CHANNEL_FIRST } from './bulk';
import { useSelection } from './selection';
import { useLibraryStore } from './store';
function Row({ icon, label, onClick, disabled }) {
    return (_jsxs("button", { type: "button", onClick: onClick, disabled: disabled, className: "flex h-12 w-full items-center gap-3 rounded-xl px-3 text-[15px] font-semibold hover:bg-fg/5 disabled:opacity-50", children: [_jsx(Icon, { name: icon, className: "text-muted" }), label] }));
}
/** "More actions": the classic ⋯ menu entries that work without the bulk-edit mode. */
export function MoreActions({ open, onClose, onShuffle }) {
    const channels = useChannels();
    const qc = useQueryClient();
    const { selectAll, clear, channels: selected } = useLibraryStore();
    const selecting = useSelection((st) => st.active);
    const done = (msg) => {
        toast(msg);
        onClose();
    };
    return (_jsx(Dialog, { open: open, onClose: onClose, title: "More actions", children: _jsxs("div", { className: "grid gap-0.5 border-t border-line pt-2", children: [_jsx(Row, { icon: "grid", label: "Select all channels", disabled: !channels.data?.length, onClick: () => { selectAll((channels.data ?? []).map((c) => c.telegram_id)); done('All channels selected'); } }), _jsx(Row, { icon: "close", label: "Clear selection", disabled: !selected.length, onClick: () => { clear(); done('Selection cleared'); } }), _jsx(Row, { icon: "edit", label: selecting ? 'Finish editing multiple videos' : 'Edit multiple videos', onClick: () => {
                        const sel = useSelection.getState();
                        if (sel.active) {
                            sel.exit();
                            return done('Selection mode ended');
                        }
                        if (!selected.length) {
                            onClose();
                            return void toast(PICK_CHANNEL_FIRST);
                        }
                        sel.enter();
                        done(ENTER_HINT);
                    } }), _jsxs("div", { className: "grid gap-0.5 md:hidden", children: ["   ", _jsx(Row, { icon: "brand-vlc", label: "Shuffle in VLC", onClick: () => { onClose(); onShuffle('vlc'); } }), _jsx(Row, { icon: "shuffle", label: "Shuffle in MPC-HC", onClick: () => { onClose(); onShuffle('mpc'); } })] }), _jsx(Row, { icon: "refresh", label: "Reload selected", onClick: () => { void qc.invalidateQueries({ queryKey: ['library'] }); void qc.invalidateQueries({ queryKey: ['channels'] }); done('Reloading the library'); } })] }) }));
}
