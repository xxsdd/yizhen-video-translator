'use strict';
(() => {
  const dialog=document.createElement('dialog');dialog.id='subtitle-edit-dialog';
  dialog.innerHTML=`<form id="subtitle-edit-form"><div class="dialog-heading"><div><h2>编辑字幕</h2><p id="subtitle-edit-time"></p></div><button type="button" class="icon-button" aria-label="关闭编辑">×</button></div><p id="subtitle-edit-split" class="field-hint" hidden>这句被拆成多行显示；这里修改完整句子，保存后会重新排版。</p><label>原文<textarea id="subtitle-edit-source" rows="3" maxlength="5000" required></textarea></label><label>译文<textarea id="subtitle-edit-translation" rows="3" maxlength="5000"></textarea></label><p id="subtitle-edit-note" class="field-hint">保存到本机，不调用 AI。</p><p id="subtitle-edit-error" class="error-text" role="alert" hidden></p><div class="dialog-footer"><button type="button" class="secondary" id="subtitle-edit-cancel">取消</button><button class="primary" id="subtitle-edit-save">保存修改</button></div></form>`;
  document.body.append(dialog);
  let editing=null,saving=false,opening=false;
  const close=()=>{if(!saving)dialog.close();};
  dialog.querySelector('.icon-button').onclick=close;$('subtitle-edit-cancel').onclick=close;
  dialog.addEventListener('cancel',e=>{if(saving)e.preventDefault();});
  async function open(id){
    if(opening||saving||dialog.open)return;
    const job=current(),row=state.segments.find(s=>String(s.id)===String(id));if(!job||!row)return;
    opening=true;
    try{
      if(['queued','processing','uploading'].includes(job.status))throw new Error('请先暂停当前处理，再编辑字幕。');
      const sourceId=row.source_id??row.id;
      const data=await api(`/jobs/${job.id}/subtitles/${sourceId}`,undefined,'GET');
      if(state.selected!==job.id)return;
      editing={jobId:job.id,sourceId,...data};$('video').pause();
      $('subtitle-edit-time').textContent=`${clock(data.start)} – ${clock(data.end)}`;
      $('subtitle-edit-source').value=data.text;$('subtitle-edit-translation').value=data.translation;
      $('subtitle-edit-split').hidden=row.source_id===undefined;
      $('subtitle-edit-note').textContent='保存到本机，不调用 AI。';$('subtitle-edit-error').hidden=true;
      dialog.showModal();$('subtitle-edit-source').focus();
    }catch(error){toast(error.message);}finally{opening=false;}
  }
  $('transcript').addEventListener('contextmenu',e=>{const row=e.target.closest('[data-segment]');if(!row)return;e.preventDefault();open(row.dataset.segment);});
  $('transcript').addEventListener('keydown',e=>{if(e.key==='ContextMenu'||(e.shiftKey&&e.key==='F10')){const row=e.target.closest('[data-segment]');if(row){e.preventDefault();open(row.dataset.segment);}}});
  $('transcript').addEventListener('click',e=>{const button=e.target.closest('[data-edit-subtitle]');if(button){e.preventDefault();e.stopPropagation();open(button.dataset.editSubtitle);}});
  $('subtitle-edit-source').addEventListener('input',()=>{
    $('subtitle-edit-note').textContent=$('subtitle-edit-source').value.trim()!==editing?.text?'修改原文后，未修改的对应译文会标为待更新；你点击翻译时才补翻这一句。':'保存到本机，不调用 AI。';
  });
  $('subtitle-edit-form').addEventListener('submit',async e=>{
    e.preventDefault();if(!editing||saving)return;saving=true;$('subtitle-edit-save').disabled=true;$('subtitle-edit-error').hidden=true;
    try{
      const result=await api(`/jobs/${editing.jobId}/subtitles/${editing.sourceId}`,{text:$('subtitle-edit-source').value,translation:$('subtitle-edit-translation').value,revision:editing.revision});
      dialog.close();state.fingerprint='';await refresh();toast(result.needs_translation?'原文已保存，对应译文待更新；其他译文保留':'字幕修改已保存');
    }catch(error){$('subtitle-edit-error').textContent=error.message;$('subtitle-edit-error').hidden=false;}
    finally{saving=false;$('subtitle-edit-save').disabled=false;}
  });
  const preview=document.createElement('img');preview.id='download-poster';preview.alt='视频封面';preview.hidden=true;$('screen-empty').prepend(preview);
})();
