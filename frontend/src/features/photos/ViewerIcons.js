import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
/** The viewer's own icon set: one stroke weight and one size, so the toolbar reads as a single family. */
function Svg({ children, fill = false }) {
    return (_jsx("svg", { viewBox: "0 0 24 24", "aria-hidden": "true", focusable: "false", className: "size-[19px] shrink-0", fill: fill ? 'currentColor' : 'none', stroke: "currentColor", strokeWidth: "1.9", strokeLinecap: "round", strokeLinejoin: "round", children: children }));
}
export const IconPrev = () => _jsx(Svg, { children: _jsx("path", { d: "M15 18l-6-6 6-6" }) });
export const IconNext = () => _jsx(Svg, { children: _jsx("path", { d: "M9 18l6-6-6-6" }) });
export const IconPlay = () => _jsx(Svg, { fill: true, children: _jsx("path", { d: "M8 5.5v13a.8.8 0 0 0 1.2.7l10.5-6.5a.8.8 0 0 0 0-1.4L9.2 4.8A.8.8 0 0 0 8 5.5z", stroke: "none" }) });
export const IconPause = () => _jsxs(Svg, { fill: true, children: [_jsx("rect", { x: "6.5", y: "5", width: "4", height: "14", rx: "1.2", stroke: "none" }), _jsx("rect", { x: "13.5", y: "5", width: "4", height: "14", rx: "1.2", stroke: "none" })] });
export const IconZoomIn = () => _jsxs(Svg, { children: [_jsx("circle", { cx: "10.5", cy: "10.5", r: "6.5" }), _jsx("path", { d: "M20 20l-4.8-4.8M10.5 8v5M8 10.5h5" })] });
export const IconZoomOut = () => _jsxs(Svg, { children: [_jsx("circle", { cx: "10.5", cy: "10.5", r: "6.5" }), _jsx("path", { d: "M20 20l-4.8-4.8M8 10.5h5" })] });
export const IconFit = () => _jsxs(Svg, { children: [_jsx("path", { d: "M4 8V5.5A1.5 1.5 0 0 1 5.5 4H8M16 4h2.5A1.5 1.5 0 0 1 20 5.5V8M20 16v2.5a1.5 1.5 0 0 1-1.5 1.5H16M8 20H5.5A1.5 1.5 0 0 1 4 18.5V16" }), _jsx("rect", { x: "8.5", y: "9", width: "7", height: "6", rx: "1" })] });
export const IconRotateLeft = () => _jsx(Svg, { children: _jsx("path", { d: "M3.5 12a8.5 8.5 0 1 0 2.5-6L3.5 8.5M3.5 3.5v5h5" }) });
export const IconRotateRight = () => _jsx(Svg, { children: _jsx("path", { d: "M20.5 12A8.5 8.5 0 1 1 18 6l2.5 2.5M20.5 3.5v5h-5" }) });
export const IconStar = ({ on }) => _jsx(Svg, { fill: on, children: _jsx("path", { d: "M12 3.2l2.6 5.5 6 .8-4.4 4.2 1.1 6-5.3-2.9-5.3 2.9 1.1-6L3.4 9.5l6-.8L12 3.2z" }) });
export const IconFullscreen = ({ on }) => (on
    ? _jsx(Svg, { children: _jsx("path", { d: "M9 4v4a1 1 0 0 1-1 1H4M15 4v4a1 1 0 0 0 1 1h4M9 20v-4a1 1 0 0 0-1-1H4M15 20v-4a1 1 0 0 1 1-1h4" }) })
    : _jsx(Svg, { children: _jsx("path", { d: "M4 9V5a1 1 0 0 1 1-1h4M15 4h4a1 1 0 0 1 1 1v4M20 15v4a1 1 0 0 1-1 1h-4M9 20H5a1 1 0 0 1-1-1v-4" }) }));
export const IconClose = () => _jsx(Svg, { children: _jsx("path", { d: "M6 6l12 12M18 6L6 18" }) });
