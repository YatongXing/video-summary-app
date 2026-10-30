import { useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'

type Imported = { id: string; status: 'QUEUED'; filename: string; duration_seconds: number; has_audio: boolean }
type Source = 'local' | 'url'
type TransferResult = { status: number; body: unknown }

function imported(value: unknown): value is Imported {
  if (!value || typeof value !== 'object') return false
  const v = value as Partial<Imported>
  return typeof v.id === 'string' && v.status === 'QUEUED' && typeof v.filename === 'string'
    && typeof v.duration_seconds === 'number' && Number.isFinite(v.duration_seconds)
    && v.duration_seconds > 0 && typeof v.has_audio === 'boolean'
}

function importError(status: number, body: unknown): string {
  const code = body && typeof body === 'object' && 'code' in body ? String(body.code) : ''
  const messages: Record<string, string> = {
    FILE_TOO_LARGE: '视频大小不能超过 2 GB。',
    TOO_LARGE: '视频大小不能超过 2 GB。',
    VIDEO_TOO_LONG: '视频时长不能超过 2 小时。',
    MEDIA_TIMEOUT: '视频校验耗时过长，请检查视频后重试。',
    MEDIA_SERVICE_UNAVAILABLE: '媒体校验服务暂不可用，请联系管理员检查本地服务。',
    DOWNLOAD_FAILED: '无法下载此视频，请检查链接是否可直接访问，再稍后重试。',
    STORAGE_UNAVAILABLE: '私有存储暂不可用，请稍后重试。',
    QUEUE_BUSY: '当前导入请求较多，请稍后重试。',
    IMPORT_BUSY: '当前导入请求较多，请稍后重试。',
    IMPORT_CANCELLED: '本次导入已中断。',
    INVALID_FILENAME: '文件名无效，请使用简短的普通文件名后重试。',
    UNSUPPORTED_MEDIA: '视频容器或编码不符合要求，请检查媒体格式。',
    INVALID_MEDIA: '无法完整读取此视频，请检查文件是否损坏。',
    UNSAFE_URL: '此链接不符合安全要求，请使用可直接访问的 HTTPS 视频文件链接。',
    RIGHTS_REQUIRED: '请先确认拥有导入和处理该视频的授权。',
  }
  if (messages[code]) return messages[code]
  if (status === 401) return '登录已失效，请重新登录后导入。'
  if (status === 403) return '暂时无法授权本次导入，请刷新登录状态后重试。'
  if (status === 413) return '视频大小不能超过 2 GB。'
  if (status === 415) return '视频容器或编码不符合要求，请检查媒体格式。'
  if (status === 400 || status === 422) return '无法导入此视频。请检查授权、链接、文件格式、时长及完整性。'
  if (status === 429) return '当前导入请求较多，请稍后再试。'
  if (status === 408 || status === 504) return '导入或校验耗时过长，请检查网络和视频后重试。'
  return '暂时无法完成导入，请稍后重试。'
}

function parseBody(text: string): unknown {
  try { return JSON.parse(text) as unknown } catch { return null }
}

export default function ImportForm({ csrfToken, onExpired, onImported }: { csrfToken: string; onExpired: () => void; onImported?: () => void }) {
  const [source, setSource] = useState<Source>('local')
  const [file, setFile] = useState<File | null>(null)
  const [url, setUrl] = useState('')
  const [rights, setRights] = useState(false)
  const [busy, setBusy] = useState(false)
  const [progress, setProgress] = useState<number | null>(null)
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')
  const [result, setResult] = useState<Imported | null>(null)
  const input = useRef<HTMLInputElement>(null)
  const active = useRef<{ abort: () => void } | null>(null)
  const running = useRef(false)
  const mounted = useRef(true)

  useEffect(() => {
    mounted.current = true
    return () => { mounted.current = false; active.current?.abort() }
  }, [])

  function switchSource(next: Source) {
    if (running.current) return
    setSource(next)
    setRights(false)
    setError('')
    setMessage('')
  }

  function upload(selected: File): Promise<TransferResult> {
    return new Promise((resolve, reject) => {
      const request = new XMLHttpRequest()
      active.current = request
      request.open('POST', '/api/inputs/upload')
      request.withCredentials = true
      request.setRequestHeader('Content-Type', 'application/octet-stream')
      request.setRequestHeader('X-Video-Filename', encodeURIComponent(selected.name))
      request.setRequestHeader('X-Upload-Authorized', 'true')
      request.setRequestHeader('X-CSRF-Token', csrfToken)
      request.upload.onprogress = (event) => {
        if (mounted.current && event.lengthComputable) setProgress(Math.min(100, Math.round(event.loaded / event.total * 100)))
      }
      request.onload = () => resolve({ status: request.status, body: parseBody(request.responseText) })
      request.onerror = () => reject(new Error('Network unavailable'))
      request.onabort = () => reject(new DOMException('Request stopped', 'AbortError'))
      request.send(selected)
    })
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (running.current) return
    setError('')
    setMessage('')
    setResult(null)
    if (!rights) { setError('请先确认拥有导入和处理该视频的授权。'); return }
    if (source === 'local' && (!file || file.size === 0 || file.size > 2_000_000_000)) {
      setError(file && file.size > 2_000_000_000 ? '视频大小不能超过 2 GB。' : '请选择非空视频文件。')
      return
    }
    if (source === 'url') {
      try {
        const parsed = new URL(url.trim())
        if (parsed.protocol !== 'https:' || parsed.username || parsed.password) throw new Error('Invalid URL')
      } catch { setError('请输入不含账号密码的 HTTPS 直达视频文件链接。'); return }
    }
    running.current = true
    setBusy(true)
    setProgress(source === 'local' ? 0 : null)
    try {
      let response: TransferResult
      if (source === 'local' && file) {
        response = await upload(file)
      } else {
        const controller = new AbortController()
        active.current = controller
        const fetched = await fetch('/api/inputs/url', {
          method: 'POST', credentials: 'same-origin', signal: controller.signal,
          headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrfToken },
          body: JSON.stringify({ url: url.trim(), rights_confirmed: true }),
        })
        response = { status: fetched.status, body: await fetched.json().catch(() => null) }
      }
      if (!mounted.current) return
      if (response.status === 401) { onExpired(); return }
      if (response.status !== 201) { setError(importError(response.status, response.body)); return }
      if (!imported(response.body)) { setError('服务器响应无法确认，请联系管理员核对后再提交。'); return }
      setResult(response.body)
      onImported?.()
      setFile(null)
      setUrl('')
      setRights(false)
      if (input.current) input.current.value = ''
    } catch (caught) {
      if (!mounted.current) return
      if (caught instanceof DOMException && caught.name === 'AbortError') {
        setMessage('已停止本次请求。若服务器已经完成校验，可能已创建排队任务；这不等同于取消已创建的任务。')
      } else {
        setError('连接中断，尚未确认导入结果。请联系管理员核对后再提交，避免重复导入。')
      }
    } finally {
      running.current = false
      active.current = null
      if (mounted.current) { setBusy(false); setProgress(null) }
    }
  }

  return (
    <section className="import-section" aria-labelledby="import-title">
      <div className="section-heading"><span className="eyebrow">导入视频</span><h2 id="import-title">从一个视频开始</h2></div>
      <p className="import-description">文件不超过 2 GB、2 小时。实际容器、编码和完整性由服务器校验，导入与排队不扣处理机会。</p>
      {error && <p className="message error" role="alert">{error}</p>}
      {message && <p className="message notice" role="status">{message}</p>}
      {result && <div className="import-success" role="status"><strong>校验通过 · Queued</strong><p>{result.filename}</p><span>时长 {Math.ceil(result.duration_seconds)} 秒。视频已进入队列，处理与总结功能将在后续工作包接通。</span>{!result.has_audio && <p>此视频没有音轨，后续将进行视觉理解；没有可用语音。</p>}</div>}
      <div className="mode-switch" aria-label="视频来源">
        <button type="button" aria-pressed={source === 'local'} onClick={() => switchSource('local')} disabled={busy}>本地文件</button>
        <button type="button" aria-pressed={source === 'url'} onClick={() => switchSource('url')} disabled={busy}>HTTPS 链接</button>
      </div>
      <form onSubmit={(event) => void submit(event)} aria-busy={busy}>
        {source === 'local' ? <div className="field"><label htmlFor="video-file">选择视频</label><input ref={input} id="video-file" name="video" type="file" accept=".mp4,.mov" required disabled={busy} onChange={(event) => { setFile(event.target.files?.[0] ?? null); setRights(false) }} aria-describedby="file-help" /><span className="field-help" id="file-help">MP4、MOV；视频 H.264 / AVC，音轨如存在须为 AAC 或 MP3。可用组合以实际校验为准。</span></div> : <div className="field"><label htmlFor="video-url">HTTPS 直达文件链接</label><input id="video-url" name="video-url" type="url" placeholder="https://example.com/video.mp4" required maxLength={8192} autoComplete="off" spellCheck={false} value={url} onChange={(event) => { setUrl(event.target.value); setRights(false) }} disabled={busy} aria-describedby="url-help" /><span className="field-help" id="url-help">仅支持合法直达视频文件，不支持平台播放页、需要登录或绕过访问限制的链接。服务器会先导入私有存储。</span></div>}
        <label className="rights-confirmation"><input type="checkbox" checked={rights} onChange={(event) => setRights(event.target.checked)} disabled={busy} required /><span>我确认拥有上传、导入及处理此视频的合法授权。</span></label>
        {busy && <div className="transfer-status" role="status"><span>{source === 'local' ? progress === 100 ? '上传完成，正在校验视频…' : '正在上传视频…' : '正在导入并校验视频…'}</span>{source === 'local' && progress !== null ? <><progress value={progress} max={100} aria-label="文件上传进度" /><span>{progress}%</span></> : <progress aria-label="等待导入和校验完成" />}<span className="field-help">这是输入导入与校验，不表示视频已完成总结。</span></div>}
        <div className="import-actions"><button type="submit" className="primary" disabled={busy}>{busy ? '正在导入…' : '导入并校验'}</button>{busy && <button type="button" className="secondary" onClick={() => active.current?.abort()}>停止本次请求</button>}</div>
      </form>
    </section>
  )
}
