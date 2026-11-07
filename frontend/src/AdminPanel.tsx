import { useEffect, useState } from 'react'
import TaskIdentifier from './TaskIdentifier'

type Row = {
  id: string; user_id: string; status: string; submitted_at: string; started_at: string | null; finished_at: string | null
  failure_category: string | null; failure_code: string | null; retry_count: number; credits_available: number | null; quota_unlimited: boolean
  queue_seconds?: number | null; processing_seconds?: number | null
  retry_records?: unknown[]; quota_events?: unknown[]; model_calls?: unknown[]
}
const labels: Record<string, string> = { QUEUED: '排队中', PROCESSING: '处理中', SUCCEEDED: '已完成', FAILED: '失败', CANCELLED: '已取消' }
const time = (value: string | null) => value ? new Date(value).toLocaleString() : '尚未记录'
const seconds = (value: number | null | undefined) => typeof value === 'number' ? `${Math.round(value)} 秒` : '尚未记录'
function records(rows: unknown[] | undefined, keys: string[]) {
  return (rows || []).filter((row): row is Record<string, unknown> => !!row && typeof row === 'object').map(row => Object.fromEntries(keys.filter(key => key in row && (row[key] === null || ['string', 'number', 'boolean'].includes(typeof row[key]))).map(key => [key, row[key]])))
}
export default function AdminPanel({ onExpired }: { onExpired: () => void }) {
  const [input, setInput] = useState(''), [taskId, setTaskId] = useState('')
  const [offset, setOffset] = useState(0), [rows, setRows] = useState<Row[]>([])
  const [error, setError] = useState(''), [loading, setLoading] = useState(false), [refresh, setRefresh] = useState(0)
  useEffect(() => {
    const controller = new AbortController(); setLoading(true); setError(''); setRows([])
    void (async () => {
      try {
        const response = await fetch(`/api/admin/tasks?limit=20&offset=${offset}${taskId ? `&task_id=${encodeURIComponent(taskId)}` : ''}`, { credentials: 'same-origin', cache: 'no-store', signal: controller.signal })
        if (controller.signal.aborted) return
        if (response.status === 401) { onExpired(); return }
        if (!response.ok) {
          setError(response.status === 429 ? '查询过于频繁，请稍后再试。' : response.status === 422 ? '查询参数无效，请刷新后重试。' : '无法读取管理员任务记录，请确认权限后重试。')
          return
        }
        const value: unknown = await response.json()
        if (controller.signal.aborted) return
        if (!Array.isArray(value) || value.some(row => !row || typeof row.id !== 'string' || typeof row.user_id !== 'string')) throw new Error('Invalid response')
        setRows(value)
      } catch { if (!controller.signal.aborted) setError('无法读取管理员任务记录，请稍后重试。') }
      finally { if (!controller.signal.aborted) setLoading(false) }
    })()
    return () => controller.abort()
  }, [taskId, offset, refresh, onExpired])
  useEffect(() => { const interval = window.setInterval(() => setRefresh(n => n + 1), 15000); return () => window.clearInterval(interval) }, [])
  function search() {
    const value = input.trim()
    if (!value) return
    setError(''); setTaskId(value); setOffset(0); setRefresh(n => n + 1)
  }
  const groups: Record<string, Row[]> = {}
  for (const row of rows) (groups[row.user_id] ??= []).push(row)
  return <section className="task-section" aria-labelledby="admin-title">
    <div className="section-heading"><span className="eyebrow">管理工作台</span><h2 id="admin-title">任务元数据</h2></div>
    <p className="field-help">仅显示任务状态、时间、错误、重试与统计。查看本人内容请使用“我的视频任务”。</p>
    <form className="admin-filter" onSubmit={event => { event.preventDefault(); search() }}><label htmlFor="admin-task">Task ID（留空查看全部）</label><input id="admin-task" value={input} onChange={event => {
      const value = event.target.value; setInput(value)
      if (!value.trim()) { setTaskId(''); setOffset(0); setError(''); setRefresh(n => n + 1) }
    }} autoComplete="off" spellCheck={false} aria-describedby="admin-task-help" /><button className="secondary" type="submit">搜索</button><button className="secondary" type="button" onClick={() => setRefresh(n => n + 1)}>刷新</button></form>
    <p id="admin-task-help" className="field-help">完整编号精确定位；其他输入按 Task ID 字面包含匹配，不区分大小写。清空即恢复全部可见任务。</p>
    {error && <p className="message error" role="alert">{error}</p>}
    {loading && <p role="status">正在读取管理记录…</p>}
    {!loading && !error && !rows.length && <p>当前页没有匹配的任务记录。</p>}
    {Object.entries(groups).map(([id, group]) => <section className="admin-group" key={id}><h3>用户 {id}</h3><ul className="task-list">{group.map(row => <li className="task-card" key={row.id}><strong>{labels[row.status] || row.status}</strong><TaskIdentifier taskId={row.id} /><dl className="metadata"><dt>提交</dt><dd>{time(row.submitted_at)}</dd><dt>开始</dt><dd>{time(row.started_at)}</dd><dt>结束</dt><dd>{time(row.finished_at)}</dd><dt>排队时长</dt><dd>{seconds(row.queue_seconds)}</dd><dt>实际处理时长</dt><dd>{seconds(row.processing_seconds)}</dd><dt>错误分类</dt><dd>{row.failure_category || '无'}</dd><dt>错误代码</dt><dd>{row.failure_code || '无'}</dd><dt>重试次数</dt><dd>{row.retry_count}</dd><dt>账户剩余机会</dt><dd>{row.quota_unlimited ? '不限' : row.credits_available}</dd></dl>
      <details><summary>重试与额度记录</summary><pre>{JSON.stringify({ retry_records: records(row.retry_records, ['source', 'accepted_at', 'cooldown_triggered']), quota_events: records(row.quota_events, ['event_type', 'delta', 'created_at']), model_calls: records(row.model_calls, ['status', 'model_version', 'adapter_version', 'prompt_version', 'input_tokens', 'output_tokens', 'cached_tokens', 'estimated_cost', 'currency', 'error_category', 'created_at', 'finished_at']) }, null, 2)}</pre></details>
    </li>)}</ul></section>)}
    <div className="task-pagination"><button type="button" className="secondary" disabled={offset === 0 || loading} onClick={() => setOffset(n => Math.max(0, n - 20))}>上一页</button><span>第 {offset / 20 + 1} 页</span><button type="button" className="secondary" disabled={rows.length < 20 || loading || offset >= 10000} onClick={() => setOffset(n => n + 20)}>下一页</button></div>
  </section>
}
