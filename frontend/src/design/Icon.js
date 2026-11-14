import { jsx as _jsx } from "react/jsx-runtime";
/** One glyph from the backend's icon sprite, identical to the classic UI's icons. */
export function Icon({ name, className }) {
    return (_jsx("svg", { className: `ax-icon ${className ?? ''}`, "aria-hidden": "true", focusable: "false", children: _jsx("use", { href: `/static/icons/ax-icons.svg#ax-${name}` }) }));
}
