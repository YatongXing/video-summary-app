// CR002 built React + actual browser; strict synthetic API.
import assert from 'node:assert/strict'
import {dirname,resolve} from 'node:path'
import {fileURLToPath} from 'node:url'
import {fixture,tid,uuid,delay} from './wp05-fixtures.mjs'
const root=resolve(dirname(fileURLToPath(import.meta.url)),'..'),f=await fixture(root),{state,calls,run,wait,click,input}=f,passed=[]
const owned="document.querySelector('[aria-labelledby=tasks-title]')",mark=s=>passed.push(s)
try{
 await f.navigate();assert.equal(await run('Boolean(document.querySelector(".feedback-footer"))'),false);mark('unconfigured footer hidden before login')
 await f.login()
 assert.deepEqual([...new Set(await run('Array.from('+owned+'.querySelectorAll(".task-state")).map(e=>e.textContent)'))].sort(),['排队中','处理中','已完成','失败','已取消'].sort())
 const ids=await run('Array.from('+owned+'.querySelectorAll(".task-identifier code")).map(e=>e.textContent)');assert.deepEqual(ids,state.tasks.map(t=>t.id))
 for(const task of state.tasks){await run('document.querySelector('+JSON.stringify('[aria-label="复制任务 ID '+task.id+'"]')+').click()');await delay(25);assert.equal(await run('window.__clipboard.at(-1)'),task.id)};mark('five states show authoritative IDs; clipboard contains only ID')
 assert(!await run('/排障授权|待处理排障请求|删除记录|人工清理/.test(document.body.textContent)'));mark('retired UI absent')
 await click('查看结果',owned);await wait("document.body.textContent.includes('合成中文摘要')");assert.equal(await run('document.querySelector(".result-panel").closest(".task-card").querySelectorAll(".task-identifier").length'),1);assert(!await run('Boolean(document.querySelector(".result-panel .task-identifier"))'))
 await input('.result-toolbar select','en');await wait("document.body.textContent.includes('Synthetic English summary')");await click('导出 JSON');await wait('window.__downloads===1');mark('owner bilingual JSON and result ID')
 await run("window.__nativeFetch=window.fetch;window.fetch=async(...a)=>{const r=await window.__nativeFetch(...a);if(String(a[0]).includes('/export')){const read=r.blob.bind(r);r.blob=async()=>{const v=await read();window.__waiting=true;await new Promise(resolve=>window.__release=resolve);return v}}return r}")
 await click('导出 JSON');await wait('window.__waiting');await click('退出登录');await wait('document.querySelector("#email")');await run('window.__release()');await delay(100);assert.equal(await run('window.__downloads'),1);assert(!await run("document.body.textContent.includes('Synthetic English summary')"));mark('late download after logout cannot escape cleanup')
 state.tasks.push(...Array.from({length:25},(_,i)=>({...state.tasks[1],id:uuid(100+i)})));await f.login('admin@example.invalid');assert(!await run('Boolean(document.querySelector("#admin-task"))'));await click('任务元数据');await wait('document.querySelector("#admin-task")');assert(!await run('Boolean(document.querySelector("video,.result-panel,.import-section,[aria-labelledby=tasks-title]"))'))
 assert(!await run('Boolean(document.querySelector(".admin-group video,.admin-group .result-panel"))'));assert(!await run('Array.from(document.querySelectorAll(".admin-group button")).some(b=>/摘要|视频|导出/.test(b.textContent))'));mark('metadata has no own/other content controls')
 await input('#admin-task',tid.toUpperCase());await click('搜索');await delay(150);assert(calls.some(c=>c.path==='/api/admin/tasks'&&new URLSearchParams(c.query).get('task_id')===tid.toUpperCase()));mark('search reaches backend including exact uppercase ID')
 for(const query of ['1','g','中文','%','_',"'",'a'.repeat(120)]){
  const before=calls.length;await input('#admin-task',query);await click('搜索');await delay(100)
  assert(calls.slice(before).some(c=>c.path==='/api/admin/tasks'&&new URLSearchParams(c.query).get('task_id')===query));assert(!await run('Boolean(document.querySelector("[role=alert]"))'))
 }
 mark('arbitrary short/unicode/literal metacharacter/long search reaches backend')
 await input('#admin-task','');await wait('document.querySelectorAll(".admin-group .task-card").length===20');assert.equal(new URLSearchParams(calls.filter(c=>c.path==='/api/admin/tasks').at(-1).query).get('offset'),'0')
 await click('下一页','document.querySelector("[aria-labelledby=admin-title]")');await wait('document.querySelector("[aria-labelledby=admin-title] .task-pagination").textContent.includes("第 2 页")')
 await input('#admin-task','0');await click('搜索');await wait('document.querySelectorAll(".admin-group .task-card").length===20');await click('下一页','document.querySelector("[aria-labelledby=admin-title]")');await wait('document.querySelector("[aria-labelledby=admin-title] .task-pagination").textContent.includes("第 2 页")');await input('#admin-task','');await wait('document.querySelector("[aria-labelledby=admin-title] .task-pagination").textContent.includes("第 1 页")&&document.querySelectorAll(".admin-group .task-card").length===20')
 assert.equal(new URLSearchParams(calls.filter(c=>c.path==='/api/admin/tasks').at(-1).query).get('offset'),'0');assert(!new URLSearchParams(calls.filter(c=>c.path==='/api/admin/tasks').at(-1).query).has('task_id'));mark('clearing input automatically restores default list and page one')
 await run("window.__metaFetch=window.fetch;window.fetch=async(...a)=>{const r=await window.__metaFetch(...a);if(String(a[0]).includes('task_id=late')){const read=r.json.bind(r);r.json=async()=>{const v=await read();window.__metaWaiting=true;await new Promise(resolve=>window.__metaRelease=resolve);return v}}return r}")
 await input('#admin-task','late');await click('搜索');await wait('window.__metaWaiting');await input('#admin-task','');await wait('document.querySelectorAll(".admin-group .task-card").length===20');await run('window.__metaRelease()');await delay(100);assert.equal(await run('document.querySelectorAll(".admin-group .task-card").length'),20);await run('window.fetch=window.__metaFetch');mark('late search response cannot overwrite cleared default list')
 await click('我的任务');await wait(owned);await click('查看结果',owned);await wait("document.body.textContent.includes('合成中文摘要')");assert.equal(await run('document.querySelector(".result-panel").closest(".task-card").querySelectorAll(".task-identifier").length'),1);assert(!await run('Boolean(document.querySelector(".result-panel .task-identifier"))'));mark('ADMIN own task result has only one card ID/copy group')
 await click('退出登录');await wait('document.querySelector("#email")');state.supportEmail='feedback@example.invalid';await f.navigate();await wait("Array.from(document.querySelectorAll('a')).some(a=>a.getAttribute('href')==='mailto:feedback@example.invalid')");mark('public runtime feedback config before login')
 await f.command('Emulation.setDeviceMetricsOverride',{width:390,height:844,deviceScaleFactor:1,mobile:true});assert(await run('document.documentElement.scrollWidth<=innerWidth'));mark('mobile footer no horizontal overflow')
 assert(!calls.some(c=>/support-request|consents|purpose/.test(c.path)));mark('no retired API traffic')
 console.log(JSON.stringify({status:'passed',checks:passed,browser:f.browser,limits:['Mock API','No backend or model claim']}))
}finally{await f.close();console.log(JSON.stringify({resources:f.resources}))}
