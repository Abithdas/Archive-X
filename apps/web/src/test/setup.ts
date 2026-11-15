import { configure } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
window.scrollTo = () => {}

// Async UI chains (router, queries, dialogs) can take longer than the 1 s default on a busy machine.
configure({ asyncUtilTimeout: 4000 })

// jsdom has no canvas; code that measures text with one (the search box's caret) falls back to an estimate, so say "none" quietly.
HTMLCanvasElement.prototype.getContext = (() => null) as typeof HTMLCanvasElement.prototype.getContext
