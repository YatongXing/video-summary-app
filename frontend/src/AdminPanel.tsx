import { useEffect, useState } from 'react'
import DeletionPanel from './DeletionPanel'
import ResultPanel from './ResultPanel'
type Row = {
  id: string; user_id: string; status: string; submitted_at: string; started_at: string | null; finished_at: string | null
  failure_category: string | null; failure_code: string | null; retry_count: number; credits_available: number | null; quota_unlimited: boolean
  queue_seconds?: number | null; processing_seconds?: number | null
  retry_records?: unknown[]; quota_events?: unknown[]; model_calls?: unknown[]
}
const labels: Record<string, string> = { QUEUED: '排队中', PROCESSING: '处理中', SUCCEEDED: '已完成', FAILED: '失败', CANCELLED: '已取消' }
const time = (value: string | null) => value ? new Date(value).toLocaleString() : '尚未记录'
function seconds(value: number | null | undefined) { return typeof value === 'number' ? `${Math.round(value)} 秒` : '尚未记录' }
export default function AdminPanel({ onExpired, csrfToken }: { onExpired: () => void; csrfToken: string }) {
  const [accessPurpose, setAccessPurpose] = useState('')
  const [opened, setOpened] = useState<{ id: string; purpose: string; scope: 'summary' | 'video' } | null>(null)
  const [filter, setFilter] = useState(''), [userId, setUserId] = useState('')
  const [offset, setOffset] = useState(0), [rows, setRows] = useState<Row[]>([])
  const [error, setError] = useState(''), [loading, setLoading] = useState(false), [refresh, setRefresh] = useState(0)
  useEffect(() => {
    const controller = new AbortController(); setLoading(true); setError(''); setRows([])
    void (async () => {
      try {
        const response = await fetch(`/api/admin/tasks?limit=20&offset=${offset}${userId ? `&user_id=${encodeURIComponent(userId)}` : ''}`, { credentials: 'same-origin', cache: 'no-store', signal: controller.signal })
        if (response.status === 401) { onExpired(); return }
        if (!response.ok) throw new Error('Unavailable')
        const value = await response.json() as Row[]
        if (!Array.isArray(value) || value.some(row => typeof row.id !== 'string' || typeof row.user_id !== 'string')) throw new Error('Invalid response')
        if (!controller.signal.aborted) setRows(value)
      } catch { if (!controller.signal.aborted) setError('无法读取管理员任务记录，请确认权限及筛选条件。') }
      finally { if (!controller.signal.aborted) setLoading(false) }
    })()
    return () => controller.abort()
  }, [userId, offset, refresh, onExpired])
  useEffect(() => { const interval = window.setInterval(() => setRefresh(n => n + 1), 15000); return () => window.clearInterval(interval) }, [])
  const groups: Record<string, Row[]> = {}
  for (const row of rows) (groups[row.user_id] ??= []).push(row)
  return <section className="task-section" aria-labelledby="admin-title">
    <div className="section-heading"><span className="eyebrow">管理工作台</span><h2 id="admin-title">任务元数据</h2></div>
    <p className="field-help">按用户查看任务状态、时间、错误、重试与额度记录。默认不展示视频或摘要正文。排障访问须填写与用户本任务授权一致的目的，并手动打开指定内容。</p>
    <form className="admin-filter" onSubmit={event => { event.preventDefault(); setUserId(filter.trim()); setOffset(0); setRefresh(n => n + 1) }}><label htmlFor="admin-user">用户 ID（留空查看全部）</label><input id="admin-user" value={filter} onChange={event => setFilter(event.target.value)} maxLength={36} pattern="[a-fA-F0-9-]{36}" /><button className="secondary" type="submit">筛选</button><button className="secondary" type="button" onClick={() => setRefresh(n => n + 1)}>刷新</button></form>
    <label>本次排障访问目的<input maxLength={200} value={accessPurpose} onChange={event => { setAccessPurpose(event.target.value); setOpened(null) }} /></label>
    {error && <p className="message error" role="alert">{error}</p>}
    {loading && <p role="status">正在读取管理记录…</p>}
    {!loading && !error && !rows.length && <p>当前页没有任务记录。</p>}
    {Object.entries(groups).map(([id, group]) => <section className="admin-group" key={id}><h3>用户 {id}</h3><ul className="task-list">{group.map(row => <li className="task-card" key={row.id}><strong>{labels[row.status] || row.status}</strong><p className="identifier">任务 {row.id}</p><dl className="metadata"><dt>提交</dt><dd>{time(row.submitted_at)}</dd><dt>开始</dt><dd>{time(row.started_at)}</dd><dt>结束</dt><dd>{time(row.finished_at)}</dd><dt>排队时长</dt><dd>{seconds(row.queue_seconds)}</dd><dt>实际处理时长</dt><dd>{seconds(row.processing_seconds)}</dd><dt>错误分类</dt><dd>{row.failure_category || '无'}</dd><dt>错误代码</dt><dd>{row.failure_code || '无'}</dd><dt>重试次数</dt><dd>{row.retry_count}</dd><dt>账户剩余机会</dt><dd>{row.quota_unlimited ? '不限' : row.credits_available}</dd></dl>
      <div className="task-actions"><button className="secondary" type="button" disabled={!accessPurpose.trim()} onClick={() => setOpened({ id: row.id, purpose: accessPurpose.trim(), scope: 'summary' })}>请求查看授权摘要</button><button className="secondary" type="button" disabled={!accessPurpose.trim()} onClick={() => setOpened({ id: row.id, purpose: accessPurpose.trim(), scope: 'video' })}>请求查看授权视频</button>{opened?.id === row.id && <button className="secondary" type="button" onClick={() => setOpened(null)}>关闭内容</button>}</div>
      {opened?.id === row.id && opened.scope === 'summary' && <ResultPanel key={`${row.id}-${opened.purpose}`} taskId={row.id} adminPurpose={opened.purpose} onExpired={onExpired} />}
      {opened?.id === row.id && opened.scope === 'video' && <video controls preload="metadata" src={`/api/admin/tasks/${row.id}/video?purpose=${encodeURIComponent(opened.purpose)}`} onError={() => setError('无法访问视频：请核实任务级授权、访问目的和当前会话。')} aria-label="获授权的任务视频" />}
      <details><summary>重试与额度记录</summary><pre>{JSON.stringify({ retry_records: row.retry_records || [], quota_events: row.quota_events || [], model_calls: row.model_calls || [] }, null, 2)}</pre></details>
    </li>)}</ul></section>)}
    <DeletionPanel admin csrfToken={csrfToken} onExpired={onExpired} />
    <div className="task-pagination"><button type="button" className="secondary" disabled={offset === 0 || loading} onClick={() => setOffset(n => Math.max(0, n - 20))}>上一页</button><span>第 {offset / 20 + 1} 页</span><button type="button" className="secondary" disabled={rows.length < 20 || loading} onClick={() => setOffset(n => n + 20)}>下一页</button></div>
  </section>
}
