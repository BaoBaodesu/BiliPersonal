import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { CheckCircle2, RefreshCw, Smartphone } from 'lucide-react'
import { api } from '../api/client'
import { keys } from '../hooks/queries'
import { Logo } from '../components/Logo'

export function LoginPage() {
  const navigate = useNavigate()
  const qc = useQueryClient()
  const qr = useQuery({ queryKey: ['qrcode'], queryFn: api.auth.qrcode, staleTime: Infinity, gcTime: 0 })
  const [status, setStatus] = useState<'waiting' | 'scanned' | 'expired' | 'success' | 'error'>('waiting')

  useEffect(() => {
    document.title = '登录 - BiliFeed'
  }, [])

  useEffect(() => {
    if (!qr.data || status === 'expired' || status === 'success') return
    const timer = setInterval(async () => {
      try {
        const r = await api.auth.qrcodeStatus(qr.data.qrcode_key)
        setStatus(r.status)
        if (r.status === 'success') {
          clearInterval(timer)
          await qc.invalidateQueries({ queryKey: keys.auth })
          navigate('/', { replace: true })
        }
      } catch {
        /* 网络抖动时继续轮询 */
      }
    }, 1500)
    return () => clearInterval(timer)
  }, [qr.data, status, navigate, qc])

  const renew = () => {
    setStatus('waiting')
    qr.refetch()
  }

  const hint = {
    waiting: '打开哔哩哔哩 App 扫码登录',
    scanned: '已扫描，请在手机上确认',
    expired: '二维码已失效',
    success: '登录成功，正在准备推荐…',
    error: '登录出现问题，请刷新二维码',
  }[status]

  return (
    <div className="flex min-h-full flex-col items-center justify-center bg-bg px-4 py-10">
      <div className="mb-8">
        <Logo />
      </div>
      <div className="w-full max-w-sm rounded-2xl border border-line p-8 text-center">
        <h1 className="text-xl font-medium">扫码登录</h1>
        <p className="mt-2 text-sm text-muted">Cookie 只保存在本机后端，不会发送给浏览器。</p>
        <div className="relative mx-auto mt-6 flex h-52 w-52 items-center justify-center overflow-hidden rounded-xl bg-white p-2">
          {qr.data ? (
            <img src={qr.data.image} alt="登录二维码" className="h-full w-full" />
          ) : qr.isError ? (
            <span className="text-sm text-neutral-500">二维码获取失败</span>
          ) : (
            <div className="skeleton h-full w-full rounded-lg" />
          )}
          {(status === 'expired' || status === 'scanned' || status === 'success' || qr.isError) && (
            <div className="absolute inset-0 flex flex-col items-center justify-center gap-3 bg-white/90 text-neutral-900">
              {status === 'scanned' && <Smartphone size={36} />}
              {status === 'success' && <CheckCircle2 size={36} className="text-green-600" />}
              {(status === 'expired' || qr.isError) && (
                <button
                  onClick={renew}
                  className="flex items-center gap-2 rounded-full bg-neutral-900 px-4 py-2 text-sm text-white"
                >
                  <RefreshCw size={16} /> 刷新二维码
                </button>
              )}
            </div>
          )}
        </div>
        <p aria-live="polite" className="mt-5 text-sm">
          {hint}
        </p>
      </div>
      <p className="mt-6 max-w-sm text-center text-xs text-subtle">
        登录后将在后台读取观看历史与收藏并训练推荐模型，期间首页先按热度展示。
      </p>
    </div>
  )
}
