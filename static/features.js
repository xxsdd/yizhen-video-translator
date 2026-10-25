'use strict';
state.checked=new Set();
let lookupContext=null,lookupSerial=0,vocabularyWords=[],translationBusy=false;

function resetPlayer(){
  const video=$('video');video.pause();video.removeAttribute('src');video.removeAttribute('poster');video.load();state.videoId=null;state.segments=[];state.fingerprint='';activeId=null;
  video.hidden=true;$('screen-empty').hidden=false;$('screen-title').textContent='从一段视频开始';$('screen-description').textContent='粘贴视频链接，或直接拖入本地文件';
  $('video-title').textContent='看视频，不被语言打断。';$('video-meta').textContent='导入后先识别原文，需要时再点击翻译。';$('translation-controls').hidden=true;$('last-translation-error').hidden=true;$('recheck-speech').hidden=true;$('asr-review').hidden=true;$('delete-current').hidden=true;$('clear-media').hidden=true;$('job-action').hidden=true;$('job-error').hidden=true;
  if(typeof renderStudioJob==='function')renderStudioJob(null);$('process-title').textContent='准备就绪';$('process-note').textContent='导入后先在本地识别原文';$('progress-bar').style.width='0%';syncProcessDetails(null);renderLocalProgress(null);renderTranslationActivity();renderTranscript();updateCues();
}
function renderSelection(){
  state.checked=new Set([...state.checked].filter(id=>state.jobs.some(j=>j.id===id)));
  $('delete-selected').disabled=!state.checked.size;$('delete-selected').textContent=state.checked.size?`删除所选 (${state.checked.size})`:'删除所选';
}
function syncTranslationControls(j,active){
  $('translation-controls').hidden=false;
  const previous=j.last_translation_error, diagnostic=$('last-translation-error');diagnostic.hidden=!previous; if(previous&&diagnostic.dataset.errorKey!==JSON.stringify(previous)){diagnostic.dataset.errorKey=JSON.stringify(previous);diagnostic.open=true;} if(previous)diagnostic.querySelector('p').textContent=`${previous.label}：${previous.message}${previous.diagnostic?.request_id?' 请求编号：'+previous.diagnostic.request_id:''}`;
  $('recheck-speech').hidden=!j.segment_count||j.media_cleared; $('recheck-speech').disabled=active||j.status==='uploading';
  if(translationBusy)return;
  if(!$('video-target').options.length||$('video-target').value!==j.target)fillLanguage($('video-target'),j.target);$('video-target').disabled=active||j.status==='uploading';
  if(typeof syncSourceLanguage==='function')syncSourceLanguage(j,active);
  $('source-language-label').textContent=`原语言：${j.recognition_language==='multi'?'多语言对白':(state.sourceLanguages?.[j.source_language]||state.languages[j.source_language]||j.source_language||'待检测')} →`;
  const done=j.segment_count>0&&j.translated_count===j.segment_count&&j.status==='completed';
  $('translate-now').hidden=done||active||j.status==='uploading'||!j.segment_count;
  $('translate-now').disabled=active||j.status==='uploading'||!j.segment_count||done;
  $('translate-now').textContent=done?'译文已保存':j.translated_count>0?'继续翻译新增字幕':'开始翻译';
  $('translation-hint').textContent=done?'译文已保存在本地，可以直接观看。':j.translated_count>0?`已有 ${j.translated_count} 条译文；点击继续后，按 AI 设置仅补翻缺失字幕。`:'服务、模型与思考强度在“翻译设置”中调整；点击“开始翻译”才会使用额度。';
}
function currentTranslationSettings(){
  const profile=state.settings.providers.find(p=>p.id===state.settings.default_provider);
  if(!profile?.model?.trim())throw new Error('请先在翻译设置中保存服务和模型。');
  return {provider_id:profile.id,model:profile.model.trim()};
}
function wordMarkup(text,segment,language){
  if(language!=='en')return escapeHTML(text);
  const pattern=/[A-Za-z]+(?:['’\-][A-Za-z]+)*/g;let html='',last=0;
  for(const match of text.matchAll(pattern)){
    html+=escapeHTML(text.slice(last,match.index))+`<button class="word-token" data-word="${escapeHTML(match[0])}" data-segid="${segment.id}" title="查询 ${escapeHTML(match[0])}">${escapeHTML(match[0])}</button>`;last=match.index+match[0].length;
  }
  return `<span data-english-sentence="${escapeHTML(text)}">${html+escapeHTML(text.slice(last))}</span>`;
}
function confirmDelete(count){
  const dialog=$('delete-confirm-dialog');dialog.returnValue='';$('delete-summary').textContent=`将删除这 ${count} 个视频，以及工具内的媒体副本、原文字幕、各语言译文和观看记录。此操作无法撤销。`;dialog.showModal();
  return new Promise(resolve=>dialog.addEventListener('close',()=>resolve(dialog.returnValue==='delete'),{once:true}));
}
async function deleteVideos(ids){
  if(!ids.length)return;
  if(ids.includes(state.videoId)){const video=$('video');video.pause();video.removeAttribute('src');video.removeAttribute('poster');video.load();state.videoId=null;}
  try{const result=await api('/jobs/delete',{ids});ids.forEach(id=>state.checked.delete(id));if(ids.includes(state.selected))state.selected=null;await refresh();toast(result.deleting?.length?'已移出列表，后台正在停止任务并清理文件':`已删除 ${ids.length} 个视频`)}
  catch(error){await refresh();toast(error.message)}
}
$('select-all').addEventListener('click',()=>{const visible=Array.from(document.querySelectorAll('[data-check-job]')).map(el=>el.dataset.checkJob);const all=visible.every(id=>state.checked.has(id));visible.forEach(id=>all?state.checked.delete(id):state.checked.add(id));renderLibrary()});
$('delete-selected').addEventListener('click',()=>deleteVideos([...state.checked]));
$('delete-current').addEventListener('click',()=>{if(current())deleteVideos([current().id])});
document.addEventListener('change',event=>{const id=event.target.dataset.checkJob;if(id){event.target.checked?state.checked.add(id):state.checked.delete(id);renderSelection()}});
$('video-target').addEventListener('change',async()=>{const j=current();if(!j)return;translationBusy=true;try{const result=await api(`/jobs/${j.id}/target`,{target:$('video-target').value});state.fingerprint='';toast(result.cached?'已读取保存的译文，未调用 AI':'目标语言已更改；点击开始翻译后才会运行')}catch(e){toast(e.message)}finally{translationBusy=false;await refresh()}});
$('translate-now').addEventListener('click',async()=>{const j=current();if(!j||translationBusy)return;translationBusy=true;$('translate-now').disabled=true;try{await api(`/jobs/${j.id}/translate`,currentTranslationSettings())}catch(e){toast(e.message)}finally{translationBusy=false;await refresh()}});

function renderDictionary(data){
  const sourceLink=['https://wordnet.princeton.edu/','https://github.com/skywind3000/ECDICT'].includes(data.source_url)?data.source_url:'https://dictionaryapi.dev/';
  let html=`<p class="dictionary-source">来源：<a href="${sourceLink}" target="_blank" rel="noopener noreferrer">${escapeHTML(data.source||'词典缓存')}</a></p>`;
  if(!data.bilingual&&data.entries?.length)html+='<p class="field-hint">暂未找到中文释义，以下为英英词典补充。</p>';
  if(!data.entries?.length)html+=`<p class="field-hint">${escapeHTML(data.message||'未保存词典释义，可以重新查询。')}</p>`;
  for(const entry of data.entries||[]){
    html+=`<p class="phonetic">${escapeHTML(entry.phonetic)}</p>`;
    if(entry.chinese)html+=`<p class="chinese-definition">${escapeHTML(entry.chinese)}</p><details class="english-definitions"><summary>查看英文释义</summary>`;
    html+='<dl class="definitions">'+(entry.definitions||[]).map(d=>`<dt>${escapeHTML(d.part)}</dt><dd>${escapeHTML(d.definition)}${d.example?`<small>例句：${escapeHTML(d.example)}</small>`:''}</dd>`).join('')+'</dl>';
    if(entry.chinese)html+='</details>';
    html+=`<p class="dictionary-source">${(entry.sources||[]).map(url=>`<a href="${escapeHTML(url)}" target="_blank" rel="noopener noreferrer">查看来源与授权</a>`).join(' · ')} ${escapeHTML(entry.license||'')}</p>`;
  }
  $('dictionary-result').innerHTML=html;
}
function presentLookup(context){
  $('video').pause();lookupContext=context;$('lookup-word').value=context.word||'';$('lookup-sentence').textContent=context.sentence?`字幕例句：${context.sentence}`:'';
  $('word-explanation').hidden=!context.explanation;$('word-explanation').textContent=context.explanation?`AI 中文解释：${context.explanation}`:'';
  $('save-word').disabled=!context.word;$('explain-word').disabled=!context.word;$('dictionary-message').textContent='本地英汉词典：直接显示中文释义，不使用 AI token。未收录时补充英英词典。';
  if(!$('dictionary-dialog').open)$('dictionary-dialog').showModal();
}
async function openLookup(word='',segment=null){
  const serial=++lookupSerial;const j=current();
  const context={word:word.trim().toLowerCase(),sentence:segment?.text||'',job_id:segment?j?.id:null,start:segment?.start||0,explanation:''};presentLookup(context);
  if(!context.word){$('dictionary-result').textContent='输入英文单词，或在字幕稿中点击英文单词。';$('lookup-word').focus();return;}
  $('dictionary-result').textContent='正在查询词典…';$('save-word').disabled=true;
  try{const data=await api('/dictionary?word='+encodeURIComponent(context.word),undefined,'GET');if(serial!==lookupSerial)return;context.dictionary=data;renderDictionary(data)}
  catch(e){if(serial===lookupSerial)$('dictionary-result').textContent=e.message}
  finally{if(serial===lookupSerial)$('save-word').disabled=false;}
}
document.addEventListener('click',event=>{const button=event.target.closest('[data-word]');if(!button)return;if(window.getSelection().toString().trim().includes(' '))return;const s=state.segments.find(s=>String(s.id)===button.dataset.segid);openLookup(button.dataset.word,{...s,text:button.closest('[data-english-sentence]').dataset.englishSentence})});
$('transcript').addEventListener('mouseup',event=>{if(event.button!==0)return;const selected=window.getSelection().toString().trim();const english=event.target.closest('[data-english-sentence]');if(!english||!selected.includes(' ')||selected.length>100||!/[a-z]/i.test(selected))return;const row=event.target.closest('[data-segment]');if(row)openLookup(selected,{...state.segments.find(s=>String(s.id)===row.dataset.segment),text:english.dataset.englishSentence})});
$('open-dictionary').addEventListener('click',()=>openLookup());
$('lookup-form').addEventListener('submit',event=>{event.preventDefault();openLookup($('lookup-word').value)});
$('pronounce-word').addEventListener('click',()=>{if(!lookupContext?.word)return;if(!('speechSynthesis' in window)){toast('此浏览器不支持系统发音。');return;}speechSynthesis.cancel();const utterance=new SpeechSynthesisUtterance(lookupContext.word);utterance.lang='en-US';speechSynthesis.speak(utterance)});
$('explain-word').addEventListener('click',async()=>{if(!lookupContext?.word)return;const context=lookupContext;$('explain-word').disabled=true;$('dictionary-message').textContent='正在使用所选 AI 服务生成中文解释…';try{const data=await api('/dictionary/explain',{word:context.word,sentence:context.sentence});context.explanation=data.explanation;if(lookupContext===context){$('word-explanation').textContent='AI 中文解释：'+data.explanation;$('word-explanation').hidden=false;$('dictionary-message').textContent='AI 解释供学习参考，词典原文和来源保留在上方。'}}catch(e){if(lookupContext===context)$('dictionary-message').textContent=e.message}finally{if(lookupContext===context)$('explain-word').disabled=false}});
$('save-word').addEventListener('click',async()=>{if(!lookupContext?.word)return;const context=lookupContext;$('save-word').disabled=true;try{await api('/vocabulary',{word:context.word,sentence:context.sentence,job_id:context.job_id,start:context.start,explanation:context.explanation});if(lookupContext===context)$('dictionary-message').textContent='已收藏到本地生词本。'}catch(e){if(lookupContext===context)$('dictionary-message').textContent=e.message}finally{if(lookupContext===context)$('save-word').disabled=false}});
function renderVocabulary(){
  const query=$('vocabulary-search').value.toLowerCase();const words=vocabularyWords.filter(w=>(w.word+' '+w.sentence+' '+w.explanation+' '+(w.dictionary?.entries?.[0]?.chinese||'')).toLowerCase().includes(query));
  $('vocabulary-list').innerHTML=words.length?words.map(w=>`<article class="vocabulary-card"><div><button class="saved-word" data-saved-word="${w.id}">${escapeHTML(w.word)}</button><button class="quiet danger" data-remove-word="${w.id}">移除收藏</button></div><p>${escapeHTML(w.dictionary?.entries?.[0]?.chinese||w.explanation||w.dictionary?.entries?.[0]?.definitions?.[0]?.definition||'点击单词查看释义')}</p>${w.sentence?`<blockquote>${escapeHTML(w.sentence)}</blockquote>`:''}${w.job_id?`<button class="quiet" data-word-video="${w.id}">回到视频 ${clock(w.start)}</button>`:w.video_deleted?'<small>原视频已删除，词语和例句仍保留。</small>':''}</article>`).join(''):'<p class="field-hint">暂无匹配的收藏。在字幕稿里点击英文单词，再点击“收藏到生词本”。</p>';
}
async function showVocabulary(){vocabularyWords=(await api('/vocabulary',undefined,'GET')).words;renderVocabulary();if(!$('vocabulary-dialog').open)$('vocabulary-dialog').showModal()}
$('open-vocabulary').addEventListener('click',()=>{$('video').pause();showVocabulary().catch(e=>toast(e.message))});
$('vocabulary-search').addEventListener('input',renderVocabulary);
$('vocabulary-list').addEventListener('click',async event=>{const button=event.target.closest('button');if(!button)return;try{
  if(button.dataset.removeWord){await api('/vocabulary/'+button.dataset.removeWord,undefined,'DELETE');await showVocabulary()}
  if(button.dataset.savedWord){const saved=vocabularyWords.find(w=>w.id===button.dataset.savedWord);lookupSerial++;$('vocabulary-dialog').close();presentLookup({...saved});renderDictionary(saved.dictionary)}
  if(button.dataset.wordVideo){const saved=vocabularyWords.find(w=>w.id===button.dataset.wordVideo);$('vocabulary-dialog').close();await selectJob(saved.job_id);const video=$('video');if(video.readyState>=1)video.currentTime=saved.start;else video.addEventListener('loadedmetadata',()=>{video.currentTime=saved.start},{once:true});}
}catch(e){toast(e.message)}});
refresh();setInterval(refresh,3000);

$('recheck-speech').addEventListener('click',async()=>{const j=current();if(!j)return;try{await api(`/jobs/${j.id}/recheck-speech`);toast('已备份，正在更新原文字幕；已有译文保留，新增部分可继续翻译。');await refresh()}catch(e){toast(e.message)}});
