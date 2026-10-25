'use strict';
const coverDialog=document.createElement('dialog');coverDialog.id='cover-dialog';
coverDialog.setAttribute('aria-labelledby','cover-heading');
coverDialog.innerHTML='<div class="dialog-heading"><div><h2 id="cover-heading">选择视频封面</h2><p>从视频里随机抽取 5 张画面，点击一张即可设为封面。</p></div><button class="icon-button" id="cover-close" aria-label="关闭封面选择">×</button></div><p id="cover-status" role="status"></p><div id="cover-grid"></div><div class="dialog-footer"><span class="field-hint">仅在本机抽帧，不使用 AI 额度</span><button class="secondary" id="cover-shuffle">换一批</button></div>';
document.body.append(coverDialog);
let coverId=null,coverBatch=null,coverRequest=0,coverBusy=false;
function openCover(id){coverId=id;coverBatch=null;coverDialog.showModal();loadCovers();}
function coverLoading(busy,message){coverBusy=busy;$('cover-shuffle').disabled=busy;$('cover-grid').querySelectorAll('button').forEach(b=>b.disabled=busy);$('cover-status').textContent=message;$('cover-status').classList.toggle('loading',busy);}
async function loadCovers(){
  const request=++coverRequest,id=coverId;$('cover-grid').replaceChildren();coverBatch=null;coverLoading(true,'正在随机抽取 5 张画面…');
  try{
    const batch=await api(`/jobs/${id}/cover-candidates`,{});
    if(request!==coverRequest||!coverDialog.open)return;
    coverBatch=batch;
    $('cover-grid').innerHTML=batch.frames.map(f=>`<button class="cover-option" data-cover-index="${f.index}" aria-label="使用 ${clock(f.time)} 的画面作为封面"><img src="${escapeHTML(f.url)}" alt="候选封面 ${f.index+1}"><span>${clock(f.time)}</span></button>`).join('');
    coverLoading(false,'选择喜欢的画面；不满意可以换一批。');
  }catch(error){if(request===coverRequest&&coverDialog.open)coverLoading(false,error.message);}
}
$('cover-shuffle').onclick=loadCovers;
$('cover-close').onclick=()=>coverDialog.close();
coverDialog.addEventListener('close',()=>{++coverRequest;coverId=null;coverBatch=null;});
$('cover-grid').onclick=async event=>{
  const button=event.target.closest('[data-cover-index]');if(!button||coverBusy||!coverBatch)return;
  const id=coverId,request=coverRequest;coverLoading(true,'正在保存封面…');
  try{
    await api(`/jobs/${id}/cover`,{generation:coverBatch.generation,index:Number(button.dataset.coverIndex)});
    if(request===coverRequest)coverDialog.close();await refresh();toast('封面已更新');
  }catch(error){if(request===coverRequest&&coverDialog.open)coverLoading(false,error.message);}
};
