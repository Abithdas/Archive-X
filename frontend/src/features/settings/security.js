/** Dashboard login rules, ported from saveWL (classic UI). Order matters: the first problem is the one shown. */
export const MIN_PASSWORD = 12;
export const EMPTY_LOGIN = { username: '', current: '', password: '', repeat: '' };
export function loginProblem(f) {
    if (f.password && f.password !== f.repeat)
        return 'The two passwords do not match.';
    if (f.password && f.password.length < MIN_PASSWORD)
        return `Use at least ${MIN_PASSWORD} characters.`;
    if (!f.current)
        return 'Enter your current password to change login details.';
    if (!f.username.trim())
        return 'Enter a username.';
    return '';
}
/** A blank new password keeps the old one. */
export const loginBody = (f) => ({ username: f.username.trim(), password: f.password, current_password: f.current });
export const savedMessage = (changedPassword) => (changedPassword ? 'Saved. Other devices were signed out.' : 'Saved.');
