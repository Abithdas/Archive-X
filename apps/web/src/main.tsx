import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { App } from './app/App'
import { ErrorBoundary } from './app/ErrorScreen'
import { installErrorLog } from './app/errorLog'
import { installExternalLinks } from './app/externalLinks'
import { setupPwa } from './app/pwa'
import { mayReload } from './app/lazyRetry'
import { applyScale, applyTheme, storedScale, storedTheme } from './theme/themes'
import './styles.css'
import { installSelectMenu } from './design/selectMenu'
import { applyMotion } from './design/motionPref'

// Apply the remembered theme and zoom before the first render so there is no flash of the default look.
const t = storedTheme()
applyTheme(t.preset, t.mode)
applyScale(storedScale())
applyMotion()

installSelectMenu()
installErrorLog()
// A file the page needs for its first paint could not be fetched (an old tab after an update): load the current copy once instead of showing nothing.
window.addEventListener('vite:preloadError', (e) => { if (mayReload()) { e.preventDefault(); window.location.reload() } })
installExternalLinks()
setupPwa()
createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <ErrorBoundary><App /></ErrorBoundary>
  </StrictMode>,
)
