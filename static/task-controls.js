'use strict';
function taskAction(job){
  if(['queued','processing'].includes(job.status)||job.resume_after_stop)return {action:'pause',label:job.run_mode==='download'||(!job.downloaded&&!job.playable)?'暂停下载':'暂停处理'};
  if(job.status==='uploading')return {action:'',label:'正在导入'};
  if(job.status==='completed'||['needs_translation','needs_config'].includes(job.status))return {action:'',label:'处理完成'};
  return job.downloaded||job.kind==='file'||job.playable?{action:'process',label:'开始处理'}:{action:'download',label:'开始下载'};
}
function libraryActions(job){
  const a=taskAction(job);
  return `<div class="library-task-actions">${a.action?`<button data-task-action="${a.action}" data-task-id="${job.id}" class="library-task-primary">${a.label}</button>`:''}<button data-task-menu="${job.id}" aria-label="${escapeHTML(job.title)}的视频操作" title="重命名、优先处理、删除">•••</button></div>`;
}
async function runTaskAction(action,id){
  if(action==='delete'){await deleteVideos([id]);return;}
  if(action==='rename'){openRename(id);return;}
  if(action==='cover'){openCover(id);return;}
  try{await api(`/jobs/${id}/control`,{action});await refresh();if(action==='priority')toast('已暂停当前任务，优先处理所选视频');}
  catch(error){toast(error.message);}
}
const taskMenu=document.createElement('div');taskMenu.id='video-context-menu';taskMenu.hidden=true;taskMenu.setAttribute('role','menu');document.body.append(taskMenu);
function closeTaskMenu(){taskMenu.hidden=true;}
function showTaskMenu(id,x,y){
  const job=state.jobs.find(j=>j.id===id);if(!job)return;
  const a=taskAction(job),actions=[];
  if(a.action)actions.push([a.action,a.label]);
  if(!['completed','needs_translation','needs_config','uploading'].includes(job.status))actions.push(['priority',job.downloaded||job.kind==='file'?'优先处理这个视频':'优先下载这个视频']);
  if(job.downloaded||job.playable)actions.push(['cover','选择封面']);
  actions.push(['rename','重命名'],['delete','删除视频']);
  taskMenu.innerHTML=actions.map(([action,label])=>`<button role="menuitem" data-task-action="${action}" data-task-id="${id}"${action==='delete'?' class="danger-menu"':''}>${label}</button>`).join('');
  taskMenu.hidden=false;taskMenu.style.left=Math.max(8,Math.min(x,innerWidth-taskMenu.offsetWidth-8))+'px';taskMenu.style.top=Math.max(8,Math.min(y,innerHeight-taskMenu.offsetHeight-8))+'px';taskMenu.querySelector('button').focus();
}
document.addEventListener('contextmenu',event=>{
  const entry=event.target.closest('#library [data-job]');
  const id=entry?.dataset.job||(event.target.closest('#screen')?state.selected:null);
  if(id){event.preventDefault();showTaskMenu(id,event.clientX,event.clientY);}
});
document.addEventListener('click',event=>{
  const action=event.target.closest('[data-task-action]'),menu=event.target.closest('[data-task-menu]');
  if(action){closeTaskMenu();runTaskAction(action.dataset.taskAction,action.dataset.taskId);}
  else if(menu){const box=menu.getBoundingClientRect();showTaskMenu(menu.dataset.taskMenu,box.right,box.top);}
  else if(!event.target.closest('#video-context-menu'))closeTaskMenu();
});
document.addEventListener('keydown',event=>{
  if(event.key==='Escape')closeTaskMenu();
  if(event.key==='ContextMenu'||(event.shiftKey&&event.key==='F10')){
    const entry=event.target.closest('#library [data-job]');if(entry){event.preventDefault();const box=entry.getBoundingClientRect();showTaskMenu(entry.dataset.job,box.right,box.top);}
  }
});
window.addEventListener('resize',closeTaskMenu);document.addEventListener('scroll',closeTaskMenu,true);
const renameDialog=document.createElement('dialog');renameDialog.id='rename-video-dialog';
renameDialog.innerHTML='<form id="rename-video-form"><div class="dialog-heading"><h2>重命名视频</h2></div><label>视频标题<input id="rename-video-title" required maxlength="200" autocomplete="off"></label><p class="field-hint">只修改显示标题，字幕和译文保留。</p><p class="error-text" id="rename-video-error" hidden></p><div class="dialog-footer"><button class="secondary" type="button" id="rename-video-cancel">取消</button><button class="primary" id="rename-video-save">保存标题</button></div></form>';
document.body.append(renameDialog);
let renameId=null;
function openRename(id){const job=state.jobs.find(j=>j.id===id);if(!job)return;renameId=id;$('rename-video-title').value=job.title;$('rename-video-error').hidden=true;renameDialog.showModal();$('rename-video-title').select();}
$('rename-video-cancel').onclick=()=>renameDialog.close();
$('rename-video-form').onsubmit=async event=>{
  event.preventDefault();$('rename-video-save').disabled=true;
  try{await api(`/jobs/${renameId}/rename`,{title:$('rename-video-title').value});renameDialog.close();await refresh();toast('标题已修改');}
  catch(error){$('rename-video-error').textContent=error.message;$('rename-video-error').hidden=false;}
  finally{$('rename-video-save').disabled=false;}
};
