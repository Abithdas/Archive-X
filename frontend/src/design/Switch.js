import { jsx as _jsx } from "react/jsx-runtime";
import { cn } from './cn';
/** On/off toggle (role=switch) in the classic style: accent track, round knob. */
export function Switch({ checked, onChange, label, disabled }) {
    return (_jsx("button", { type: "button", role: "switch", "aria-checked": checked, "aria-label": label, disabled: disabled, onClick: () => onChange(!checked), className: cn('h-6 w-11 shrink-0 rounded-full p-0.5 transition-colors disabled:opacity-50', checked ? 'bg-accent' : 'bg-surface-3'), children: _jsx("span", { className: cn('block size-5 rounded-full bg-bg transition-transform', checked && 'translate-x-5') }) }));
}
