/** Sign-in and first-run setup rules (same wording and limits as the classic login page). */
export const MIN_PASSWORD = 12
export const DEFAULT_ADMIN = 'admin'
export interface LoginHint { setup_required?: boolean; default_login?: boolean; login_required?: boolean }

export const SIGN_IN_SUBTITLE = 'Sign in to continue'
export const SETUP_SUBTITLE = 'Create your administrator account'
export const SETUP_HINT = 'First-run security setup is required. Create a new administrator password (12+ characters) before signing in.'
export const SETUP_DONE = 'Administrator created. Sign in with your new credentials.'
export const UNREACHABLE = 'Cannot reach the app. Is it still running?'
export const WRONG = 'Wrong username or password.'
export const TOO_MANY = 'Too many attempts. Wait a few minutes.'
export const NO_SESSION = 'Your browser did not keep the sign-in session. Open the app in a new tab and sign in again.'
export const SETUP_FAILED = 'Could not complete administrator setup.'

/** What to tell the person after a failed sign-in. A 428 means setup has not been done yet. */
export function loginMessage(status: number | null, detail = ''): { text: string; needsSetup: boolean } {
  if (status === null || status === 0) return { text: UNREACHABLE, needsSetup: false }
  if (status === 428) return { text: SETUP_HINT, needsSetup: true }
  if (status === 429) return { text: detail || TOO_MANY, needsSetup: false }
  return { text: WRONG, needsSetup: false }
}
/** Problems with the setup form, found before anything is sent; '' when it is fine. */
export function setupProblem(username: string, password: string, confirmation: string): string {
  if (!username.trim()) return 'Username is required'
  if (username.trim().length > 80) return 'Username must be 80 characters or fewer'
  if (password.length < MIN_PASSWORD) return `Password must be at least ${MIN_PASSWORD} characters`
  if (password !== confirmation) return 'Passwords do not match'
  return ''
}
export const setupBody = (username: string, password: string, confirmation: string) => ({ username: username.trim() || DEFAULT_ADMIN, password, confirmation })
