import {useEffect,useRef,useState} from 'react'
type Result={language:'zh'|'en';offline_test:boolean;export:{summary:string;key_events:{timestamp_seconds:number;title:string;description:string}[]};presentation:{overview:string;points:string[];chapters:{timestamp_seconds:number;title:string}[];limitations:string[]}}
const stamp=(n:number)=>`${Math.floor(n/60)}:${String(Math.floor(n%60)).padStart(2,'0')}`
export default function ResultPanel({taskId,onExpired}:{taskId:string;onExpired:()=>void}){
 const [language,setLanguage]=useState<'zh'|'en'>('zh'),[result,setResult]=useState<Result|null>(null),[error,setError]=useState(''),[loading,setLoading]=useState(false),[downloading,setDownloading]=useState(false),[failed,setFailed]=useState(false)
 const video=useRef<HTMLVideoElement>(null),generation=useRef(0),downloads=useRef(new Set<AbortController>()),urls=useRef(new Set<string>())
 const base=`/api/tasks/${taskId}`
 useEffect(()=>{
  const n=++generation.current,c=new AbortController();const valid=()=>n===generation.current&&!c.signal.aborted
  setResult(null);setError('');setFailed(false);setLoading(true)
  void(async()=>{try{
   const r=await fetch(`${base}/result?language=${language}`,{credentials:'same-origin',cache:'no-store',signal:c.signal})
   if(!valid())return
   if(r.status===401){onExpired();return}
   if(!r.ok){throw Error()}
   const v=await r.json();if(!valid())return
   if(v.language!==language||typeof v.export?.summary!=='string'||typeof v.presentation?.overview!=='string'||!Array.isArray(v.export.key_events)||!Array.isArray(v.presentation.points)||!Array.isArray(v.presentation.chapters)||!Array.isArray(v.presentation.limitations))throw Error()
   setResult(v)
  }catch{if(valid()){setResult(null);setError('暂时无法读取有效结果，请重新确认访问权限后打开。')}}finally{if(valid())setLoading(false)}})()
  return()=>{++generation.current;c.abort();for(const d of downloads.current)d.abort();downloads.current.clear();for(const u of urls.current)URL.revokeObjectURL(u);urls.current.clear();setDownloading(false)}
 },[base,language,onExpired])
 useEffect(()=>{const element=video.current;return()=>{if(element){element.pause();element.removeAttribute('src');element.load()}}},[base,failed])
 async function download(){
  if(!result||downloading)return
  const n=generation.current,c=new AbortController();downloads.current.add(c);setDownloading(true)
  const valid=()=>n===generation.current&&!c.signal.aborted
  try{
   const r=await fetch(`${base}/export?language=${language}`,{credentials:'same-origin',cache:'no-store',signal:c.signal})
   if(!valid())return
   if(r.status===401){onExpired();return}
   if(!r.ok){setResult(null);throw Error()}
   const blob=await r.blob();if(!valid())return
   const url=URL.createObjectURL(blob);urls.current.add(url)
   const a=document.createElement('a');a.href=url;a.download=`video-summary-${language}.json`;a.click()
   setTimeout(()=>{URL.revokeObjectURL(url);urls.current.delete(url)},1000)
  }catch{if(valid())setError('无法下载有效JSON，请刷新状态后再试。')}
  finally{downloads.current.delete(c);if(valid())setDownloading(false)}
 }
 const showVideo=!failed
 return <section className="result-panel" aria-label="视频总结结果">
 <div className="result-toolbar"><label>输出语言 <select value={language} onChange={e=>setLanguage(e.target.value as 'zh'|'en')}><option value="zh">中文</option><option value="en">English</option></select></label><button type="button" className="secondary" disabled={!result||loading||downloading} onClick={()=>void download()}>导出 JSON</button></div>
 {error&&<p className="message error" role="alert">{error}</p>}{loading&&<p role="status">正在读取结果…</p>}
 {showVideo&&<video ref={video} controls preload="metadata" src={base+'/video'} aria-label="原始视频播放器" onError={()=>{setFailed(true);setError('视频暂不可播放，请刷新访问状态后重试。');}}/>}
 {result&&<div lang={language==='zh'?'zh-CN':'en'}>{result.offline_test&&<p className="message notice">离线替身演示结果，不代表真实视频总结或模型质量。</p>}<h3>总览</h3><p>{result.presentation.overview}</p><h3>要点</h3><ul>{result.presentation.points.map((p,i)=><li key={i}>{p}</li>)}</ul><h3>摘要</h3><p className="summary-text">{result.export.summary}</p><h3>章节</h3><ol className="timeline">{result.presentation.chapters.map((c,i)=><li key={i}><button className="timestamp" disabled={!showVideo} onClick={()=>{if(video.current)video.current.currentTime=c.timestamp_seconds}}>{stamp(c.timestamp_seconds)}</button> {c.title}</li>)}</ol><h3>关键时刻</h3>{result.export.key_events.length?<ol className="timeline">{result.export.key_events.map((e,i)=><li key={i}><button className="timestamp" disabled={!showVideo} onClick={()=>{if(video.current)video.current.currentTime=e.timestamp_seconds}}>{stamp(e.timestamp_seconds)}</button> <strong>{e.title}</strong><p>{e.description}</p></li>)}</ol>:<p>未识别到可独立列出的关键事件。</p>}<h3>音频与视觉局限</h3><ul>{result.presentation.limitations.map((p,i)=><li key={i}>{p}</li>)}</ul></div>}
 </section>
}
