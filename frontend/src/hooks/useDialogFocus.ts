import { useEffect, useRef, type RefObject } from 'react'

// 共用弹层的焦点约束、退出恢复与背景滚动锁。
export function useDialogFocus(ref: RefObject<HTMLElement | null>, open: boolean, onClose: () => void, returnFocus?: RefObject<HTMLElement | null>) {
  const close = useRef(onClose)
  close.current = onClose

  useEffect(() => {
    if (!open || !ref.current) return
    const previous = returnFocus?.current ?? document.activeElement as HTMLElement | null
    const overflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    const focusable = () => Array.from(ref.current!.querySelectorAll<HTMLElement>(
      'a[href], button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex="0"]',
    )).filter((el) => el.getClientRects().length > 0)
    ;(focusable()[0] ?? ref.current).focus()

    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.preventDefault()
        e.stopPropagation()
        close.current()
      } else if (e.key === 'Tab') {
        const items = focusable()
        if (!items.length) {
          e.preventDefault()
          ref.current?.focus()
        } else if (e.shiftKey && (document.activeElement === items[0] || !ref.current?.contains(document.activeElement))) {
          e.preventDefault()
          items[items.length - 1].focus()
        } else if (!e.shiftKey && (document.activeElement === items[items.length - 1] || !ref.current?.contains(document.activeElement))) {
          e.preventDefault()
          items[0].focus()
        }
      }
    }
    document.addEventListener('keydown', onKey, true)
    return () => {
      document.removeEventListener('keydown', onKey, true)
      document.body.style.overflow = overflow
      if (previous?.isConnected) previous.focus({ preventScroll: true })
    }
  }, [open, ref, returnFocus])
}
