import { jsx as _jsx } from "react/jsx-runtime";
import { Toaster } from 'sonner';
/** Sonner renders into the top layer, so toasts stay above dialogs (the legacy UI needed special handling for this). */
export function AppToaster() {
    return (_jsx(Toaster, { position: "bottom-right", closeButton: true, toastOptions: {
            classNames: { toast: '!bg-surface !text-fg !border !border-line !rounded-2xl', description: '!text-muted' },
        } }));
}
