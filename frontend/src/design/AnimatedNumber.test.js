import { jsx as _jsx } from "react/jsx-runtime";
import { render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { AnimatedNumber } from './AnimatedNumber';
import { Empty } from './Empty';
describe('AnimatedNumber', () => {
    it('shows the first value as is, then settles on the new one', async () => {
        const { rerender, container } = render(_jsx(AnimatedNumber, { value: 1200 }));
        expect(container).toHaveTextContent('1,200');
        rerender(_jsx(AnimatedNumber, { value: 1500 }));
        await waitFor(() => expect(container).toHaveTextContent('1,500'), { timeout: 2000 });
    });
});
describe('Empty', () => {
    it('shows what is missing and the next step', () => {
        render(_jsx(Empty, { icon: "user", title: "No people yet", action: _jsx("button", { type: "button", children: "Find people" }), children: "Press the button." }));
        expect(screen.getByText('No people yet')).toBeInTheDocument();
        expect(screen.getByText('Press the button.')).toBeInTheDocument();
        expect(screen.getByRole('button', { name: 'Find people' })).toBeInTheDocument();
    });
});
