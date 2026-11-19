import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { App } from './app/App'
import { ErrorBoundary } from './app/ErrorScreen'
import { installErrorLog } from './app/errorLog'
import { installExternalLinks } from './app/externalLinks'
import { setupPwa } from './app/pwa'
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
installExternalLinks()
setupPwa()
createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <ErrorBoundary><App /></ErrorBoundary>
  </StrictMode>,
)
