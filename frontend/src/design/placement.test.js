import { describe, expect, it } from 'vitest';
import { dropdownPosition, zoomOf } from './placement';
const rect = (left, top, w = 40, h = 40) => ({ left, top, right: left + w, bottom: top + h, width: w, height: h, x: left, y: top, toJSON: () => ({}) });
const el = (offset, real) => ({ offsetWidth: offset, getBoundingClientRect: () => ({ width: real }) });
describe('menu placement under zoom', () => {
    it('reads the zoom from how much bigger the element is on screen than in CSS', () => {
        expect(zoomOf(el(100, 115))).toBeCloseTo(1.15);
        expect(zoomOf(el(100, 100))).toBe(1);
    });
    it('falls back to 1 when there is nothing to measure', () => {
        expect(zoomOf(null)).toBe(1);
        expect(zoomOf(el(0, 0))).toBe(1);
    });
    it('opens below the button and right-aligns to it at 100%', () => {
        expect(dropdownPosition(rect(500, 100), el(1, 1), 200, 100, 'right', { w: 1000, h: 800 })).toMatchObject({ left: 340, top: 144 });
    });
    it('converts screen pixels so the menu lands under the button at 130% zoom', () => {
        // button on screen at x=650 (500 css px * 1.3), y=130; screen 1300×1040 = 1000×800 css px
        const p = dropdownPosition(rect(650, 130, 52, 52), el(40, 52), 200, 100, 'left', { w: 1300, h: 1040 });
        expect(p.left).toBeCloseTo(500);
        expect(p.top).toBeCloseTo(100 + 40 + 4);
    });
    it('flips above when there is no room below, and stays on screen', () => {
        expect(dropdownPosition(rect(10, 760), el(1, 1), 200, 100, 'right', { w: 1000, h: 800 })).toMatchObject({ left: 8, top: 656 });
    });
    it('limits the height and uses the roomier side when the menu fits neither above nor below', () => {
        const p = dropdownPosition(rect(100, 300), el(1, 1), 200, 500, 'left', { w: 1000, h: 700 });
        expect(p.maxHeight).toBeLessThan(500);
        expect(p.top + p.maxHeight).toBeLessThanOrEqual(700);
        expect(p.top).toBeGreaterThanOrEqual(8);
    });
});
