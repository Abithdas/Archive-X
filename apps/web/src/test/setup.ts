import { configure } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
window.scrollTo = () => {}

// Async UI chains (router, queries, dialogs) can take longer than the 1 s default on a busy machine.
configure({ asyncUtilTimeout: 4000 })
