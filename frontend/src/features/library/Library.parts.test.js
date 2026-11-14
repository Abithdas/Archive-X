import { jsx as _jsx } from "react/jsx-runtime";
import { act, fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it } from 'vitest';
import { ActiveFilters } from './ActiveFilters';
import { Pagination, pageWindow } from './Pagination';
import { NO_FILTERS, useLibraryStore } from './store';
describe('pageWindow', () => {
    it('lists every page when there are few', () => expect(pageWindow(2, 5)).toEqual([1, 2, 3, 4, 5]));
    it('keeps first, last and the neighbours of the current page', () => expect(pageWindow(4, 20)).toEqual([1, 2, 3, 4, 5, '…', 19, 20]));
    it('shows a single gap at each end', () => {
        expect(pageWindow(1, 8)).toEqual([1, 2, '…', 7, 8]);
        expect(pageWindow(8, 8)).toEqual([1, 2, '…', 7, 8]);
    });
});
describe('Pagination', () => {
    it('moves between pages, and disables first/previous on the first page', () => {
        const pages = [];
        render(_jsx(Pagination, { page: 0, pages: 8, per: 24, onPage: (n) => pages.push(n), onPer: () => { } }));
        expect(screen.getByRole('button', { name: 'First page' })).toBeDisabled();
        fireEvent.click(screen.getByRole('button', { name: 'Last page' }));
        fireEvent.click(screen.getByRole('button', { name: '2' }));
        expect(pages).toEqual([7, 1]);
        const input = screen.getByLabelText('Go to page');
        fireEvent.change(input, { target: { value: '99' } });
        fireEvent.keyDown(input, { key: 'Enter' });
        expect(pages.at(-1)).toBe(7);
    });
    it('reports the chosen page size', () => {
        const sizes = [];
        render(_jsx(Pagination, { page: 0, pages: 2, per: 24, onPage: () => { }, onPer: (n) => sizes.push(n) }));
        fireEvent.change(screen.getByLabelText('Items per page'), { target: { value: '48' } });
        expect(sizes).toEqual([48]);
    });
});
describe('ActiveFilters', () => {
    beforeEach(() => useLibraryStore.setState({ filters: NO_FILTERS }));
    it('renders nothing without filters, one removable chip per filter otherwise', () => {
        const { container } = render(_jsx(ActiveFilters, {}));
        expect(container).toBeEmptyDOMElement();
        act(() => useLibraryStore.getState().setFilters({ ...NO_FILTERS, studio: 'Northlight', tag: 'pov' }));
        expect(screen.getByText(/Studio: Northlight/)).toBeInTheDocument();
        fireEvent.click(screen.getByRole('button', { name: 'Remove Studio filter' }));
        expect(screen.queryByText(/Studio: Northlight/)).not.toBeInTheDocument();
        expect(screen.getByText(/Tag: pov/)).toBeInTheDocument();
    });
});
describe('Toolbar shuffle', () => {
    it('has VLC and MPC-HC shuffle buttons that report the player', async () => {
        const { Toolbar } = await import('./Toolbar');
        const { withRouter } = await import('@/test/withRouter');
        const { fireEvent, screen } = await import('@testing-library/react');
        const seen = [];
        withRouter(_jsx(Toolbar, { onRefresh: () => { }, refreshing: false, onShuffle: (p) => seen.push(p), shuffling: false }));
        fireEvent.click(await screen.findByRole('button', { name: 'Shuffle in VLC' }));
        fireEvent.click(screen.getByRole('button', { name: 'Shuffle in MPC-HC' }));
        expect(seen).toEqual(['vlc', 'mpc']);
    });
});
