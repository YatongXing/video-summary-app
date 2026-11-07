// Actual React + actual WP06 backend/Worker/PG/RustFS. No API mocks.
import assert from 'node:assert/strict'
import {readFileSync,writeFileSync,mkdtempSync,existsSync,rmSync} from 'node:fs'
import {join,resolve,sep} from 'node:path'
import {tmpdir} from 'node:os'
import {spawn} from 'node:child_process'
const state=JSON.parse(readFileSync(process.env.WP06_STATE,'utf8'))
assert(state.url.startsWith('http://127.0.0.1:'))
const browser=[process.env.UI_TEST_BROWSER,join(process.env.PROGRAMFILES||'','Google/Chrome/Application/chrome.exe'),join(process.env['PROGRAMFILES(X86)']||'','Microsoft/Edge/Application/msedge.exe')].filter(Boolean).find(existsSync)
assert(browser)
const profile=mkdtempSync(join(tmpdir(),'video-summary-wp06-real-'))
const child=spawn(browser,['--headless=new','--disable-gpu','--no-first-run','--no-default-browser-check','--remote-debugging-port=0','--user-data-dir='+profile,'about:blank'],{windowsHide:true,stdio:'ignore'})
let socket,counter=0;const pending=new Map(),delay=ms=>new Promise(r=>setTimeout(r,ms))
try{
 const end=Date.now()+15000
 while(!existsSync(join(profile,'DevToolsActivePort'))){assert(Date.now()<end);await delay(100)}
 const port=readFileSync(join(profile,'DevToolsActivePort'),'utf8').split('\n')[0]
 const targets=await(await fetch('http://127.0.0.1:'+port+'/json/list')).json()
 socket=new WebSocket(targets.find(t=>t.type==='page').webSocketDebuggerUrl)
 await new Promise((r,j)=>{socket.onopen=r;socket.onerror=j})
 socket.onmessage=e=>{const m=JSON.parse(e.data);if(pending.has(m.id)){pending.get(m.id)(m);pending.delete(m.id)}}
 const cmd=(method,params={})=>new Promise((r,j)=>{const id=++counter,t=setTimeout(()=>j(Error('CDP timeout '+method)),20000);pending.set(id,m=>{clearTimeout(t);m.error?j(Error(JSON.stringify(m.error))):r(m.result)});socket.send(JSON.stringify({id,method,params}))})
 const run=async expression=>{const r=await cmd('Runtime.evaluate',{expression,awaitPromise:true,returnByValue:true});assert(!r.exceptionDetails,JSON.stringify(r.exceptionDetails));return r.result?.value}
 const wait=async(expression,ms=60000)=>{const end=Date.now()+ms;while(!await run('Boolean('+expression+')')){assert(Date.now()<end,'UI wait '+expression);await delay(200)}}
 const input=async(selector,v)=>run('(()=>{let e=document.querySelector('+JSON.stringify(selector)+');Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,"value").set.call(e,'+JSON.stringify(v)+');e.dispatchEvent(new Event("input",{bubbles:true}))})()')
 const click=async(text,scope='document')=>{const q='Array.from('+scope+'.querySelectorAll("button")).find(b=>b.textContent.trim()==='+JSON.stringify(text)+')';await wait(q+'&&!('+q+').disabled');await run('('+q+').click()')}
 await cmd('Page.enable');await cmd('Page.addScriptToEvaluateOnNewDocument',{source:"window.__copied=[];Object.defineProperty(navigator,'clipboard',{value:{writeText:async v=>window.__copied.push(v)},configurable:true});const originalOpen=XMLHttpRequest.prototype.open;XMLHttpRequest.prototype.open=function(method,url,...args){this.addEventListener('load',()=>{if(method==='POST'&&new URL(url,location.href).pathname==='/api/inputs/upload'&&this.status===201){try{const body=JSON.parse(this.responseText);window.__lastUploadReceipt={id:body.id,status:body.status}}catch{}}});return originalOpen.call(this,method,url,...args)}"});await cmd('Page.navigate',{url:state.url})
 async function personal(account){
  await wait('document.querySelector("#email")&&!document.querySelector(".checking")')
  await input('#email',account.email);await input('#password',account.password);await click('登录','document.querySelector("form")')
  await wait('document.querySelector("#video-file")')
  const {root}=await cmd('DOM.getDocument');const {nodeId}=await cmd('DOM.querySelector',{nodeId:root.nodeId,selector:'#video-file'})
  await run('window.__lastUploadReceipt=null');await cmd('DOM.setFileInputFiles',{nodeId,files:[state.samples.long]});await run('document.querySelector(".rights-confirmation input").click()');await click('导入并校验','document.querySelector(".import-section")')
  await wait('window.__lastUploadReceipt?.status==="QUEUED"&&document.querySelector(".import-success")')
  const taskId=await run('window.__lastUploadReceipt.id');assert.match(taskId,/^[a-f0-9-]{36}$/);assert(!await run('Boolean(document.querySelector(".import-success .task-identifier"))'))
  const card='Array.from(document.querySelectorAll("[aria-labelledby=tasks-title] .task-card")).find(e=>e.querySelector("code.identifier")?.textContent==='+JSON.stringify(taskId)+')'
  await wait(card)
  await click('复制任务 ID',card);assert.equal(await run('window.__copied.at(-1)'),taskId)
  await wait(card+'&&('+card+').textContent.includes("已完成")',90000);await click('查看结果',card)
  await wait('document.querySelector("[aria-labelledby=tasks-title] video")?.readyState>=2')
  await wait('document.querySelector(".result-panel .summary-text")')
  assert.equal(await run('('+card+').querySelectorAll(".task-identifier").length'),1);assert(!await run('Boolean(document.querySelector(".result-panel .task-identifier"))'))
  await run('(()=>{const v=document.querySelector("[aria-labelledby=tasks-title] video");v.muted=true;return v.play().then(()=>true)})()');await wait('document.querySelector("[aria-labelledby=tasks-title] video").currentTime>.1')
  const duration=await run('document.querySelector("[aria-labelledby=tasks-title] video").duration');assert(duration>3)
  await run('document.querySelector("[aria-labelledby=tasks-title] video").currentTime=5');await wait('!document.querySelector("[aria-labelledby=tasks-title] video").seeking&&document.querySelector("[aria-labelledby=tasks-title] video").readyState>=2&&document.querySelector("[aria-labelledby=tasks-title] video").currentTime>=2')
  await run('(()=>{const e=document.querySelector(".result-toolbar select");Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype,"value").set.call(e,"en");e.dispatchEvent(new Event("change",{bubbles:true}))})()');await wait('document.querySelector(".result-panel [lang=en]")')
  const metrics=await run('({duration:document.querySelector("[aria-labelledby=tasks-title] video").duration,currentTime:document.querySelector("[aria-labelledby=tasks-title] video").currentTime,readyState:document.querySelector("[aria-labelledby=tasks-title] video").readyState})')
  return {role:account.role,taskId,...metrics}
 }
 const metrics=[];metrics.push(await personal(state.accounts.find(a=>a.role==='USER')))
 await click('退出登录');metrics.push(await personal(state.accounts.find(a=>a.role==='ADMIN')))
 assert(!await run('/排障授权|待处理排障请求|删除记录|人工清理/.test(document.body.textContent)'))
 await click('任务元数据');await wait('document.querySelector("#admin-task")');assert(!await run('Boolean(document.querySelector("video,.result-panel,.import-section,[aria-labelledby=tasks-title]"))'))
 const target=metrics[0].taskId
 await input('#admin-task',target.toUpperCase());await click('搜索');await wait('document.querySelector(".admin-group")?.textContent.includes('+JSON.stringify(target)+')')
 assert(!await run('Boolean(document.querySelector(".admin-group video,.admin-group .result-panel"))'));assert(!await run('Array.from(document.querySelectorAll(".admin-group button")).some(b=>/摘要|视频|导出/.test(b.textContent))'))
 await input('#admin-task',target.slice(0,8).toUpperCase());await click('搜索');await wait('document.querySelector(".admin-group")?.textContent.includes('+JSON.stringify(target)+')')
 assert(await run("Array.from(document.querySelectorAll('a')).some(a=>a.getAttribute('href')==='mailto:feedback@example.invalid')"))
 const desktop=await cmd('Page.captureScreenshot',{format:'png',captureBeyondViewport:true});writeFileSync(resolve('docs/baseline/cr-002-desktop.png'),Buffer.from(desktop.data,'base64'))
 await cmd('Emulation.setDeviceMetricsOverride',{width:390,height:844,deviceScaleFactor:1,mobile:true});await delay(150)
 assert(await run('document.documentElement.scrollWidth<=innerWidth'))
 const mobile=await cmd('Page.captureScreenshot',{format:'png',captureBeyondViewport:true});writeFileSync(resolve('docs/baseline/cr-002-mobile.png'),Buffer.from(mobile.data,'base64'))
 const version=await cmd('Browser.getVersion');console.log(JSON.stringify({status:'passed',browser:version.product,checks:['USER real upload/Worker/bilingual result/MP4 decode/seek/ID copy','ADMIN real upload/Worker/bilingual result/MP4 decode/seek/ID copy','metadata no own or other content controls','exact and partial uppercase Task ID backend search','retired UI absent','configured footer','mobile no overflow'],metrics,limits:['Offline FakeModel','No provider API']}))
}finally{
 socket?.close();child.kill();await Promise.race([new Promise(r=>child.once('exit',r)),delay(3000)])
 assert(resolve(profile).startsWith(resolve(tmpdir())+sep)&&profile.split(sep).at(-1).startsWith('video-summary-wp06-real-'))
 rmSync(profile,{recursive:true,force:true,maxRetries:10,retryDelay:200})
 console.log(JSON.stringify({resources:{browserExited:child.exitCode!==null||child.signalCode!==null,profileRemoved:!existsSync(profile)}}))
}
