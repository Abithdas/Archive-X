import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { RouterProvider } from '@tanstack/react-router'
import { useEffect } from 'react'
import { SESSION_LOST_EVENT } from '@/api/client'
import { queryKeys } from '@/api/hooks'
import { MotionProvider } from '@/design/motion'
import { AppToaster } from '@/design/toast'
import { LoginGate } from '@/features/auth/LoginGate'
import { router } from './router'

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: false } },
})

export function App() {
  // Any 401 from any request re-checks the session, which shows the sign-in form again.
  useEffect(() => {
    const onLost = () => void queryClient.invalidateQueries({ queryKey: queryKeys.status })
    window.addEventListener(SESSION_LOST_EVENT, onLost)
    return () => window.removeEventListener(SESSION_LOST_EVENT, onLost)
  }, [])
  return (
    <QueryClientProvider client={queryClient}>
      <MotionProvider>
        <LoginGate>
          <RouterProvider router={router} />
        </LoginGate>
        <AppToaster />
      </MotionProvider>
    </QueryClientProvider>
  )
}
