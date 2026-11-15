export default function FeedbackFooter({ configured }: { configured: unknown }) {
  if (!(typeof configured === 'string' && configured.length <= 254 && /^[a-zA-Z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-zA-Z0-9](?:[a-zA-Z0-9.-]*[a-zA-Z0-9])?\.[a-zA-Z]{2,}$/.test(configured))) return null
  return <footer className="feedback-footer" aria-label="问题反馈">遇到问题？请将任务 ID 和问题描述发送至：<a href={`mailto:${configured}`}>{configured}</a>。</footer>
}
