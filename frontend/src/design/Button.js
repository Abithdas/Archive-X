import { jsx as _jsx } from "react/jsx-runtime";
import { cn } from './cn';
const variants = {
    primary: 'bg-accent text-accent-fg hover:brightness-110',
    outline: 'border border-line bg-surface hover:bg-surface-2',
    ghost: 'hover:bg-surface-2',
};
export function Button({ variant = 'outline', className, ...props }) {
    return (_jsx("button", { type: "button", ...props, className: cn(
        // 40px minimum height: touch-sized on phones, same on desktop.
        'inline-flex min-h-10 items-center justify-center gap-2 rounded-xl px-4 text-sm font-semibold', 'transition-[background-color,filter,transform] duration-150 active:scale-[0.98] disabled:opacity-50', 'focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent', variants[variant], className) }));
}
