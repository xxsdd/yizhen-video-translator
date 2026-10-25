'use strict';
const $ = id => document.getElementById(id);
const escapeHTML = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const state = {jobs:[], settings:null, languages:{}, selected:null, filter:'all', segments:[], mode:localStorage.getItem('subtitle-mode') || 'translated', follow:true, tab:'links', files:[], drafts:[], profile:'deepseek', models:{}, fingerprint:'', videoId:null, toastTimer:null, polling:false};
const labels = {awaiting_download:'待下载',awaiting_processing:'待处理',deleting:'正在删除',queued:'等待处理', processing:'处理中', completed:'已完成', paused:'已暂停', failed:'需要处理', needs_config:'等待 AI 配置', needs_translation:'等待手动翻译', uploading:'导入中', cancelled:'已取消'};
const clock = seconds => {const s=Math.floor(seconds||0); return s>=3600?`${Math.floor(s/3600)}:${String(Math.floor(s%3600/60)).padStart(2,'0')}:${String(s%60).padStart(2,'0')}`:`${String(Math.floor(s/60)).padStart(2,'0')}:${String(s%60).padStart(2,'0')}`};
const size = bytes => bytes>1024**3?`${(bytes/1024**3).toFixed(1)} GB`:`${(bytes/1024**2).toFixed(1)} MB`;
const posterURL = job => `/api/jobs/${job.id}/poster?v=${encodeURIComponent(job.cover_revision||'initial')}`;
function toast(message){$('toast').textContent=message;$('toast').hidden=false;clearTimeout(state.toastTimer);state.toastTimer=setTimeout(()=>$('toast').hidden=true,5500)}
async function api(path, data, method='POST'){
  const options = {method,headers:{'X-Local-App':'1'}};
  if(data instanceof FormData) options.body=data;
  else if(data !== undefined){options.headers['Content-Type']='application/json';options.body=JSON.stringify(data)}
  const response=await fetch('/api'+path,options);
  let body;try{body=await response.json()}catch{throw new Error('服务暂时没有响应，请确认本地程序仍在运行。')}
  if(!response.ok) throw new Error(typeof body.detail==='string'?body.detail:'输入内容不完整，请检查后重试。');
  return body;
}
function current(){return state.jobs.find(j=>j.id===state.selected)}
function setProcessDetails(expanded){
  $('process-details').hidden=!expanded;
  const toggle=$('process-details-toggle');
  toggle.setAttribute('aria-expanded',String(expanded));
  toggle.textContent=expanded?'收起详情':'展开详情';
}
function syncProcessDetails(job){
  const toggle=$('process-details-toggle'), jobId=job?.id||'';
  toggle.hidden=!job;
  // Polling keeps the user's choice; a newly selected video starts collapsed.
  if(toggle.dataset.job!==jobId){
    toggle.dataset.job=jobId;
    setProcessDetails(false);
  }
}
$('process-details-toggle').addEventListener('click',()=>setProcessDetails($('process-details').hidden));
function renderTranslationActivity(){
  const j=current(), box=$('translation-progress');
  if(!box)return;
  box.hidden=!j||(!j.translation_activity&&!j.translation_usage);
  if(box.hidden)return;
  const a=j.translation_activity, u=j.translation_usage;
  const active=j.status==='processing'&&a&&!a.ended;
  const stages={waiting:'等待服务商响应',thinking:'模型正在思考',receiving:'正在接收译文',retrying:'服务繁忙，正在重试',saved:'本次响应已保存',paused:'已暂停',failed:'请求未完成'};
  const elapsed=a?Math.max(0,(active?Date.now()/1000:a.ended||a.started)-a.started):0;
  $('translation-live').textContent=a?`${stages[active?a.stage:j.status==='paused'?'paused':a.stage]||'正在处理'} · 本次请求 ${clock(elapsed)}`:'';
  $('translation-live').classList.toggle('is-waiting',!!active);
  $('translation-meter').max=Math.max(1,j.segment_count||0);
  $('translation-meter').value=j.translated_count||0;
  $('translation-saved').textContent=`已保存 ${j.translated_count||0} / ${j.segment_count||0} 段字幕`;
  const silent=active?(Date.now()/1000-(a.last_response||a.started)):0;
  $('translation-wait').textContent=active?(silent>60?`已 ${Math.floor(silent)} 秒未收到新内容，仍在等待；可点击“暂停”。`:'没有总时长限制；完整字幕会逐步保存，可随时暂停。'):'';
  const fmt=n=>Number(n||0).toLocaleString();
  $('translation-usage').textContent=!u?'':u.reported_requests?`此视频累计已报告 token：输入 ${fmt(u.prompt_tokens)} · 输出 ${fmt(u.completion_tokens)}${u.reasoning_reported?`（其中思考 ${fmt(u.reasoning_tokens)}）`:''}。${u.requests>u.reported_requests?`另有 ${u.requests-u.reported_requests} 次请求未报告用量。`:''}`:'服务商尚未返回 token 用量；未报告不代表未消耗。';
}
setInterval(renderTranslationActivity,1000);
function renderLibrary(){
  $('count-all').textContent=state.jobs.length;
  $('count-active').textContent=state.jobs.filter(j=>['queued','processing','uploading'].includes(j.status)).length;
  $('count-completed').textContent=state.jobs.filter(j=>j.status==='completed').length;
  $('library-total').textContent=`${state.jobs.length} 部`;
  const visible=state.jobs.filter(j=>state.filter==='all'||(state.filter==='active'?['queued','processing','uploading'].includes(j.status):j.status==='completed'));
  $('library').innerHTML=visible.length?visible.map(j=>`<div class="library-line"><input type="checkbox" data-check-job="${j.id}" aria-label="选择 ${escapeHTML(j.title)}" ${state.checked?.has(j.id)?'checked':''}><button class="library-item ${state.selected===j.id?'active':''}" data-job="${j.id}" title="${escapeHTML(j.title)}"><span class="library-thumb">${j.has_poster?`<img alt="" src="${posterURL(j)}">`:'▷'}</span><span class="library-info"><strong>${escapeHTML(j.title)}</strong><small>${escapeHTML(typeof libraryStatusLabel==='function'?libraryStatusLabel(j):labels[j.status]||j.status)}${j.duration?' · '+clock(j.duration):''}</small>${typeof libraryProgressMarkup==='function'?libraryProgressMarkup(j):''}</span></button>${typeof libraryActions==='function'?libraryActions(j):''}</div>`).join(''):`<p class="library-empty">${state.filter==='all'?'导入后，视频会保存在这里。':'这里暂时没有视频。'}</p>`;
  if(typeof renderSelection==='function')renderSelection();
}

function syncPlayer(){
  const j=current(),video=$('video');
  if(!j){resetPlayer();return;}
  $('video-title').textContent=j.title;
  $('video-meta').textContent=j.duration?clock(j.duration):'';
  $('breadcrumb').textContent='我的视频';
  $('process-title').textContent=labels[j.status]||j.status;
  $('process-note').textContent=j.message;
  syncProcessDetails(j);
  renderLocalProgress(j);
  renderTranslationActivity();
  $('progress-bar').style.width=`${j.progress||0}%`;
  document.querySelectorAll('.process-steps span').forEach((el,i)=>el.classList.toggle('done',(j.progress||0)>=[0,27,70,100][i]));
  $('job-error').hidden=!j.error;$('job-error').textContent=j.error?`${j.error}${j.translation_activity?.diagnostic?.request_id?' 请求编号：'+j.translation_activity.diagnostic.request_id:''}`:'';
  const active=['processing','queued'].includes(j.status);
  const restart=['paused','failed','awaiting_download','awaiting_processing'].includes(j.status);
  $('job-action').hidden=!(active||restart);
  $('job-action').textContent=active?(!j.playable&&(j.progress||0)<19?'暂停下载':'暂停'):'继续本地处理';
  if(typeof taskAction==='function'){const a=taskAction(j);$('job-action').hidden=!a.action;$('job-action').textContent=a.label;}
  if(typeof syncTranslationControls==='function')syncTranslationControls(j,active);
  $('clear-media').hidden=!j.playable||active;
  $('delete-current').hidden=false;
  if(typeof renderStudioJob==='function')renderStudioJob(j);
  if(j.playable){
    video.hidden=false;$('screen-empty').hidden=true;
    const poster=j.has_poster?posterURL(j):'';if(video.getAttribute('poster')!==poster)video.setAttribute('poster',poster);
    if(state.videoId!==j.id){
      video.pause();video.src=`/api/jobs/${j.id}/video`;video.poster=j.has_poster?posterURL(j):'';
      video.onloadedmetadata=()=>{if(j.position&&j.position<video.duration-1)video.currentTime=j.position;video.playbackRate=Number($('speed').value)};
      state.videoId=j.id;
    }
  }else{
    video.hidden=true;$('screen-empty').hidden=false;
    if(state.videoId){video.pause();video.removeAttribute('src');video.load();state.videoId=null}
    $('screen-title').textContent=j.media_cleared?'媒体缓存已清理':j.status==='failed'?'视频暂未就绪':'视频正在准备中';
    if(j.status==='awaiting_download')$('screen-title').textContent='等待你开始下载';
    if(j.status==='awaiting_processing')$('screen-title').textContent='视频已导入，等待处理';
    if(j.status==='paused')$('screen-title').textContent='任务已暂停';
    $('screen-description').textContent=j.media_cleared?'字幕仍保存在下方，可随时导出':j.status==='failed'?'查看下方原因，或导入本地文件继续':j.message;
  }
  const preview=$('download-poster');
  if(preview){preview.hidden=j.playable||!j.has_poster||j.media_cleared;const key=j.id+':'+(j.cover_revision||'');if(!preview.hidden&&preview.dataset.job!==key){preview.src=posterURL(j);preview.dataset.job=key;}}
}
function renderTranscript(){
  const term=$('search').value.trim().toLocaleLowerCase();
  const filtered=state.segments.filter(s=>!term||(s.text+' '+s.translation).toLocaleLowerCase().includes(term));
  $('transcript-count').textContent=state.segments.length?`${state.segments.filter(s=>s.translation||s.translation_complete).length} / ${state.segments.length} 句已翻译`:'逐句对应视频时间';
  $('export-button').disabled=!state.segments.length;
  const top=$('transcript').scrollTop;
  $('transcript').innerHTML=filtered.length?filtered.map(s=>{
    let content;
    if(state.mode==='original'||state.mode==='off')content=wordMarkup(s.text,s,current()?.source_language);
    else content=(s.translation?wordMarkup(s.translation,s,current()?.target):s.translation_complete?'':'<span class="pending-text">等待翻译…</span>')+(state.mode==='bilingual'?`<small>${wordMarkup(s.text,s,current()?.source_language)}</small>`:'');
    return `<div class="subtitle-row" data-segment="${s.id}" tabindex="0" title="右键编辑这句字幕"><button class="subtitle-time" data-start="${s.start}" aria-label="跳到 ${clock(s.start)}"><time>${clock(s.start)}</time></button><span class="subtitle-copy">${content}</span><button class="subtitle-edit-button" data-edit-subtitle="${s.id}" aria-label="编辑 ${clock(s.start)} 的字幕" title="编辑字幕">✎</button></div>`;
  }).join(''):`<div class="transcript-empty"><span>≋</span><p>${term?'没有匹配的字幕':'字幕会随着处理进度出现在这里'}</p><small>${term?'试试另一个关键词。':'译文模式适合沉浸观看，双语模式适合学习。'}</small></div>`;
  $('transcript').scrollTop=top;
  updateActive(false);
}
let track;
function updateCues(){
  const video=$('video');
  if(!track)track=video.addTextTrack('subtitles','译帧字幕','zh');
  track.mode='hidden';
  if(track.cues)Array.from(track.cues).forEach(c=>track.removeCue(c));
  for(const s of state.segments){
    let text=state.mode==='original'?s.text:state.mode==='bilingual'?`${s.translation?s.translation+'\n':''}${s.text}`:s.translation;
    if(!text||s.end<=s.start)continue;
    text=text.replace(/</g,'‹').replace(/>/g,'›');
    const cue=new VTTCue(s.start,Math.max(s.start+0.001,s.end-0.01),text);cue.line=-2;if(typeof positionNativeCue==='function')positionNativeCue(cue);track.addCue(cue);
  }
  track.mode=state.mode==='off'?'disabled':typeof nativeCaptionPresentation==='function'?(nativeCaptionPresentation()?'showing':'hidden'):'showing';
  if(typeof syncCaptionTracks==='function')syncCaptionTracks();
  if(typeof renderCurrentCaption==='function'){track.oncuechange=()=>renderCurrentCaption();renderCurrentCaption();}
}
let activeId=null;
function updateActive(scroll=true){
  const time=$('video').currentTime;
  const s=state.segments.find(s=>s.start<=time&&s.end>time);
  const id=s?.id??null;
  if(typeof renderCurrentCaption==='function')renderCurrentCaption(s);
  if(id===activeId&&!scroll)return;
  if(id!==activeId){
    document.querySelector('.subtitle-row.active')?.classList.remove('active');
    const row=id===null?null:document.querySelector(`[data-segment="${id}"]`);row?.classList.add('active');
    if(row&&state.follow&&scroll){const box=$('transcript');box.scrollTop=Math.max(0,row.offsetTop-box.offsetTop-box.clientHeight/2)}
    activeId=id;
  }
}
async function selectJob(id){
  // Save the previous video's time before changing the selected job.
  if(state.videoId){const old=state.videoId;const position=$('video').currentTime;$('video').pause();api(`/jobs/${old}/position`,{position}).catch(()=>{})}
  state.selected=id;localStorage.setItem('selected-job',id);state.fingerprint='';state.segments=[];activeId=null;
  $('search').value='';renderLibrary();syncPlayer();await refreshDetail();
}
async function refreshDetail(){
  if(!state.selected)return;
  const id=state.selected;
  const detail=await api(`/jobs/${id}`,undefined,'GET');
  const review=detail.review_segments||[];
  $('asr-review').hidden=!review.length;
  const reviewKey=JSON.stringify(review);
  if($('asr-review').dataset.key!==reviewKey){
    $('asr-review').dataset.key=reviewKey;
    $('asr-review').innerHTML=review.length?`<summary>${review.length} 段识别存疑，已排除出字幕与翻译</summary><p>可对照原视频核对；这些内容没有交给 AI 翻译。</p>`+review.map(s=>`<p><button class="text-button" data-start="${s.start}">${clock(s.start)}</button> ${escapeHTML(s.reason)} · ${escapeHTML(s.text)}</p>`).join(''):'';
  }
  if(id!==state.selected)return;
  const index=state.jobs.findIndex(j=>j.id===id);if(index>=0)state.jobs[index]=detail.job;
  const fingerprint=JSON.stringify(detail.segments);
  if(fingerprint!==state.fingerprint){state.fingerprint=fingerprint;state.segments=detail.segments;renderTranscript();updateCues()}
  syncPlayer();
}
async function refresh(){
  if(state.polling)return;state.polling=true;
  try{
    const result=await api('/state',undefined,'GET');
    state.jobs=result.jobs;state.settings=result.settings;state.languages=result.languages;state.sourceLanguages=result.source_languages||result.languages;
    const importedId=new URLSearchParams(location.search).get('job');
    if(importedId&&state.jobs.some(j=>j.id===importedId)){state.selected=importedId;localStorage.setItem('selected-job',importedId);state.fingerprint='';history.replaceState(null,'',location.pathname);}
    if(['returned'].includes(new URLSearchParams(location.search).get('chatgpt'))||new URLSearchParams(location.search).get('settings')==='chatgpt'){history.replaceState(null,'',location.pathname);openSettings();if(state.profile!=='chatgpt'){captureProfile();showProfile('chatgpt')}}
    $('connection-state').textContent='已连接';$('model-status').textContent=result.model.message;
    $('model-footnote').textContent=result.model.state==='ready'?result.model.message:'本地识别声音，AI 翻译文字。';
    if(!state.jobs.some(j=>j.id===state.selected)){state.selected=null;resetPlayer();}
    if(!state.selected&&state.jobs.length){const saved=localStorage.getItem('selected-job');state.selected=state.jobs.some(j=>j.id===saved)?saved:state.jobs[0].id}
    renderLibrary();if(state.selected)await refreshDetail();
  }catch(e){$('connection-state').textContent='连接中断'}
  finally{state.polling=false}
}
function fillLanguage(select,value,auto=false){select.innerHTML=(auto?'<option value="">自动识别</option>':'')+Object.entries(state.languages).map(([id,name])=>`<option value="${id}">${escapeHTML(name)}</option>`).join('');select.value=value}
function openImport(files){
  if(!state.settings){toast('本地服务尚未连接，请稍等。');return}
  fillLanguage($('import-target'),state.settings.target);$('source-language').innerHTML='<option value="">自动检测主要语言并固定</option><option value="multi">多语言对白（允许切换语言）</option>'+Object.entries(state.sourceLanguages||state.languages).map(([id,name])=>`<option value="${escapeHTML(id)}">${escapeHTML(name)}（固定）</option>`).join('');
  $('import-provider').innerHTML=state.settings.providers.map(p=>`<option value="${p.id}">${escapeHTML(p.name)}${p.model?' · '+escapeHTML(p.model):''}</option>`).join('');
  $('import-provider').value=state.settings.default_provider;providerHint();
  $('import-error').hidden=true;$('upload-status').textContent='导入后先等待，由你开始下载和处理';
  if(files){state.files=Array.from(files);setImportTab('files');renderFiles()}
  $('import-dialog').showModal();
}
function providerHint(){const p=state.settings.providers.find(p=>p.id===$('import-provider').value);$('import-provider-hint').textContent=(p?.has_key||p?.id==='chatgpt')&&p.model?'原文识别在本机完成；点击开始翻译后才会使用所选服务。':'可以先免费识别原文；需要翻译时再配置 AI 服务。'}
function setImportTab(tab){state.tab=tab;document.querySelectorAll('[data-import-tab]').forEach(b=>b.classList.toggle('active',b.dataset.importTab===tab));$('link-panel').hidden=tab!=='links';$('file-panel').hidden=tab!=='files'}
function renderFiles(){$('file-list').innerHTML=state.files.map(f=>`<div class="file-chip"><span>${escapeHTML(f.name)}</span><span>${size(f.size)}</span></div>`).join('')}
function captureProfile(){const p=state.drafts.find(p=>p.id===state.profile);if(!p)return;p.base_url=$('base-url').value.trim();p.model=$('model-name').value.trim();p.api_key=$('api-key').value.trim();p.clear_key=$('clear-key').checked;if(state.profile==='chatgpt'&&$('codex-effort').value)p.reasoning_effort=$('codex-effort').value}
function renderModelChoices(){const models=state.models[state.profile]||[];$('model-choice').innerHTML='<option value="">选择模型</option>'+models.map(m=>`<option value="${escapeHTML(m)}">${escapeHTML(m)}</option>`).join('');$('model-choice').value=models.includes($('model-name').value)?$('model-name').value:'';$('model-choice').hidden=!models.length}
const ZHIPU_MODELS=['glm-5.3-flash','glm-5.3','glm-5.2','glm-5.1','glm-5-turbo','glm-5','glm-4.7','glm-4.7-flash','glm-4.7-flashx','glm-4.6','glm-4.5-air','glm-4.5-airx','glm-4.5-flash','glm-4-flash-250414','glm-4-flashx-250414'];
function usesZhipuPresets(){return state.profile==='zhipu'&&$('base-url').value.trim().replace(/\/+$/,'')==='https://open.bigmodel.cn/api/paas/v4'}
function updateProviderControls(){
  const subscription=state.profile==='chatgpt';
  $('chatgpt-panel').hidden=!subscription;
  $('codex-effort-field').hidden=!subscription;
  for(const id of ['base-url','api-key'])$(id).closest('label').hidden=subscription;
  $('clear-key').closest('.key-hint').hidden=subscription;
  $('model-api-note').textContent=subscription?'自动读取本机 Codex 模型目录；无需填写 API Key。':'使用支持文字输出的 Chat Completions 兼容模型。';
  $('test-provider').textContent=subscription?'测试订阅翻译（使用额度）':'测试翻译连接（使用 token）';
  $('model-name').readOnly=subscription;
  if(subscription){renderCodexEfforts(state.drafts.find(p=>p.id==='chatgpt')?.reasoning_effort);refreshChatGPTStatus();}
  const preset=usesZhipuPresets();
  if(preset)state.models[state.profile]=[...ZHIPU_MODELS];
  $('load-models').textContent=preset?'预置模型':'读取模型';
  $('provider-note').hidden=state.profile!=='zhipu';
  $('provider-note').textContent=preset?'使用智谱开放平台 API Key。下拉列表为官方文档预置模型，实际可用性以你的账户为准；也可手动填写模型名称。':'使用你填写的兼容接口；请确认 API Key 和模型适用于该地址。';
  renderModelChoices();
}
function showProfile(id){state.profile=id;const p=state.drafts.find(p=>p.id===id);$('base-url').value=p.base_url;$('api-key').value=p.api_key||'';$('api-key').placeholder=p.has_key?'已保存，留空可保持原 Key':'输入你的 API Key';$('model-name').value=p.model;$('clear-key').checked=!!p.clear_key;$('key-status').textContent=p.has_key?'已使用 Windows 账户加密保存':'仅在本机加密保存';updateProviderControls();$('settings-message').textContent='';$('provider-tabs').innerHTML=state.drafts.map(p=>`<button type="button" data-profile="${p.id}" class="${p.id===id?'active':''}">${escapeHTML(p.name)}</button>`).join('')}
function openSettings(){if(!state.settings){toast('本地服务尚未连接。');return}state.drafts=structuredClone(state.settings.providers);showProfile(state.settings.default_provider);$('asr-model').value=state.settings.asr_model;$('device').value=state.settings.device;$('conversion-device').value=state.settings.conversion_device||'auto';$('settings-message').textContent='';$('settings-dialog').showModal()}
async function saveSettings(){captureProfile();const result=await api('/settings',{providers:state.drafts.map(({id,name,base_url,model,api_key,clear_key,reasoning_effort})=>({id,name,base_url,model,reasoning_effort:reasoning_effort||'',api_key:api_key||'',clear_key:!!clear_key})),default_provider:state.profile,target:state.settings.target,asr_model:$('asr-model').value,device:$('device').value,conversion_device:$('conversion-device').value});state.settings=result;state.drafts=structuredClone(result.providers);showProfile(state.profile);return result}
document.addEventListener('click',async event=>{
  const b=event.target.closest('button');if(!b)return;
  if(b.dataset.action==='import')openImport();
  if(b.dataset.action==='settings')openSettings();
  if(b.dataset.close)$(b.dataset.close).close();
  if(b.dataset.job)await selectJob(b.dataset.job);
  if(b.dataset.filter){state.filter=b.dataset.filter;document.querySelectorAll('[data-filter]').forEach(x=>x.classList.toggle('active',x===b));renderLibrary()}
  if(b.dataset.mode){state.mode=b.dataset.mode;localStorage.setItem('subtitle-mode',state.mode);document.querySelectorAll('[data-mode]').forEach(x=>x.classList.toggle('selected',x.dataset.mode===state.mode));activeId=null;renderTranscript();updateCues()}
  if(b.dataset.importTab)setImportTab(b.dataset.importTab);
  if(b.dataset.profile){captureProfile();showProfile(b.dataset.profile)}
  if(b.dataset.start!==undefined&&current()?.playable){$('video').currentTime=Number(b.dataset.start);$('video').play().catch(()=>{})}
});
$('files').addEventListener('change',e=>{state.files=Array.from(e.target.files);renderFiles()});
$('import-provider').addEventListener('change',providerHint);
for(const zone of [$('drop-zone'),$('screen')]){
  zone.addEventListener('dragover',e=>{e.preventDefault();zone.classList.add('dragging')});
  zone.addEventListener('dragleave',()=>zone.classList.remove('dragging'));
  zone.addEventListener('drop',e=>{e.preventDefault();zone.classList.remove('dragging');if(zone===$('screen'))openImport(e.dataTransfer.files);else{state.files=Array.from(e.dataTransfer.files);renderFiles()}});
}
$('import-form').addEventListener('submit',async e=>{
  e.preventDefault();$('import-error').hidden=true;const button=$('start-button');button.disabled=true;
  try{
    let result;
    if(state.tab==='links'){
      const urls=$('urls').value.split(/\r?\n/).map(u=>u.trim()).filter(Boolean);
      if(!urls.length||urls.length>10)throw new Error('请输入 1–10 个链接，每行一个。');
      result=await api('/jobs/links',{urls,provider_id:$('import-provider').value,target:$('import-target').value,source_language:$('source-language').value});
    }else{
      if(!state.files.length||state.files.length>10)throw new Error('请选择 1–10 个视频或音频文件。');
      const data=new FormData();state.files.forEach(f=>data.append('files',f));data.append('provider_id',$('import-provider').value);data.append('target',$('import-target').value);data.append('source_language',$('source-language').value);
      $('upload-status').textContent='正在复制文件，请保持此窗口开启…';button.textContent='正在导入…';
      result=await api('/jobs/files',data);
    }
    $('import-dialog').close();$('urls').value='';state.files=[];$('files').value='';renderFiles();
    state.selected=result.jobs[0].id;state.fingerprint='';await refresh();toast(`${result.jobs.length} 个视频已加入队列`);
  }catch(error){$('import-error').textContent=error.message;$('import-error').hidden=false}
  finally{button.disabled=false;button.textContent='导入并识别原文'}
});
$('settings-form').addEventListener('submit',async e=>{e.preventDefault();try{await saveSettings();$('settings-dialog').close();toast('设置已保存');await refresh()}catch(error){$('settings-message').textContent=error.message}});
async function settingsAction(button,fn){button.disabled=true;$('settings-message').textContent='正在连接…';try{await saveSettings();await fn()}catch(error){$('settings-message').textContent=error.message}finally{button.disabled=false}}
$('load-models').addEventListener('click',()=>{if(usesZhipuPresets()){updateProviderControls();$('settings-message').textContent='已显示官方文档预置模型；未发送 API 请求，也未消耗 token。';$('model-choice').focus();return}return settingsAction($('load-models'),async()=>{const data=await api(`/providers/${state.profile}/models`,{});state.models[state.profile]=data.models;if(!$('model-name').value&&(data.models.length===1||state.profile==='chatgpt'))$('model-name').value=data.models[0];renderModelChoices();if(state.profile==='chatgpt'){codexCatalog=data.catalog||codexCatalog;renderCodexEfforts(state.drafts.find(p=>p.id==='chatgpt')?.reasoning_effort)}$('settings-message').textContent=`读取到 ${data.models.length} 个模型，请在下拉列表中选择。`;$('model-choice').focus()})});
$('model-choice').addEventListener('change',()=>{if($('model-choice').value)$('model-name').value=$('model-choice').value});
$('model-name').addEventListener('input',()=>{$('model-choice').value=(state.models[state.profile]||[]).includes($('model-name').value)?$('model-name').value:''});
$('base-url').addEventListener('input',()=>{delete state.models[state.profile];updateProviderControls()});
$('test-provider').addEventListener('click',()=>settingsAction($('test-provider'),async()=>{const data=await api(`/providers/${state.profile}/test`,{});$('settings-message').textContent=data.message}));
$('prepare-model').addEventListener('click',()=>settingsAction($('prepare-model'),async()=>{await api('/model/prepare',{});$('settings-message').textContent='模型正在后台准备，完成后会显示就绪。'}));
$('job-action').addEventListener('click',async()=>{const j=current();if(!j)return;await runTaskAction(taskAction(j).action,j.id)});
$('clear-media').addEventListener('click',async()=>{const j=current();if(!j)return;if(!confirm('清理这个视频在工具内的媒体缓存？原始文件不会被修改，字幕和观看记录会保留。'))return;try{const data=await api(`/jobs/${j.id}/clear-media`,{});await refresh();toast(`已释放 ${size(data.bytes)}，字幕仍保留。`)}catch(error){toast(error.message)}});
$('export-button').addEventListener('click',()=>{const j=current();if(!j)return;const a=document.createElement('a');a.href=`/api/jobs/${j.id}/subtitles?format=srt&mode=${state.mode==='off'?'translated':state.mode}`;a.download='subtitles.srt';a.click()});
$('speed').addEventListener('change',()=>{$('video').playbackRate=Number($('speed').value)});
$('search').addEventListener('input',()=>{activeId=null;renderTranscript()});
$('follow-toggle').addEventListener('click',()=>{state.follow=!state.follow;$('follow-toggle').classList.toggle('selected',state.follow)});
let positionSaved=0;
$('video').addEventListener('timeupdate',()=>{updateActive();if(state.selected&&Date.now()-positionSaved>5000){positionSaved=Date.now();api(`/jobs/${state.selected}/position`,{position:$('video').currentTime}).catch(()=>{})}});
$('video').addEventListener('pause',()=>{if(state.videoId)api(`/jobs/${state.videoId}/position`,{position:$('video').currentTime}).catch(()=>{})});
$('video').addEventListener('error',()=>{if(state.videoId)toast('播放器无法读取视频。请确认本地服务正在运行，或重新导入文件。')});
document.querySelectorAll('[data-mode]').forEach(x=>x.classList.toggle('selected',x.dataset.mode===state.mode));


let chatgptPollBusy=false, codexCatalog=[], codexIdentity='', codexBusy=false;
const effortNames={none:'不思考',minimal:'极轻度',low:'轻度',medium:'中度',high:'深度',xhigh:'超深度',max:'最高强度',ultra:'超高强度'};
function renderCodexEfforts(preferred){
  const m=codexCatalog.find(m=>m.model===$('model-name').value), choices=m?.supportedReasoningEfforts||[];
  const previous=preferred||$('codex-effort').value;
  $('codex-effort').innerHTML=choices.map(e=>`<option value="${escapeHTML(e.reasoningEffort)}">${escapeHTML(effortNames[e.reasoningEffort]||e.reasoningEffort)}</option>`).join('');
  $('codex-effort').value=choices.some(e=>e.reasoningEffort===previous)?previous:(m?.defaultReasoningEffort||'');
  $('codex-effort').disabled=codexBusy||!choices.length;
  $('codex-effort-note').textContent=!choices.length?'暂未读取到模型支持的思考档位，请先连接 Codex 并读取模型。':codexBusy?'当前任务的设置已固定；任务结束后可调整思考强度。':'只显示当前模型支持的档位；保存后用于下一次翻译。';
}
async function refreshChatGPTStatus(){
  if(chatgptPollBusy)return;chatgptPollBusy=true;
  try{
    const data=await api('/codex/status',undefined,'GET');
    codexBusy=!!data.busy;
    $('codex-account').textContent=data.account?`使用账号：${data.account.email||'官方未返回邮箱'} · ${data.account.planType}`:data.login_required?'本机 Codex 尚未登录':data.disconnected?'本 App 已断开连接':'本机账号检测暂不可用';
    $('chatgpt-status').textContent=data.message||'';
    $('chatgpt-login').textContent=data.disconnected?'连接本机 Codex':data.connected?'重新检测':data.login_required?'登录 Codex':'重新检测账号';
    $('chatgpt-login').disabled=codexBusy;
    $('chatgpt-logout').disabled=!data.connected;
    $('codex-cancel').hidden=!codexBusy;
    if(data.connected||!data.login_pending){$('chatgpt-auth-link').hidden=true;$('chatgpt-auth-link').removeAttribute('href');}
    const limits=data.limits, buckets=limits?.rateLimitsByLimitId||{codex:limits?.rateLimits};
    const windows=Object.entries(buckets).flatMap(([id,b])=>['primary','secondary'].filter(k=>b?.[k]).map(k=>{const w=b[k];return `${id} · ${w.windowDurationMins?Math.round(w.windowDurationMins/60)+' 小时窗口':'额度窗口'}剩余 ${Math.min(100,Math.max(0,100-w.usedPercent))}%${w.resetsAt?'，'+new Date(w.resetsAt*1000).toLocaleString()+' 重置':''}`}));
    $('codex-quota').textContent=(windows.join('；')||'额度状态暂不可用')+(limits?.ordinaryUsageAllowed===false?' · 当前订阅额度不可用，翻译已暂停':limits?.ordinaryUsageAllowed!==true?' · 订阅可用性暂无法确认，翻译将暂停':'');
    if(data.connected){
      $('chatgpt-auth-link').hidden=true;
      const identity=data.account_id+'|'+data.account.email;
      codexCatalog=data.models||[];
      const p=state.drafts?.find(p=>p.id==='chatgpt');
      state.models.chatgpt=codexCatalog.map(m=>m.model);
      if(p&&state.profile==='chatgpt'){
        const changed=codexIdentity!==identity;
        if(changed||!state.models.chatgpt.includes($('model-name').value)){
          const selected=data.selection||data.execution;
          if(selected){$('model-name').value=selected.model;p.model=selected.model;p.reasoning_effort=selected.reasoning_effort;}
        }
        renderModelChoices();renderCodexEfforts(changed?p.reasoning_effort:undefined);
        $('model-choice').disabled=codexBusy;
      }
      codexIdentity=identity;
    }else{
      codexIdentity='';codexCatalog=[];delete state.models.chatgpt;
      if(state.profile==='chatgpt'){renderModelChoices();renderCodexEfforts();}
    }
  }catch(e){$('chatgpt-status').textContent=e.message;codexCatalog=[];$('chatgpt-login').textContent='重新检测账号';$('chatgpt-login').disabled=false;$('chatgpt-auth-link').hidden=true;$('chatgpt-auth-link').removeAttribute('href');}
  finally{chatgptPollBusy=false}
}
$('chatgpt-login').addEventListener('click',async()=>{
  $('chatgpt-login').disabled=true;
  try{
    const status=await api('/codex/connect',{});
    if(!status.connected&&status.login_required){
      const data=await api('/codex/login',{});
      if(data.url){const link=$('chatgpt-auth-link');link.href=data.url;link.hidden=false;window.open(data.url,'_blank','noopener');}
    }
    await refreshChatGPTStatus();
  }catch(e){$('chatgpt-status').textContent=e.message}
  finally{$('chatgpt-login').disabled=false}
});
$('chatgpt-logout').addEventListener('click',async()=>{try{await api('/codex/disconnect',{});await refreshChatGPTStatus()}catch(e){$('chatgpt-status').textContent=e.message}});
$('codex-cancel').addEventListener('click',async()=>{await api('/codex/cancel',{});$('chatgpt-status').textContent='正在取消，已完成字幕会保留';});
$('model-choice').addEventListener('change',()=>{if(state.profile==='chatgpt')renderCodexEfforts()});
window.addEventListener('focus',refreshChatGPTStatus);
document.addEventListener('visibilitychange',()=>{if(!document.hidden)refreshChatGPTStatus()});
setInterval(()=>{if(!document.hidden)refreshChatGPTStatus()},5000);
refreshChatGPTStatus();
