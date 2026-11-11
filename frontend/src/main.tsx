import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { App } from './app/App'
import { applyScale, applyTheme, storedScale, storedTheme } from './theme/themes'
import './styles.css'
import { installSelectMenu } from './design/selectMenu'

// Apply the remembered theme and zoom before the first render so there is no flash of the default look.
const t = storedTheme()
applyTheme(t.preset, t.mode)
applyScale(storedScale())

installSelectMenu()
createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
