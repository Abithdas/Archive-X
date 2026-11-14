import { jsx as _jsx, Fragment as _Fragment, jsxs as _jsxs } from "react/jsx-runtime";
import { act, fireEvent, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import { withRouter } from '@/test/withRouter';
import { AccountMenu } from './AccountMenu';
import { CommandPalette } from './CommandPalette';
import { useUi } from './ui';
beforeAll(() => {
    HTMLDialogElement.prototype.showModal = function () { this.setAttribute('open', ''); };
    HTMLDialogElement.prototype.close = function () { this.removeAttribute('open'); };
    HTMLElement.prototype.hidePopover = function () { };
});
afterEach(() => { vi.unstubAllGlobals(); act(() => useUi.setState({ palette: false })); });
const json = (body, status = 200) => new Response(JSON.stringify(body), { status });
describe('CommandPalette', () => {
    const open = async () => {
        withRouter(_jsx(CommandPalette, {}));
        act(() => useUi.getState().setPalette(true));
        return screen.findByRole('combobox', { name: 'Search commands' });
    };
    it('lists pages and actions in groups', async () => {
        await open();
        expect(screen.getAllByRole('option')).toHaveLength(12);
        expect(screen.getByText('Go to')).toBeInTheDocument();
        expect(screen.getByText('Actions')).toBeInTheDocument();
        expect(screen.getByRole('option', { name: /Open Photos/ })).toHaveAttribute('aria-selected', 'true');
    });
    it('filters as you type and says so when nothing matches', async () => {
        const box = await open();
        fireEvent.change(box, { target: { value: 'perf' } });
        expect(screen.getAllByRole('option')).toHaveLength(1);
        fireEvent.change(box, { target: { value: 'zzzz' } });
        expect(screen.getByText('No matching commands')).toBeInTheDocument();
    });
    it('moves with the arrow keys, wrapping around, and Enter runs the command and closes', async () => {
        const box = await open();
        fireEvent.keyDown(box, { key: 'ArrowUp' });
        expect(screen.getByRole('option', { name: /Refresh library/ })).toHaveAttribute('aria-selected', 'true');
        fireEvent.keyDown(box, { key: 'ArrowDown' });
        expect(screen.getByRole('option', { name: /Open Photos/ })).toHaveAttribute('aria-selected', 'true');
        fireEvent.keyDown(box, { key: 'Enter' });
        await waitFor(() => expect(useUi.getState().palette).toBe(false));
    });
    it('finds performers and studios by name once you type, and remembers what you opened', async () => {
        vi.stubGlobal('fetch', vi.fn(async (url) => json(url.startsWith('/api/performers') ? [{ id: 4, name: 'Ava Stone', video_count: 3 }, { id: 5, name: 'Zoe Ray', video_count: 9 }] : url.startsWith('/api/studios') ? [{ id: 8, name: 'Avalon Films', video_count: 2 }] : [])));
        localStorage.removeItem('archive-x:palette-recent');
        const box = await open();
        fireEvent.change(box, { target: { value: 'ava' } });
        expect(await screen.findByRole('option', { name: /Ava Stone/ })).toBeInTheDocument();
        expect(screen.getByRole('option', { name: /Avalon Films/ })).toBeInTheDocument();
        expect(screen.queryByRole('option', { name: /Zoe Ray/ })).toBeNull();
        expect(screen.getByText('Performers')).toBeInTheDocument();
        expect(screen.getByText('Studios')).toBeInTheDocument();
        fireEvent.click(screen.getByRole('option', { name: /Ava Stone/ }).querySelector('button'));
        await waitFor(() => expect(useUi.getState().palette).toBe(false));
        expect(localStorage.getItem('archive-x:palette-recent')).toContain('performers:4:Ava Stone');
        act(() => useUi.getState().setPalette(true));
        expect(await screen.findByText('Recent')).toBeInTheDocument();
        expect(screen.getAllByRole('option')[0]).toHaveTextContent('Ava Stone');
    });
    it('"Open Notifications" opens the panel', async () => {
        const box = await open();
        fireEvent.change(box, { target: { value: 'notifications' } });
        fireEvent.keyDown(box, { key: 'Enter' });
        await waitFor(() => expect(useUi.getState().notifications).toBe(true));
        act(() => useUi.setState({ notifications: false }));
    });
});
describe('AccountMenu', () => {
    it('shows the initial and offers Sign out and Exit app in a browser', async () => {
        vi.stubGlobal('fetch', vi.fn(async (url) => (url === '/desktop/info' ? json({ detail: 'x' }, 404) : url === '/api/account/profile' ? json({ name: 'Sam' }) : json({}))));
        withRouter(_jsx(AccountMenu, { live: "live" }));
        const btn = await screen.findByRole('button', { name: 'Account menu' });
        await waitFor(() => expect(btn).toHaveTextContent('S'));
        expect(await screen.findByRole('menuitem', { name: 'Sign out', hidden: true })).toBeInTheDocument();
        expect(screen.getByRole('menuitem', { name: /Exit app/, hidden: true })).toBeInTheDocument();
    });
    it('hides Sign out inside the desktop app', async () => {
        vi.stubGlobal('fetch', vi.fn(async (url) => (url === '/desktop/info' ? json({ desktop: true }) : url === '/api/account/profile' ? json({ name: 'Sam' }) : json({}))));
        withRouter(_jsx(AccountMenu, { live: "live" }));
        await screen.findByRole('button', { name: 'Account menu' });
        await waitFor(() => expect(screen.queryByRole('menuitem', { name: 'Sign out', hidden: true })).not.toBeInTheDocument());
    });
    it('asks before exiting, and only then calls the backend', async () => {
        const f = vi.fn(async (url) => (url === '/api/app/exit' ? json({ ok: true }) : url === '/desktop/info' ? json({ desktop: true }) : json({})));
        vi.stubGlobal('fetch', f);
        withRouter(_jsx(AccountMenu, { live: "live" }));
        fireEvent.click(await screen.findByRole('menuitem', { name: /Exit app/, hidden: true }));
        expect(f.mock.calls.some((c) => c[0] === '/api/app/exit')).toBe(false);
        fireEvent.click(screen.getByRole('button', { name: 'Keep Archive X open' }));
        expect(f.mock.calls.some((c) => c[0] === '/api/app/exit')).toBe(false);
        fireEvent.click(screen.getByRole('menuitem', { name: /Exit app/, hidden: true }));
        fireEvent.click(screen.getByRole('button', { name: 'Exit app' }));
        await waitFor(() => expect(f.mock.calls.some((c) => c[0] === '/api/app/exit')).toBe(true));
        expect(await screen.findByText(/Waiting for the server/)).toBeInTheDocument();
    });
});
describe('Dialog accessibility', () => {
    it('gives every open dialog its own accessible name', async () => {
        const { Dialog } = await import('@/design/Dialog');
        const { render } = await import('@testing-library/react');
        render(_jsxs(_Fragment, { children: [_jsx(Dialog, { open: true, onClose: () => { }, title: "First", children: "a" }), _jsx(Dialog, { open: true, onClose: () => { }, title: "Second", children: "b" })] }));
        expect(screen.getByRole('dialog', { name: 'First' })).toBeInTheDocument();
        expect(screen.getByRole('dialog', { name: 'Second' })).toBeInTheDocument();
    });
});
