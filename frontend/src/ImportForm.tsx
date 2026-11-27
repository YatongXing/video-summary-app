import { useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { validTaskId } from './TaskIdentifier'

type Imported = { id: string; status: 'QUEUED'; filename: string; duration_seconds: number; has_audio: boolean; has_subtitles?: boolean; evidence?: 'PLATFORM_SUBTITLES' | 'AUDIO_ONLY' | 'FULL_MEDIA' }
export const QWEN_MODEL = 'qwen3.8-omni-flash'
export const DEEPSEEK_MODEL = 'deepseek-v4.1-flash'
type Model = typeof QWEN_MODEL | typeof DEEPSEEK_MODEL
type Source = 'local' | 'bilibili'
type TransferResult = { status: number; body: unknown }

function imported(value: unknown): value is Imported {
  if (!value || typeof value !== 'object') return false
  const v = value as Partial<Imported>
  return typeof v.id === 'string' && validTaskId(v.id) && v.status === 'QUEUED' && typeof v.filename === 'string'
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
    BILIBILI_URL_INVALID: '请输入有效的 Bilibili BV 播放页和分 P。',
    BILIBILI_PART_MISMATCH: '无法确认链接指向的分 P，请核对链接。',
    BILIBILI_RESTRICTED: '此视频无法匿名完整获取。可以上传你有权处理的本地文件。',
    BILIBILI_TLS: '连接 Bilibili 时发生 TLS 错误，导入已停止。',
    BILIBILI_TIMEOUT: 'Bilibili 下载或校验超时，导入已停止。',
    BILIBILI_NO_MEDIA: '未获得可校验的完整音视频。',
    BILIBILI_TOO_LARGE: 'Bilibili 素材超过导入大小上限。',
    BILIBILI_NO_SPACE: '当前暂存空间不足，请稍后重试。',
    BILIBILI_DISABLED: 'Bilibili 导入已关闭。',
    STORAGE_UNAVAILABLE: '私有存储暂不可用，请稍后重试。',
    QUEUE_BUSY: '当前导入请求较多，请稍后重试。',
    IMPORT_BUSY: '当前导入请求较多，请稍后重试。',
    IMPORT_CANCELLED: '本次导入已中断。',
    INVALID_FILENAME: '文件名无效，请使用简短的普通文件名后重试。',
    UNSUPPORTED_MEDIA: '视频容器或编码不符合要求，请检查媒体格式。',
    INVALID_MEDIA: '无法完整读取此视频，请检查文件是否损坏。',
    RIGHTS_REQUIRED: '请先确认拥有导入和处理该视频的授权。',
    INVALID_MODEL: '不支持所选处理模型。',
    MODEL_DISABLED: '所选处理模型暂未启用。请刷新后重新选择。',
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

export default function ImportForm({ csrfToken, deepseekAvailable, bilibiliAvailable, onExpired, onImported }: {
  csrfToken: string; deepseekAvailable: boolean; bilibiliAvailable: boolean; onExpired: () => void; onImported?: () => void
}) {
  const [source, setSource] = useState<Source>('local')
  const [model, setModel] = useState<Model>(QWEN_MODEL)
  const [file, setFile] = useState<File | null>(null)
  const [url, setUrl] = useState('')
  const [rights, setRights] = useState(false)
  const [combineVisual, setCombineVisual] = useState(false)
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
    setCombineVisual(false)
    setRights(false)
    setError('')
    setMessage('')
  }

  function upload(selected: File, selectedModel: Model): Promise<TransferResult> {
    return new Promise((resolve, reject) => {
      const request = new XMLHttpRequest()
      active.current = request
      request.open('POST', '/api/inputs/upload')
      request.withCredentials = true
      request.setRequestHeader('Content-Type', 'application/octet-stream')
      request.setRequestHeader('X-Video-Filename', encodeURIComponent(selected.name))
      request.setRequestHeader('X-Upload-Authorized', 'true')
      if (selectedModel !== QWEN_MODEL) request.setRequestHeader('X-Video-Model', selectedModel)
      request.setRequestHeader('X-CSRF-Token', csrfToken)
      request.upload.onprogress = (event) => {
        if (mounted.current && event.lengthComputable && event.total > 0) {
          setProgress(Math.min(100, Math.floor(event.loaded / event.total * 100)))
        }
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
    if (source === 'bilibili') {
      if (!bilibiliAvailable) { setError('Bilibili 导入已关闭。'); return }
      try {
        const parsed = new URL(url.trim())
        if (parsed.protocol !== 'https:' || !['bilibili.com', 'www.bilibili.com'].includes(parsed.host) || parsed.username || parsed.password) throw new Error('Invalid URL')
      } catch { setError('请输入HTTPS Bilibili BV 播放页链接。'); return }
    }
    const selectedModel = deepseekAvailable ? model : QWEN_MODEL
    running.current = true
    setBusy(true)
    setProgress(source === 'local' ? 0 : null)
    try {
      let response: TransferResult
      if (source === 'local' && file) {
        response = await upload(file, selectedModel)
      } else {
        const controller = new AbortController()
        active.current = controller
        const fetched = await fetch('/api/inputs/bilibili', {
          method: 'POST', credentials: 'same-origin', signal: controller.signal,
          headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrfToken },
          body: JSON.stringify({ url: url.trim(), rights_confirmed: true, ...(combineVisual && { combine_visual: true }), ...(selectedModel !== QWEN_MODEL && { model: selectedModel }) }),
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
        setError('连接中断，尚未收到导入确认。请先刷新“我的视频任务”，确认没有这条任务后再提交。')
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
      {result && <div className="import-success" role="status"><strong>导入成功</strong><p>{result.filename}</p><span>时长 {Math.ceil(result.duration_seconds)} 秒。视频已创建处理任务，请在“我的视频任务”中查看进度和结果。</span>{!result.has_audio && !result.has_subtitles && result.evidence !== 'PLATFORM_SUBTITLES' && <p>此视频没有音轨，后续将进行视觉理解；没有可用语音。</p>}</div>}
      <div className="mode-switch" aria-label="视频来源">
        <button type="button" aria-pressed={source === 'local'} onClick={() => switchSource('local')} disabled={busy}>本地文件</button>
        {bilibiliAvailable && <button type="button" aria-pressed={source === 'bilibili'} onClick={() => switchSource('bilibili')} disabled={busy}>Bilibili 链接</button>}
      </div>
      <form onSubmit={(event) => void submit(event)} aria-busy={busy}>
        {deepseekAvailable && <div className="field">
          <label htmlFor="processing-model">处理模型</label>
          <select id="processing-model" value={model} disabled={busy} onChange={event => setModel(event.target.value === DEEPSEEK_MODEL ? DEEPSEEK_MODEL : QWEN_MODEL)}>
            <option value={QWEN_MODEL}>Qwen3.8-Omni-Flash（默认）</option>
            <option value={DEEPSEEK_MODEL}>DeepSeek-V4.1-Flash</option>
          </select>
          {model === DEEPSEEK_MODEL && <span className="field-help">音轨用于语音转写，转写文字和关键画面用于生成摘要。</span>}
        </div>}
        {source === 'local' ? <div className="field"><label htmlFor="video-file">选择视频</label><input ref={input} id="video-file" name="video" type="file" accept=".mp4,.mov" required disabled={busy} onChange={(event) => { setFile(event.target.files?.[0] ?? null); setRights(false) }} aria-describedby="file-help" /><span className="field-help" id="file-help">MP4、MOV；视频 H.264 / AVC，音轨如存在须为 AAC 或 MP3。可用组合以实际校验为准。</span></div> : <div className="field"><label htmlFor="video-url">Bilibili 播放页链接</label><input id="video-url" name="video-url" type="url" placeholder="https://www.bilibili.com/video/BVxxxxxxxxxx/" required maxLength={4096} autoComplete="off" spellCheck={false} value={url} onChange={(event) => { setUrl(event.target.value); setRights(false) }} disabled={busy} aria-describedby="url-help" /><span className="field-help" id="url-help">仅导入链接所指的一 P，未指定时使用 P1。默认优先使用平台字幕，缺少可用字幕时取独立音轨。独立音轨上限 64 MiB。勾选「结合画面」后，媒体上限 256 MiB，导入可能需要几分钟。</span></div>}
        {source === 'bilibili' && <label className="rights-confirmation"><input type="checkbox" id="combine-visual" checked={combineVisual} disabled={busy} onChange={event => setCombineVisual(event.target.checked)} />结合画面</label>}
        <label className="rights-confirmation"><input type="checkbox" checked={rights} onChange={(event) => setRights(event.target.checked)} disabled={busy} required /><span>我确认拥有上传、导入及处理此视频的合法授权。</span></label>
        {busy && <div className="transfer-status" role="status"><span>{source === 'local' ? progress === 100 ? '上传完成，正在校验并保存视频…' : '正在上传视频…' : '正在导入并校验视频…'}</span>{source === 'local' && progress !== null && progress < 100 ? <><progress value={progress} max={100} aria-label="文件上传进度" /><span>{progress}%</span></> : <progress aria-label="等待导入和校验完成" />}<span className="field-help">任务创建后可在“我的视频任务”中查看处理进度。</span></div>}
        <div className="import-actions"><button type="submit" className="primary" disabled={busy}>{busy ? '正在导入…' : '导入并校验'}</button>{busy && <button type="button" className="secondary" onClick={() => active.current?.abort()}>停止本次请求</button>}</div>
      </form>
    </section>
  )
}
