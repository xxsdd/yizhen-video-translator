'use strict';
function seekFiveSeconds(direction){
  const video=document.getElementById('video');
  if(!video||!Number.isFinite(video.duration)||video.readyState<1)return;
  video.currentTime=Math.max(0,Math.min(video.duration,video.currentTime+direction*5));
}
document.addEventListener('keydown',event=>{
  if(!['ArrowLeft','ArrowRight'].includes(event.key)||event.altKey||event.ctrlKey||event.metaKey||event.shiftKey)return;
  if(event.target?.closest('input,textarea,select,[contenteditable="true"],dialog,#caption-overlay'))return;
  if(document.querySelector('dialog[open]'))return;
  event.preventDefault();event.stopPropagation();
  if(!event.repeat)seekFiveSeconds(event.key==='ArrowRight'?1:-1);
},true);
if('mediaSession' in navigator){
  try{
    navigator.mediaSession.setActionHandler('seekbackward',()=>seekFiveSeconds(-1));
    navigator.mediaSession.setActionHandler('seekforward',()=>seekFiveSeconds(1));
  }catch{/* Older browsers may not implement these actions. */}
}
