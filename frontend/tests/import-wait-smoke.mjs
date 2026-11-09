import assert from 'node:assert/strict'
import { fileURLToPath } from 'node:url'
import { fixture, uuid } from './wp05-fixtures.mjs'

const browser = await fixture(fileURLToPath(new URL('..', import.meta.url)))
const { state, run, wait, click, calls } = browser
let allowResponse
const validation = new Promise(resolve => { allowResponse = resolve })
const name = 'import-wait-synthetic.mp4'
const task = { ...state.tasks[1], id: uuid(77), filename: name }
state.hook = async (call, { json }) => {
  if (call.path !== '/api/inputs/upload') return false
  await validation
  state.tasks.unshift({ ...task, status: 'PROCESSING', started_at: new Date().toISOString() })
  json({ id: task.id, status: 'QUEUED', filename: name, duration_seconds: 5, has_audio: true }, 201)
  return true
}

try {
  await browser.navigate()
  await browser.login()
  await run(`(() => {
    const selected = new DataTransfer()
    selected.items.add(new File(['synthetic'], ${JSON.stringify(name)}, { type: 'video/mp4' }))
    const picker = document.querySelector('#video-file')
    picker.files = selected.files
    picker.dispatchEvent(new Event('change', { bubbles: true }))
    document.querySelector('.import-section input[type=checkbox]').click()
  })()`)
  await click('导入并校验')
  await wait("document.body.textContent.includes('上传完成，正在校验并保存视频')")
  assert.equal(await run("Boolean(document.querySelector('.import-success'))"), false)
  allowResponse()
  await wait("document.querySelector('.import-success')?.textContent.includes('导入成功')")
  await wait("document.querySelector('[aria-labelledby=tasks-title]').textContent.includes('import-wait-synthetic.mp4')")
  assert.equal(calls.filter(call => call.path === '/api/inputs/upload').length, 1)
  console.log('Browser waited for validation, then displayed import success and the processing task')
} finally {
  allowResponse()
  await browser.close()
}
