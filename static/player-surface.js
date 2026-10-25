'use strict';

// Controls and captions share the fullscreen wrapper; the browser's second
// subtitle menu is deliberately absent from this player.
let playbackHideTimer,playbackScrubbing=false;
function showPlaybackControls(){
  $('screen').classList.remove('controls-idle');clearTimeout(playbackHideTimer);
  if(!$('video').paused)playbackHideTimer=setTimeout(()=>{
    if(!$('video').paused&&!$('playback-controls').contains(document.activeElement))$('screen').classList.add('controls-idle');
  },2500);
}
function playerClock(seconds){
  seconds=Math.max(0,Math.floor(Number(seconds)||0));
  const h=Math.floor(seconds/3600),m=Math.floor(seconds/60)%60,s=seconds%60;
  return (h?h+':'+String(m).padStart(2,'0'):m)+':'+String(s).padStart(2,'0');
}
function syncPlaybackControls(){
  const video=$('video'),duration=Number.isFinite(video.duration)?video.duration:0;
  $('playback-controls').hidden=!current()?.playable;
  $('playback-toggle').textContent=video.paused?'▶':'Ⅱ';$('playback-toggle').setAttribute('aria-label',video.paused?'播放':'暂停');
  $('playback-time').textContent=playerClock(video.currentTime)+' / '+playerClock(duration);
  const percent=duration?bound(video.currentTime/duration*100,0,100):0,seek=$('playback-seek');
  if(!playbackScrubbing)seek.value=percent;
  seek.disabled=!duration;seek.style.setProperty('--played',percent+'%');seek.setAttribute('aria-valuetext',playerClock(video.currentTime)+' / '+playerClock(duration));
  $('playback-volume').value=video.muted?0:video.volume;
  const muted=video.muted||video.volume===0;
  $('playback-mute').setAttribute('aria-label',muted?'取消静音':'静音');$('playback-mute').title=muted?'取消静音':'静音';
  $('playback-mute').setAttribute('aria-pressed',String(muted));
  $('playback-mute').innerHTML=muted?'<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M11 5 6 9H3v6h3l5 4zM16 9l5 6m0-6-5 6"/></svg>':'<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M11 5 6 9H3v6h3l5 4zM16 8q5 4 0 8M19 5q8 7 0 14"/></svg>';
  $('speed').value=String(video.playbackRate);
}
async function togglePlayback(){
  const video=$('video');if(!current()?.playable)return;
  try{if(video.paused)await video.play();else video.pause()}catch(error){toast('暂时无法播放：'+error.message)}
  showPlaybackControls();
}
$('playback-toggle').addEventListener('click',togglePlayback);
$('video').addEventListener('click',togglePlayback);
$('playback-seek').addEventListener('pointerdown',()=>{playbackScrubbing=true});
for(const name of ['pointerup','pointercancel'])document.addEventListener(name,()=>{if(playbackScrubbing){playbackScrubbing=false;syncPlaybackControls()}});
$('playback-seek').addEventListener('input',event=>{const video=$('video');if(Number.isFinite(video.duration))video.currentTime=Number(event.target.value)/100*video.duration;showPlaybackControls();syncPlaybackControls()});
$('playback-seek').addEventListener('change',()=>{const video=$('video');if(Number.isFinite(video.duration))$('playback-seek').value=video.currentTime/video.duration*100});
$('playback-mute').addEventListener('click',()=>{const video=$('video');if(video.muted||video.volume===0){video.muted=false;if(!video.volume)video.volume=1}else video.muted=true});
$('playback-volume').addEventListener('input',event=>{const video=$('video');video.volume=Number(event.target.value);video.muted=false});
for(const name of ['loadedmetadata','durationchange','timeupdate','play','pause','ended','volumechange','ratechange','emptied'])$('video').addEventListener(name,()=>{syncPlaybackControls();if(['play','pause','ended'].includes(name))showPlaybackControls()});
for(const name of ['pointermove','pointerdown','focusin'])$('screen').addEventListener(name,showPlaybackControls);
$('screen').addEventListener('focusout',showPlaybackControls);
$('screen').addEventListener('keydown',event=>{if(event.code==='Space'&&event.target===$('video')){event.preventDefault();togglePlayback()}});
document.addEventListener('fullscreenchange',()=>{
  const full=!!document.fullscreenElement,button=$('player-fullscreen');
  button.setAttribute('aria-label',full?'退出全屏':'全屏播放');button.title=full?'退出全屏':'全屏播放';button.textContent=full?'⤡':'⛶';
  syncCaptionTracks();renderCurrentCaption();showPlaybackControls();
});
$('video').textTracks.addEventListener('change',syncCaptionTracks);
syncPlaybackControls();
