'use strict';

// The existing worker maps transfer bytes to 3–17 and scanned audio to 28–69.
// These are per-stage completion estimates, never estimates of time remaining.
function localStageState(job){
  const p=Number(job.progress)||0;
  const running=job.status==='processing';
  const stopped={paused:'已暂停',failed:'已中断',cancelled:'已取消'}[job.status];
  const asrDone=p>=70||['needs_translation','needs_config','completed'].includes(job.status);
  const mediaDone=p>=19||job.playable||asrDone;
  const local=job.kind==='file';
  const downloadDone=mediaDone||(local&&job.status!=='uploading');
  const downloading=!local&&!downloadDone&&p>=3;
  const downloadValue=downloadDone?100:downloading&&p>3?Math.min(100,Math.max(0,(p-3)/14*100)):0;
  const scanning=!asrDone&&p>=28;
  const scanValue=asrDone?100:scanning?Math.min(99,Math.max(0,(p-28)/41*100)):0;
  const scanTime=job.duration>0?`约扫描至 ${clock(job.duration*scanValue/100)} / ${clock(job.duration)}`:'按音频扫描位置估算';
  const conversionDone=p>=24||job.playable||asrDone;
  const converting=!conversionDone&&p>=19;
  const conversionValue=conversionDone?100:converting?Math.min(99,Math.max(0,(p-19)/5*100)):0;
  return {
    download:{
      label:local?'导入视频':'下载视频', value:downloadValue,
      unknown:running&&!downloadDone&&(!downloading||p<=3),
      status:downloadDone?'100% · 已完成':stopped||(downloading&&p>3?`约 ${Math.round(downloadValue)}%`:running?'读取视频信息…':'等待开始'),
      note:downloadDone?(local?'本地文件已导入':'视频获取完成'):downloading&&p>3?'按当前媒体流计算；音视频分开下载时会分段显示。':'视频源提供文件大小后显示百分比。'
    },
    conversion:{
      label:'视频格式转换', value:conversionValue,
      unknown:running&&converting&&p===19,
      status:conversionDone?'100% · 已完成':converting?(stopped|| (p>19?`${Math.floor(conversionValue+1e-6)}%`:'转换中…')):'等待开始',
      note:conversionDone?(running&&p<27?'视频已准备好，正在准备识别音频。':'已准备好可播放的视频。'):converting?(p>19?job.message:'正在准备可播放的视频；等待转换进度。旧任务需下次重启后才能报告百分比。'):'下载或导入完成后开始；百分比按已处理的视频时长计算。'
    },
    recognition:{
      label:'识别对白', value:scanValue,
      unknown:running&&!asrDone&&p>=27&&p<28,
      status:asrDone?'100% · 已完成':scanning?(stopped?`${stopped} · 约 ${Math.round(scanValue)}%`:`约 ${Math.round(scanValue)}%`):running&&p>=27?'加载识别模型…':'等待开始',
      note:asrDone?`已识别 ${Number(job.segment_count||0).toLocaleString()} 段对白`:scanning?`${scanTime} · 已识别 ${Number(job.segment_count||0).toLocaleString()} 段`:mediaDone?'视频准备完成后开始本地识别。':'获取视频后开始本地识别。'
    }
  };
}

function renderLocalProgress(job){
  const box=$('local-stage-progress');
  if(!box)return;
  box.hidden=!job;
  if(!job)return;
  for(const [key,stage] of Object.entries(localStageState(job))){
    $(`${key}-stage-label`).textContent=stage.label;
    $(`${key}-stage-status`).textContent=stage.status;
    $(`${key}-stage-note`).textContent=stage.note;
    const meter=$(`${key}-stage-meter`);
    meter.setAttribute('aria-label',stage.label+'进度');
    if(stage.unknown)meter.removeAttribute('value');
    else meter.value=stage.value;
  }
}
