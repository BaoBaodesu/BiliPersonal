import { useEffect, useRef, useState } from 'react'
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
    const esc = (e: KeyboardEvent) => e.key === 'Escape' && setOpen(false)
    const scroll = () => setOpen(false)
    document.addEventListener('mousedown', close)
    document.addEventListener('keydown', esc)
    window.addEventListener('scroll', scroll, { passive: true })
    return () => {
      document.removeEventListener('mousedown', close)
      document.removeEventListener('keydown', esc)
      window.removeEventListener('scroll', scroll)
    }
  }, [open])

  const toggle = (e: React.MouseEvent) => {
    e.preventDefault()
    e.stopPropagation()
    const r = btn.current!.getBoundingClientRect()
    const width = 264
    const left = Math.min(Math.max(8, r.right - width), window.innerWidth - width - 8)
    const top = r.bottom + 360 > window.innerHeight ? Math.max(8, r.top - 360) : r.bottom + 4
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
    'flex w-full items-center gap-4 px-4 py-2 text-left text-sm text-fg hover:bg-surface-hover focus-visible:bg-surface-hover outline-none'

  return (
    <>
      <button
        ref={btn}
        onClick={toggle}
        aria-label="更多操作"
        aria-haspopup="menu"
        aria-expanded={open}
        className={`-mr-2 flex h-9 w-9 shrink-0 items-center justify-center rounded-full text-fg transition hover:bg-surface-hover focus-visible:opacity-100 ${
          open ? 'opacity-100' : 'opacity-100 md:opacity-0 md:group-hover:opacity-100'
        }`}
      >
        <MoreVertical size={20} />
      </button>
      {open &&
        createPortal(
          <div
            ref={menu}
            role="menu"
            style={{ top: pos.top, left: pos.left }}
            className="fade-in fixed z-50 w-66 overflow-hidden rounded-xl bg-elevated py-2 shadow-pop"
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
                  onClick={() => {
                    navigator.clipboard?.writeText(video.bvid)
                    toast(`已复制 ${video.bvid}`)
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
                <div className="px-2 pb-2 text-xs text-muted">选择要屏蔽的关键词</div>
                <div className="flex max-h-72 flex-wrap gap-2 overflow-y-auto px-2 pb-1">
                  {keywordOptions.map((k) => (
                    <button
                      key={k}
                      onClick={() => {
                        blockKeyword.mutate(k)
                        setOpen(false)
                      }}
                      className="flex items-center gap-1 rounded-lg bg-surface px-3 py-1.5 text-sm hover:bg-surface-hover"
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
      {explain && <ExplainDialog video={video} onClose={() => setExplain(false)} />}
    </>
  )
}
