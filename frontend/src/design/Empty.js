import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { Icon } from './Icon';
import { cn } from './cn';
/** The same empty state everywhere: a soft icon, what is missing, and what to do next. */
export function Empty({ icon = 'images', title, children, action, className, ...rest }) {
    return (_jsxs("div", { ...rest, className: cn('grid justify-items-center gap-2 rounded-card border border-dashed border-line px-6 py-12 text-center', className), children: [_jsx("span", { "aria-hidden": "true", className: "grid size-12 place-items-center rounded-full bg-surface-2 text-muted", children: _jsx(Icon, { name: icon, className: "size-6" }) }), title ? _jsx("p", { className: "font-semibold", children: title }) : null, children ? _jsx("div", { className: "max-w-md text-sm text-muted", children: children }) : null, action ? _jsx("div", { className: "mt-1", children: action }) : null] }));
}
