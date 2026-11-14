import { jsx as _jsx } from "react/jsx-runtime";
import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { App } from './app/App';
import { ErrorBoundary } from './app/ErrorScreen';
import { installErrorLog } from './app/errorLog';
import { setupPwa } from './app/pwa';
import { applyScale, applyTheme, storedScale, storedTheme } from './theme/themes';
import './styles.css';
import { installSelectMenu } from './design/selectMenu';
// Apply the remembered theme and zoom before the first render so there is no flash of the default look.
const t = storedTheme();
applyTheme(t.preset, t.mode);
applyScale(storedScale());
installSelectMenu();
installErrorLog();
setupPwa();
createRoot(document.getElementById('root')).render(_jsx(StrictMode, { children: _jsx(ErrorBoundary, { children: _jsx(App, {}) }) }));
