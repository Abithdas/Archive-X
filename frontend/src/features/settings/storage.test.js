import { describe, expect, it } from 'vitest';
import { DEFAULT_POLICY, backupFrom, backupPasswordProblem, backupRows, backupSettingsBody, backupStatusLine, moveBlocked, moveProblem, moveWarning, policyBody, policyFrom, policyProblem, restorePreview, retentionNote, savedMessage, usageCards } from './storage';
const fmt = (b) => `${b}B`;
describe('cache policy', () => {
    it('loads the server values and falls back to classic defaults', () => {
        expect(policyFrom({ range_cache_budget_gib: 100, range_cache_retention: 'manual', playback_readahead_seconds: 300, playback_ram_cache_mb: 512, playback_connection_profile: 'fast' })).toEqual({ budget: 100, retention: 'manual', readahead: 300, ram: 512, profile: 'fast' });
        expect(policyFrom(undefined)).toEqual(DEFAULT_POLICY);
        expect(policyFrom({})).toEqual(DEFAULT_POLICY);
    });
    it('accepts whole GiB between 1 and 500 only', () => {
        expect([policyProblem({ ...DEFAULT_POLICY, budget: 0 }), policyProblem({ ...DEFAULT_POLICY, budget: 501 }), policyProblem({ ...DEFAULT_POLICY, budget: 2.5 })].every(Boolean)).toBe(true);
        expect(policyProblem({ ...DEFAULT_POLICY, budget: 500 })).toBe('');
    });
    it('sends the exact request body', () => expect(policyBody({ budget: 25, retention: 'idle_10m', readahead: 60, ram: 64, profile: 'gentle' })).toEqual({ budget_gib: 25, retention: 'idle_10m', readahead_seconds: 60, ram_mb: 64, connection_profile: 'gentle' }));
    it('explains each retention choice and what was saved', () => {
        expect(retentionNote('manual')).toContain('kept until you clear them');
        expect(retentionNote('idle_10m')).toContain('10 minutes idle');
        expect(retentionNote('idle_30m')).toContain('30 minutes idle');
        expect(savedMessage({ budget_gib: 50, retention: 'idle_30m', evicted_bytes: 2048 }, fmt)).toBe('Saved · 50 GiB · playback cache cleared after 30 minutes idle · removed 2048B of inactive cache');
        expect(savedMessage({ budget_gib: 10, retention: 'manual' }, fmt)).toBe('Saved · 10 GiB · playback cache kept until manually cleared');
    });
    it('shows the six usage cards', () => {
        const c = usageCards({ range_cache_bytes: 5, range_cache_budget_bytes: 50, range_cache_entries: 2, range_cache_complete: 1, kept_entries: 1, kept_bytes: 7, snapshots_bytes: 118 }, fmt);
        expect(c.map((x) => x.value)).toEqual(['5B of 50B', '2 entries · 1 complete', '1 video · 7B', '118B', '0B', '0B']);
    });
});
describe('data folder move', () => {
    it('warns in the classic order: environment, running jobs, staged move', () => {
        expect(moveWarning({ environment_override: true, blocking_jobs: 2 })).toContain('ARCHIVE_X_DATA_DIR');
        expect(moveWarning({ blocking_jobs: 2 })).toContain('2 running/queued job(s)');
        expect(moveWarning({ pending_path: 'D:\\x' })).toContain('staged safely');
        expect(moveWarning({})).toBe('');
    });
    it('blocks the move while the environment decides or jobs run', () => {
        expect([moveBlocked({ environment_override: true }), moveBlocked({ blocking_jobs: 1 }), moveBlocked({})]).toEqual([true, true, false]);
        expect(moveProblem('  ')).toBe('Select a destination folder first.');
        expect(moveProblem('D:\\data')).toBe('');
    });
});
describe('backups', () => {
    it('reads settings with classic defaults and writes them back', () => {
        expect(backupFrom({})).toEqual({ enabled: false, repository: '', daily: 7, weekly: 4, monthly: 3, time: '02:00' });
        const b = backupFrom({ settings: { enabled: true, repository: 'D:\\bk', retention_daily: 0, retention_weekly: 2, retention_monthly: 1, schedule_time: '03:30' } });
        expect(b).toEqual({ enabled: true, repository: 'D:\\bk', daily: 0, weekly: 2, monthly: 1, time: '03:30' });
        expect(backupSettingsBody(b)).toEqual({ enabled: true, repository: 'D:\\bk', daily: 0, weekly: 2, monthly: 1, schedule_time: '03:30' });
    });
    it('reports the last backup or error', () => {
        expect(backupStatusLine({})).toBe('No verified backup has been created yet.');
        expect(backupStatusLine({ settings: { last_backup: '2026-01-01' } })).toBe('Last verified backup: 2026-01-01');
        expect(backupStatusLine({ settings: { last_backup: 'x', last_error: 'disk full' } })).toBe('Last backup error: disk full');
    });
    it('requires 8+ characters when a password is given, and none when blank', () => {
        expect(backupPasswordProblem('short')).toBe('Backup password must be at least 8 characters.');
        expect(backupPasswordProblem('')).toBe('');
        expect(backupPasswordProblem('long-enough')).toBe('');
    });
    it('lists history rows', () => expect(backupRows({ backups: [{ name: 'a.axbackup', path: 'D:\\a.axbackup', created_at: 't', size: 5, files: 2, verified: true }] })).toEqual([{ name: 'a.axbackup', path: 'D:\\a.axbackup', created: 't', size: 5, files: 2, verified: true }]));
    it('previews a restore, and says when jobs must stop first', () => {
        expect(restorePreview({ manifest: { application_version: 'v29', files: [1, 2, 3], encrypted: true }, active_jobs: 0 })).toBe('Valid backup · v29 · 3 files · encrypted · restart required. Ready to stage.');
        expect(restorePreview({ manifest: { files: [] }, active_jobs: 1 })).toContain('Stop active jobs before restore.');
    });
});
