'use strict';

const CAPTION_DEFAULTS={size:24,x:50,y:78};
let captionStyle={...CAPTION_DEFAULTS}, captionKey='', captionPointer=null;
try{const saved=JSON.parse(localStorage.getItem('yizhen-caption')||'{}');for(const k of ['size','x','y'])if(Number.isFinite(saved[k]))captionStyle[k]=saved[k]}catch{}
const bound=(value,min,max)=>Math.max(min,Math.min(max,value));
captionStyle.size=bound(captionStyle.size,14,46);captionStyle.x=bound(captionStyle.x,8,92);captionStyle.y=bound(captionStyle.y,6,84);
function saveCaptionStyle(){try{localStorage.setItem('yizhen-caption',JSON.stringify(captionStyle))}catch{}}
function setTheme(value){
  const theme=value==='light'?'light':'dark';document.documentElement.dataset.theme=theme;$('theme-choice').value=theme;
  try{localStorage.setItem('yizhen-theme',theme)}catch{}
}
$('theme-choice').value=document.documentElement.dataset.theme||'dark';
$('theme-choice').addEventListener('change',event=>setTheme(event.target.value));

function nativeCaptionPresentation(){
  const video=$('video');return !!(video.webkitDisplayingFullscreen||document.pictureInPictureElement===video||document.fullscreenElement===video);
}
function positionNativeCue(cue){cue.snapToLines=false;cue.line=captionStyle.y;cue.position=captionStyle.x;cue.lineAlign='center';cue.positionAlign='center';cue.align='center';cue.size=90}
let syncingCaptionTracks=false;
function syncCaptionTracks(){
  if(syncingCaptionTracks)return;
  syncingCaptionTracks=true;
  try{
    const native=nativeCaptionPresentation()&&state.mode!=='off';
    for(const item of $('video').textTracks){
      const mode=typeof track!=='undefined'&&item===track?(native?'showing':'hidden'):'disabled';
      if(item.mode!==mode)item.mode=mode;
    }
  }finally{syncingCaptionTracks=false}
}
function layoutCaption(){
  const overlay=$('caption-overlay'),screen=$('screen');if(overlay.hidden)return;
  overlay.style.fontSize=captionStyle.size+'px';
  const width=screen.clientWidth,height=screen.clientHeight;if(!width||!height)return;
  const natural=Math.max(1,...Array.from($('caption-text').children).map(el=>el.scrollWidth));
  overlay.style.fontSize=Math.max(10,captionStyle.size*Math.min(1,(width-32)/natural))+'px';
  const box=overlay.getBoundingClientRect(),halfX=(box.width/2+8)/width*100,halfY=(box.height/2+8)/height*100;
  overlay.style.left=bound(captionStyle.x,Math.min(50,halfX),Math.max(50,100-halfX))+'%';
  // Keep the last line above native playback controls, including small windows.
  const bottom=Math.max(halfY,(height-80-box.height/2)/height*100);
  overlay.style.top=bound(captionStyle.y,halfY,bottom)+'%';
}
function applyCaptionStyle(persist=true){
  const screen=$('screen'),overlay=$('caption-overlay');
  overlay.style.left=captionStyle.x+'%';overlay.style.top=captionStyle.y+'%';screen.style.setProperty('--caption-size',captionStyle.size+'px');
  $('subtitle-size').value=captionStyle.size;$('subtitle-size-value').value=captionStyle.size+' px';
  if(typeof track!=='undefined'&&track?.cues)for(const cue of track.cues)positionNativeCue(cue);
  layoutCaption();
  if(persist)saveCaptionStyle();
}
function renderCurrentCaption(segment){
  const overlay=$('caption-overlay'),video=$('video');
  if(segment===undefined)segment=state.segments.find(s=>s.start<=video.currentTime&&s.end>video.currentTime);
  const hidden=state.mode==='off'||!current()?.playable||!segment||nativeCaptionPresentation();
  const lines=hidden?[]:state.mode==='original'?[segment.text]:state.mode==='bilingual'?[segment.translation,segment.text].filter(Boolean):[segment.translation].filter(Boolean);
  overlay.hidden=!lines.length;
  const key=JSON.stringify(lines);
  if(key!==captionKey){
    captionKey=key;$('caption-text').replaceChildren(...lines.map((text,index)=>{const span=document.createElement('span');span.textContent=text;if(lines.length>1&&index===lines.length-1)span.className='caption-original';return span}));
    layoutCaption();
  }
}
$('subtitle-display').value=state.mode;
$('subtitle-display').addEventListener('change',event=>{
  state.mode=event.target.value;localStorage.setItem('subtitle-mode',state.mode);activeId=null;renderTranscript();updateCues();
});
$('subtitle-size').addEventListener('input',event=>{captionStyle.size=Number(event.target.value);applyCaptionStyle()});
for(const [id,delta] of [['caption-smaller',-2],['caption-larger',2]])$(id).addEventListener('click',event=>{event.stopPropagation();captionStyle.size=bound(captionStyle.size+delta,14,46);applyCaptionStyle()});
$('caption-reset').addEventListener('click',()=>{captionStyle={...CAPTION_DEFAULTS};applyCaptionStyle()});
document.querySelectorAll('[data-caption-anchor]').forEach(button=>button.addEventListener('click',()=>{captionStyle.x=50;captionStyle.y={top:15,middle:48,bottom:78}[button.dataset.captionAnchor];applyCaptionStyle()}));
$('caption-overlay').addEventListener('pointerdown',event=>{
  if(event.button!==0||event.target.closest('button'))return;
  event.preventDefault();event.stopPropagation();
  const rect=$('screen').getBoundingClientRect();captionPointer={id:event.pointerId,startX:event.clientX,startY:event.clientY,x:captionStyle.x,y:captionStyle.y,width:rect.width,height:rect.height};
  event.currentTarget.setPointerCapture(event.pointerId);event.currentTarget.classList.add('is-dragging');
});
$('caption-overlay').addEventListener('pointermove',event=>{
  if(captionPointer?.id!==event.pointerId)return;
  const p=captionPointer;captionStyle.x=bound(p.x+(event.clientX-p.startX)/p.width*100,8,92);captionStyle.y=bound(p.y+(event.clientY-p.startY)/p.height*100,6,84);applyCaptionStyle(false);
});
function endCaptionDrag(event){if(captionPointer?.id!==event.pointerId)return;captionPointer=null;$('caption-overlay').classList.remove('is-dragging');saveCaptionStyle()}
for(const name of ['pointerup','pointercancel','lostpointercapture'])$('caption-overlay').addEventListener(name,endCaptionDrag);
$('caption-overlay').addEventListener('keydown',event=>{
  if(event.target.closest('button')||!['ArrowLeft','ArrowRight','ArrowUp','ArrowDown'].includes(event.key))return;
  event.preventDefault();event.stopPropagation();
  captionStyle.x=bound(captionStyle.x+({ArrowLeft:-1,ArrowRight:1}[event.key]||0),8,92);captionStyle.y=bound(captionStyle.y+({ArrowUp:-1,ArrowDown:1}[event.key]||0),6,84);applyCaptionStyle();
});
for(const name of ['loadedmetadata','seeked','timeupdate','enterpictureinpicture','leavepictureinpicture','webkitbeginfullscreen','webkitendfullscreen'])$('video').addEventListener(name,()=>{syncCaptionTracks();renderCurrentCaption()});
async function togglePlayerFullscreen(){try{if(document.fullscreenElement)await document.exitFullscreen();else await $('screen').requestFullscreen()}catch{toast('浏览器暂时无法进入全屏。')}}
$('player-fullscreen').addEventListener('click',togglePlayerFullscreen);
$('video').addEventListener('dblclick',togglePlayerFullscreen);
document.addEventListener('fullscreenchange',()=>{syncCaptionTracks();renderCurrentCaption()});
// Only one of the two small player menus stays open.
for(const id of ['caption-appearance','player-more'])$(id).addEventListener('toggle',()=>{if($(id).open)$(id==='caption-appearance'?'player-more':'caption-appearance').open=false});
document.addEventListener('pointerdown',event=>{for(const id of ['caption-appearance','player-more'])if(!$(id).contains(event.target))$(id).open=false});

function compactJobProgress(job){
  if(!job||!['processing','queued','uploading'].includes(job.status))return null;
  if(job.status==='queued')return {label:'等待处理',unknown:true,value:0};
  if(job.status==='uploading')return {label:'导入视频',unknown:true,value:0};
  const p=Number(job.progress)||0,stages=localStageState(job);
  if(p<19)return {...stages.download,label:job.kind==='file'?'导入视频':'下载视频',approximate:true};
  if(p<24)return {...stages.conversion,label:'视频格式转换'};
  if(p<28)return {label:p<27?'准备识别音频':'加载识别模型',unknown:true,value:0};
  if(p<70)return {...stages.recognition,label:'识别对白',approximate:true};
  const total=Number(job.segment_count)||0,done=Number(job.translated_count)||0;
  return {label:'翻译字幕',unknown:!total||!done,value:total?bound(done/total*100,0,100):0};
}
function libraryStatusLabel(job){const stage=compactJobProgress(job);return stage?`${stage.label}${stage.unknown?'…':` ${stage.approximate?'约 ':''}${Math.floor(stage.value)}%`}`:labels[job.status]||job.status}
function libraryProgressMarkup(job){const stage=compactJobProgress(job);if(!stage)return '';return `<span class="library-progress${stage.unknown?' indeterminate':''}" aria-hidden="true"><i style="width:${stage.unknown?30:stage.value}%"></i></span>`}
function renderStudioJob(job){
  const process=$('process'),meter=$('process-mini');process.dataset.state=job?.status||'empty';process.hidden=!job;meter.hidden=true;
  if(typeof syncPlaybackControls==='function')syncPlaybackControls();
  if(!job){$('caption-overlay').hidden=true;return}
  const phase=compactJobProgress(job);
  $('process-note').hidden=!!phase||['completed','needs_translation','needs_config'].includes(job.status);
  if(phase){
    $('process-title').textContent=phase.label;meter.hidden=false;meter.dataset.unknown=String(phase.unknown);$('process-mini-fill').style.width=(phase.unknown?30:phase.value)+'%';
    const label=phase.unknown?'等待进度':`${phase.approximate?'约 ':''}${Math.floor(phase.value)}%`;
    $('process-mini-label').textContent=label;meter.setAttribute('aria-label',phase.label);meter.setAttribute('aria-valuetext',label);
    if(phase.unknown)meter.removeAttribute('aria-valuenow');else meter.setAttribute('aria-valuenow',String(Math.round(phase.value)));
  }else if(job.status==='completed')$('process-title').textContent='字幕已就绪';
  else if(['needs_translation','needs_config'].includes(job.status))$('process-title').textContent='原文已就绪';
  else if(job.status==='failed'){$('process-note').textContent=job.error||job.message;$('process-note').hidden=false;}
  $('asr-review').dataset.compact='true';renderCurrentCaption();
}
function syncSourceLanguage(job,active){
  const localized={zh:'简体中文',en:'英语',ja:'日语',ko:'韩语',fr:'法语',de:'德语',es:'西班牙语'};
  const select=$('video-source'),names={...(state.sourceLanguages||state.languages)},mode=job.recognition_language??'';
  for(const id of Object.keys(names))names[id]=localized[id]||names[id];
  for(const option of $('video-target').options)option.textContent=localized[option.value]||option.textContent;
  const detected=names[job.source_language]||job.source_language;
  const key=JSON.stringify(names)+detected;
  if(select.dataset.options!==key){select.innerHTML=`<option value="">自动${detected?' · '+escapeHTML(detected):'识别'}</option>`+Object.entries(names).map(([id,name])=>`<option value="${escapeHTML(id)}">${escapeHTML(name)}</option>`).join('')+'<option value="multi">多语言</option>';select.dataset.options=key;}
  select.value=mode;select.disabled=active||job.status==='uploading';
}
$('video-source').addEventListener('change',async event=>{
  const job=current();if(!job)return;const language=event.target.value,previous=job.recognition_language??'';if(language===previous)return;
  const dialog=$('source-confirm-dialog');dialog.returnValue='';dialog.showModal();
  const accepted=await new Promise(resolve=>dialog.addEventListener('close',()=>resolve(dialog.returnValue==='confirm'),{once:true}));
  if(!accepted){event.target.value=previous;return}
  event.target.disabled=true;
  try{await api(`/jobs/${job.id}/source`,{source_language:language});state.fingerprint='';state.segments=[];captionKey='';updateCues();toast('已备份字幕，正在按所选语言重新识别；不会自动调用翻译。');await refresh()}
  catch(error){toast(error.message);event.target.value=previous;event.target.disabled=false}
});
new ResizeObserver(layoutCaption).observe($('screen'));
applyCaptionStyle(false);renderStudioJob(current());
