import { Toaster } from 'sonner'

/** Sonner renders into the top layer, so toasts stay above dialogs (the legacy UI needed special handling for this). */
export function AppToaster() {
  return (
    <Toaster
      position="bottom-right"
      closeButton
      toastOptions={{
        classNames: { toast: '!bg-surface !text-fg !border !border-line !rounded-2xl', description: '!text-muted' },
      }}
    />
  )
}
