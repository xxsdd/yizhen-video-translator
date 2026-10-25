'use strict';
const LOCAL = 'http://127.0.0.1:8765';
const running = new Map();

async function importVideo(payload, sourceTab) {
  // No generating request is ever made here. The server queues local ASR only.
  const health = await fetch(LOCAL+'/api/health', {signal:AbortSignal.timeout(3000)}).catch(()=>null);
  if (!health?.ok || (await health.json()).app !== 'yizhen-local') throw new Error('译帧还没启动。请双击桌面“译帧”，启动后回到视频网页再点扩展。');
  const saved = await chrome.storage.session.get('recentImports');
  const recent = saved.recentImports || {};
  const identity = `${sourceTab}:${payload.url}`;
  const previous = recent[identity];
  const requestId = previous && Date.now()-previous.time<600000 ? previous.requestId : crypto.randomUUID();
  recent[identity] = {requestId,time:Date.now()};
  for (const key of Object.keys(recent)) if (Date.now()-recent[key].time>600000) delete recent[key];
  await chrome.storage.session.set({recentImports:recent});
  const tabs = await chrome.tabs.query({url:LOCAL+'/*'});
  let tab = tabs[0] || await chrome.tabs.create({url:LOCAL+'/',active:false});
  const deadline = Date.now()+15000;
  while (tab.status!=='complete') {
    if(Date.now()>deadline) throw new Error('译帧页面尚未就绪，请稍后重试。');
    await new Promise(resolve=>setTimeout(resolve,200));
    tab = await chrome.tabs.get(tab.id);
  }
  const results = await chrome.scripting.executeScript({target:{tabId:tab.id},
    func:async body=>{
      if (location.origin!=='http://127.0.0.1:8765') throw new Error('本地页面已切换，请重试。');
      const response = await fetch('/api/browser/import',{method:'POST', headers:{'Content-Type':'application/json','X-Local-App':'1'},body:JSON.stringify(body)});
      const result = await response.json();
      if(!response.ok) throw new Error(typeof result.detail==='string'?result.detail:'导入内容无效。');
      return result;
    }, args:[{...payload,request_id:requestId}]});
  const result = results[0]?.result;
  if (!result?.job?.id) throw new Error('没有收到导入结果，请回到译帧检查任务列表。');
  return {id:result.job.id,reused:result.reused,tabId:tab.id};
}

chrome.runtime.onMessage.addListener((message,sender,sendResponse)=>{
  if (sender.id!==chrome.runtime.id || message.type!=='import') return;
  const entries=message.entries;
  if(!Array.isArray(entries)||entries.length<1||entries.length>10||entries.some(payload=>!payload || !/^https?:\/\//.test(payload.url) || !/^https?:\/\//.test(payload.page_url))) {
    sendResponse({error:'当前页面不是可导入的视频网页。'});return;
  }
  const key=String(message.tabId)+':'+entries.map(p=>p.url).join('|');
  if(!running.has(key)) running.set(key,(async()=>{
    let result;
    for(const payload of entries) result=await importVideo(payload,message.tabId);
    await chrome.tabs.update(result.tabId,{url:LOCAL+'/?job='+encodeURIComponent(result.id),active:true});
    return result;
  })().finally(()=>running.delete(key)));
  running.get(key).then(result=>sendResponse({ok:true,...result}),error=>sendResponse({error:error.message||'导入失败，请重试。'}));
  return true;
});
