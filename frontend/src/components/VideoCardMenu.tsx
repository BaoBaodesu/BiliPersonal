import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import {
  Ban,
  Clock,
  Copy,
  EyeOff,
  CheckCheck,
  HelpCircle,
  MoreVertical,
  ThumbsUp,
  UserX,
  Tag,
  ArrowLeft,
  Loader2,
} from 'lucide-react'
import type { Video } from '../types'
import { useBlockKeyword, useFeedback } from '../hooks/queries'
import { useToast } from '../stores/ui'
import { ExplainDialog } from './ExplainDialog'

interface Props {
  video: Video
  // 推荐流中才显示“为什么推荐给我”
  inFeed?: boolean
}

export function VideoCardMenu({ video, inFeed = true }: Props) {
  const [open, setOpen] = useState(false)
  const [keywordMode, setKeywordMode] = useState(false)
  const [explain, setExplain] = useState(false)
  const [pos, setPos] = useState({ top: 0, left: 0 })
  const btn = useRef<HTMLButtonElement>(null)
  const menu = useRef<HTMLDivElement>(null)
  const feedback = useFeedback()
  const blockKeyword = useBlockKeyword()
  const toast = useToast((s) => s.show)

  useEffect(() => {
    if (!open) return
    const close = (e: MouseEvent) => {
      if (!menu.current?.contains(e.target as Node) && !btn.current?.contains(e.target as Node)) setOpen(false)
    }
    const esc = (e: KeyboardEvent) => {
      if (e.key === 'Escape' || e.key === 'Tab') {
        if (e.key === 'Escape') e.preventDefault()
        setOpen(false)
      } else if (['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(e.key)) {
        e.preventDefault()
        const items = Array.from(menu.current?.querySelectorAll<HTMLButtonElement>('button:not(:disabled)') ?? [])
        if (!items.length) return
        const index = items.indexOf(document.activeElement as HTMLButtonElement)
        items[e.key === 'Home' ? 0 : e.key === 'End' ? items.length - 1 : (index + (e.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length].focus()
      }
    }
    const scroll = () => setOpen(false)
    document.addEventListener('mousedown', close)
    document.addEventListener('keydown', esc)
    window.addEventListener('scroll', scroll, { passive: true })
    return () => {
      document.removeEventListener('mousedown', close)
      document.removeEventListener('keydown', esc)
      window.removeEventListener('scroll', scroll)
      if (menu.current?.contains(document.activeElement) || document.activeElement === document.body) btn.current?.focus({ preventScroll: true })
    }
  }, [open])

  useLayoutEffect(() => {
    if (!open || !menu.current || !btn.current) return
    const r = btn.current.getBoundingClientRect()
    setPos({
      left: Math.max(8, Math.min(r.right - menu.current.offsetWidth, window.innerWidth - menu.current.offsetWidth - 8)),
      top: Math.max(8, Math.min(r.bottom + 4 + menu.current.offsetHeight > window.innerHeight - 8 ? r.top - menu.current.offsetHeight - 4 : r.bottom + 4, window.innerHeight - menu.current.offsetHeight - 8)),
    })
    menu.current.querySelector<HTMLButtonElement>('button:not(:disabled)')?.focus({ preventScroll: true })
  }, [open, keywordMode])

  const toggle = (e: React.MouseEvent) => {
    e.preventDefault()
    e.stopPropagation()
    const r = btn.current!.getBoundingClientRect()
    const width = 264
    const left = Math.min(Math.max(8, r.right - width), window.innerWidth - width - 8)
    const top = r.bottom + 4
    setPos({ top, left })
    setKeywordMode(false)
    setOpen((o) => !o)
  }

  const act = (action: 'not_interested' | 'block_up' | 'watched' | 'watch_later' | 'like') => {
    feedback.mutate({ video, action })
    setOpen(false)
  }

  // 可选的屏蔽关键词：标签 + 分区
  const keywordOptions = Array.from(new Set([...(video.tags || []), video.tname].filter(Boolean) as string[])).slice(
    0,
    10,
  )

  const item =
    'flex min-h-11 w-full items-center gap-3 px-4 py-2 text-left text-sm text-fg hover:bg-surface-hover focus-visible:bg-surface-hover outline-none disabled:opacity-50'

  return (
    <>
      <button
        ref={btn}
        onClick={toggle}
        aria-label="更多操作"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-busy={feedback.isPending || blockKeyword.isPending}
        disabled={feedback.isPending || blockKeyword.isPending}
        className={`-mr-2 flex h-11 w-11 shrink-0 items-center justify-center rounded-full text-fg transition hover:bg-surface-hover focus-visible:opacity-100 disabled:opacity-60 ${
          open ? 'opacity-100' : 'opacity-100 md:opacity-0 md:group-hover:opacity-100'
        }`}
      >
        {feedback.isPending || blockKeyword.isPending ? <Loader2 size={20} className="animate-spin" /> : <MoreVertical size={20} />}
      </button>
      {open &&
        createPortal(
          <div
            ref={menu}
            role="menu"
            aria-label="视频操作"
            style={{ top: pos.top, left: pos.left }}
            className="fade-in fixed z-50 max-h-[calc(100dvh-16px)] w-66 max-w-[calc(100vw-16px)] overflow-y-auto rounded-xl bg-elevated py-2 shadow-pop"
          >
            {!keywordMode ? (
              <>
                <button role="menuitem" className={item} onClick={() => act('not_interested')}>
                  <EyeOff size={20} /> 不感兴趣
                </button>
                <button role="menuitem" className={item} onClick={() => act('block_up')}>
                  <UserX size={20} /> 不看这个 UP：<span className="truncate">{video.author}</span>
                </button>
                <button
                  role="menuitem"
                  className={item}
                  onClick={() => setKeywordMode(true)}
                  disabled={!keywordOptions.length}
                >
                  <Ban size={20} /> 屏蔽此关键词…
                </button>
                <button role="menuitem" className={item} onClick={() => act('watched')}>
                  <CheckCheck size={20} /> 看过了
                </button>
                <div className="my-2 border-t border-line" />
                <button role="menuitem" className={item} onClick={() => act('watch_later')}>
                  <Clock size={20} /> 稍后再看
                </button>
                <button role="menuitem" className={item} onClick={() => act('like')}>
                  <ThumbsUp size={20} /> 喜欢
                </button>
                <button
                  role="menuitem"
                  className={item}
                  onClick={async () => {
                    try {
                      await navigator.clipboard.writeText(video.bvid)
                      toast(`已复制 ${video.bvid}`)
                    } catch {
                      toast('复制失败，请检查剪贴板权限后重试')
                    }
                    setOpen(false)
                  }}
                >
                  <Copy size={20} /> 复制 BV 号
                </button>
                {inFeed && (
                  <button
                    role="menuitem"
                    className={item}
                    onClick={() => {
                      setExplain(true)
                      setOpen(false)
                    }}
                  >
                    <HelpCircle size={20} /> 为什么推荐给我
                  </button>
                )}
              </>
            ) : (
              <div className="px-2">
                <button role="menuitem" className={item} onClick={() => setKeywordMode(false)}><ArrowLeft size={20} />返回操作</button>
                <div className="px-2 pb-2 text-xs text-muted">选择要屏蔽的关键词</div>
                <div className="flex max-h-72 flex-wrap gap-2 overflow-y-auto px-2 pb-1">
                  {keywordOptions.map((k) => (
                    <button
                      key={k}
                      role="menuitem"
                      onClick={() => {
                        blockKeyword.mutate(k)
                        setOpen(false)
                      }}
                      className="flex min-h-11 items-center gap-1 rounded-lg bg-surface px-3 py-1.5 text-sm hover:bg-surface-hover"
                    >
                      <Tag size={14} /> {k}
                    </button>
                  ))}
                </div>
              </div>
            )}
          </div>,
          document.body,
        )}
      {explain && <ExplainDialog video={video} onClose={() => setExplain(false)} returnFocus={btn} />}
    </>
  )
}
