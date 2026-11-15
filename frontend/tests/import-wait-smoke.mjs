import assert from 'node:assert/strict'
import { fileURLToPath } from 'node:url'
import { writeFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { fixture, uuid } from './wp05-fixtures.mjs'

const browser = await fixture(fileURLToPath(new URL('..', import.meta.url)))
const { state, run, wait, click, calls } = browser
let allowResponse
const validation = new Promise(resolve => { allowResponse = resolve })
state.deepseekEnabled = true
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
  await wait("document.querySelector('#processing-model')?.value === 'qwen3.8-omni-flash'")
  assert.deepEqual(await run("Array.from(document.querySelector('#processing-model').options, option => option.value)"),
    ['qwen3.8-omni-flash', 'deepseek-v4.1-flash'])
  await run(`(() => {
    const model = document.querySelector('#processing-model')
    Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value').set.call(model, 'deepseek-v4.1-flash')
    model.dispatchEvent(new Event('change', { bubbles: true }))
    const selected = new DataTransfer()
    selected.items.add(new File(['synthetic'], ${JSON.stringify(name)}, { type: 'video/mp4' }))
    const picker = document.querySelector('#video-file')
    picker.files = selected.files
    picker.dispatchEvent(new Event('change', { bubbles: true }))
    document.querySelector('.import-section input[type=checkbox]').click()
  })()`)
  if (process.env.UI_TEST_EVIDENCE) {
    await run('document.querySelector("#processing-model").scrollIntoView({block:"center"})')
    const { data } = await browser.command('Page.captureScreenshot', { format: 'png' })
    writeFileSync(resolve(process.env.UI_TEST_EVIDENCE, 'deepseek-model-selected.png'), Buffer.from(data, 'base64'))
  }
  await click('导入并校验')
  await wait("document.body.textContent.includes('上传完成，正在校验并保存视频')")
  assert.equal(await run("Boolean(document.querySelector('.import-success'))"), false)
  assert.equal(await run("document.querySelector('#processing-model').disabled"), true)
  assert.equal(calls.find(call => call.path === '/api/inputs/upload').model, 'deepseek-v4.1-flash')
  allowResponse()
  await wait("document.querySelector('.import-success')?.textContent.includes('导入成功')")
  await wait("document.querySelector('[aria-labelledby=tasks-title]').textContent.includes('import-wait-synthetic.mp4')")
  assert.equal(calls.filter(call => call.path === '/api/inputs/upload').length, 1)
  console.log('Browser waited for validation, then displayed import success and the processing task')
} finally {
  allowResponse()
  await browser.close()
}
