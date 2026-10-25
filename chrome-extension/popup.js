'use strict';
let snapshot=null, sourceTab=null, busy=false;
const $=id=>document.getElementById(id);
async function submit(entries){
  if(busy)return;busy=true;$('import').disabled=true;$('retry').hidden=true;
  try{
    $('status').textContent=`正在导入 ${entries.length} 个视频…`;
    const result=await chrome.runtime.sendMessage({type:'import',tabId:sourceTab.id,
      entries:entries.map(({url,title,thumbnail})=>({url,title,thumbnail:thumbnail||'',page_url:snapshot.page_url}))});
    if(result.error)throw new Error(result.error);
    $('status').textContent='已加入视频库，点击开始下载后才会下载。';
  }catch(error){$('status').textContent=error.message;$('retry').hidden=false;}
  finally{busy=false;$('import').disabled=false;}
}
async function scan(){
  $('status').textContent='正在读取当前网页…';$('import').hidden=true;$('retry').hidden=true;$('candidates').replaceChildren();
  try{
    [sourceTab]=await chrome.tabs.query({active:true,currentWindow:true});
    if(!/^https?:\/\//.test(sourceTab?.url||'') || sourceTab.url.startsWith('http://127.0.0.1:8765')) throw new Error('请先打开要导入的视频网页，再点译帧扩展。');
    const result=await chrome.scripting.executeScript({target:{tabId:sourceTab.id},files:['discover.js']});
    snapshot=result[0]?.result;
    if(!snapshot) throw new Error('无法读取此页面，请打开视频的播放页后重试。');
    if(!snapshot.candidates.length){
      $('status').textContent='暂未检测到视频。可以先播放几秒再重新检测，或尝试导入当前网页。';
      snapshot.candidates=[{url:snapshot.page_url,title:snapshot.title,thumbnail:'',kind:'page'}];
      $('import').textContent='尝试导入当前网页';$('import').hidden=false;$('retry').hidden=false;return;
    }
    for(const [index,candidate] of snapshot.candidates.entries()){
      const row=document.createElement('label'),input=document.createElement('input'),text=document.createElement('span');
      input.type='checkbox';input.value=index;input.checked=true;row.append(input);
      if(candidate.thumbnail){const img=document.createElement('img');img.src=candidate.thumbnail;img.referrerPolicy='no-referrer';img.alt='视频封面';img.onerror=()=>img.remove();row.append(img);}
      text.textContent=candidate.title;const sub=document.createElement('small');
      sub.textContent=candidate.duration?`${Math.floor(candidate.duration/60)}:${String(Math.floor(candidate.duration%60)).padStart(2,'0')}`:'时长将由译帧读取';
      text.append(sub);row.append(text);$('candidates').append(row);
    }
    if(snapshot.candidates.length===1){await submit(snapshot.candidates);return;}
    $('status').textContent=`检测到 ${snapshot.candidates.length} 个候选视频，选择需要导入的内容（每次最多 10 个）。`;
    $('import').textContent='导入所选视频';$('import').hidden=false;
  }catch(error){$('status').textContent=error.message;$('retry').hidden=false;}
}
$('import').onclick=()=>{
  const boxes=[...document.querySelectorAll('input:checked')];
  const selected=$('candidates').children.length?boxes.map(b=>snapshot.candidates[Number(b.value)]):snapshot.candidates;
  if(!selected.length||selected.length>10){$('status').textContent='请选择 1–10 个视频。';return;}
  submit(selected);
};
$('retry').onclick=scan;
scan();
