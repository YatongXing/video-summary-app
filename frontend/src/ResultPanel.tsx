import { useEffect, useRef, useState } from 'react'
type Event = { timestamp_seconds: number; title: string; description: string }
type Result = {
  language: 'zh' | 'en'; offline_test: boolean
  export: { summary: string; key_events: Event[] }
  presentation: { overview: string; points: string[]; chapters: { timestamp_seconds: number; title: string }[]; limitations: string[] }
}
function stamp(n: number) { return `${Math.floor(n / 60)}:${String(Math.floor(n % 60)).padStart(2, '0')}` }
export default function ResultPanel({ taskId, onExpired, adminPurpose }: { taskId: string; onExpired: () => void; adminPurpose?: string }) {
  const [language, setLanguage] = useState<'zh' | 'en'>('zh')
  const [result, setResult] = useState<Result | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const [downloading, setDownloading] = useState(false)
  const base = adminPurpose === undefined ? `/api/tasks/${taskId}` : `/api/admin/tasks/${taskId}`
  const purposeQuery = adminPurpose === undefined ? '' : `&purpose=${encodeURIComponent(adminPurpose)}`
  const video = useRef<HTMLVideoElement>(null)
  useEffect(() => {
    const controller = new AbortController()
    setLoading(true); setResult(null); setError('')
    void (async () => {
      try {
        const response = await fetch(`${base}/result?language=${language}${purposeQuery}`, { credentials: 'same-origin', cache: 'no-store', signal: controller.signal })
        if (response.status === 401) { onExpired(); return }
        if (!response.ok) throw new Error('Unavailable')
        const value = await response.json() as Result
        if (controller.signal.aborted) return
        if (!value.export || !value.presentation || value.language !== language || !Array.isArray(value.export.key_events) || !Array.isArray(value.presentation.points) || !Array.isArray(value.presentation.chapters) || !Array.isArray(value.presentation.limitations)) throw new Error('Invalid response')
        setResult(value)
      } catch { if (!controller.signal.aborted) setError('暂时无法读取有效结果，请稍后重新打开。') }
      finally { if (!controller.signal.aborted) setLoading(false) }
    })()
    return () => controller.abort()
  }, [taskId, language, onExpired, base, purposeQuery])
  async function download() {
    if (!result || downloading) return
    setDownloading(true); setError('')
    try {
      const response = await fetch(`${base}/export?language=${language}${purposeQuery}`, { credentials: 'same-origin', cache: 'no-store' })
      if (response.status === 401) { onExpired(); return }
      if (!response.ok) throw new Error('Unavailable')
      const blob = await response.blob(), url = URL.createObjectURL(blob)
      const a = document.createElement('a'); a.href = url; a.download = `video-summary-${language}.json`
      a.click(); window.setTimeout(() => URL.revokeObjectURL(url), 1000)
    } catch { setError('无法下载有效 JSON，请刷新结果后再试。') }
    finally { setDownloading(false) }
  }
  function jump(seconds: number) { if (video.current) { video.current.currentTime = seconds; video.current.focus() } }
  return <section className="result-panel" aria-label="视频总结结果">
    <div className="result-toolbar"><label>输出语言 <select value={language} onChange={event => setLanguage(event.target.value as 'zh' | 'en')}><option value="zh">中文</option><option value="en">English</option></select></label><button className="secondary" type="button" disabled={!result || loading || downloading} onClick={() => void download()}>{downloading ? '正在下载…' : '导出 JSON'}</button></div>
    {error && <p className="message error" role="alert">{error}</p>}
    {loading && <p role="status">正在读取结果…</p>}
    {result && <div lang={language === 'zh' ? 'zh-CN' : 'en'}>
      {result.offline_test && <p className="message notice">离线替身演示结果，仅用于验证应用流程，不代表真实视频总结或模型质量。</p>}
      {adminPurpose === undefined && <video ref={video} controls preload="metadata" src={`/api/tasks/${taskId}/video`} onError={() => setError('视频暂不可播放；可稍后重试，摘要仍可查看。')} aria-label="原始视频播放器" />}
      <h3>总览</h3><p>{result.presentation.overview}</p>
      <h3>要点</h3><ul>{result.presentation.points.map((point, i) => <li key={i}>{point}</li>)}</ul>
      <h3>摘要</h3><p className="summary-text">{result.export.summary}</p>
      <h3>章节</h3><ol className="timeline">{result.presentation.chapters.map((chapter, i) => <li key={i}><button className="timestamp" type="button" disabled={adminPurpose !== undefined} onClick={() => jump(chapter.timestamp_seconds)}>{stamp(chapter.timestamp_seconds)}</button> {chapter.title}</li>)}</ol>
      <h3>关键时刻</h3>{result.export.key_events.length ? <ol className="timeline">{result.export.key_events.map((event, i) => <li key={i}><button className="timestamp" type="button" disabled={adminPurpose !== undefined} onClick={() => jump(event.timestamp_seconds)}>{stamp(event.timestamp_seconds)}</button> <strong>{event.title}</strong><p>{event.description}</p></li>)}</ol> : <p>未识别到可独立列出的关键事件。</p>}
      <h3>音频与视觉局限</h3><ul>{result.presentation.limitations.map((item, i) => <li key={i}>{item}</li>)}</ul>
    </div>}
  </section>
}

