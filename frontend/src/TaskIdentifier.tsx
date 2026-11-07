import { useEffect, useRef, useState } from 'react'

export const validTaskId = (value: string) => /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value)

export default function TaskIdentifier({ taskId }: { taskId: string }) {
  const [message, setMessage] = useState('')
  const generation = useRef(0)
  useEffect(() => { ++generation.current; setMessage(''); return () => { ++generation.current } }, [taskId])
  if (!validTaskId(taskId)) return null
  async function copy() {
    const current = generation.current
    try {
      await navigator.clipboard.writeText(taskId)
      if (current === generation.current) setMessage('已复制任务 ID')
    } catch {
      if (current === generation.current) setMessage('无法自动复制，请选中上方完整任务 ID 手动复制。')
    }
  }
  return <div className="task-identifier"><span>Task ID：<code className="identifier">{taskId}</code></span><button type="button" className="secondary" onClick={() => void copy()} aria-label={`复制任务 ID ${taskId}`}>复制任务 ID</button>{message && <span className="field-help" role="status">{message}</span>}</div>
}
