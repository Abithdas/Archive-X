import { jsx as _jsx } from "react/jsx-runtime";
import { screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { withRouter } from '@/test/withRouter';
import { NamedPoster, PerformerPoster, PosterGrid, StudioPoster } from './PosterCard';
describe('related-entity posters', () => {
    it('a similar performer shows its match chip and video count, and opens that performer', async () => {
        withRouter(_jsx(PosterGrid, { label: "Similar Performers", children: _jsx(PerformerPoster, { id: 9, name: "Zoe Ray", count: 4, similar: { similarity_score: 0.86, matched_detail_count: 5, compared_detail_count: 6, matched_fields: ['Country'], metadata_coverage: 1 } }) }));
        const link = await screen.findByRole('link', { name: 'Open performer Zoe Ray' });
        expect(link).toHaveAttribute('href', '/performers/9');
        expect(within(link).getByText('86% match · 5/6 traits')).toHaveAttribute('title', expect.stringContaining('Matched attributes: Country'));
        expect(within(link).getByText('4 videos')).toBeInTheDocument();
    });
    it('a performer without a score has no match chip', async () => {
        withRouter(_jsx(PosterGrid, { label: "Performers", children: _jsx(PerformerPoster, { id: 3, name: "Mia", count: 1 }) }));
        const link = await screen.findByRole('link', { name: 'Open performer Mia' });
        expect(link).toHaveTextContent('1 video');
        expect(link).not.toHaveTextContent('match');
    });
    it('a studio poster opens the studio and counts videos', async () => {
        withRouter(_jsx(PosterGrid, { label: "Studios", children: _jsx(StudioPoster, { id: 5, name: "Aurora", count: 2 }) }));
        const link = await screen.findByRole('link', { name: 'Open studio Aurora' });
        expect(link).toHaveAttribute('href', '/studios/5');
        expect(link).toHaveTextContent('2 videos');
    });
    it('a performer with no profile is shown but is not a link', async () => {
        withRouter(_jsx(PosterGrid, { label: "Performers", children: _jsx(NamedPoster, { name: "Unlisted", count: 3 }) }));
        expect(await screen.findByText('Unlisted')).toBeInTheDocument();
        expect(screen.queryByRole('link')).not.toBeInTheDocument();
    });
});
