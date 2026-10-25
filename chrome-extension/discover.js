/* Runs only on the tab whose extension button the user clicked. No cookies,
   history, page scripts, credentials, or continuous browsing monitor. */
(() => {
  const httpURL = value => {
    if (typeof value !== 'string' || !value.trim()) return '';
    try { const u = new URL(value, location.href); return /^https?:$/.test(u.protocol) && !u.username && !u.password ? u.href : ''; }
    catch { return ''; }
  };
  const meta = name => document.querySelector(`meta[property="${name}"],meta[name="${name}"]`)?.content || '';
  const pageTitle = meta('og:title') || document.title || '网页视频';
  const pagePoster = httpURL(meta('og:image'));
  const videos = [...document.querySelectorAll('video')];
  const candidates = new Map();
  const add = (url, title, thumbnail, duration, kind='media') => {
    url = httpURL(url); if (!url) return;
    // Keep signed query parameters intact; only identical URLs are deduped.
    if (!candidates.has(url)) candidates.set(url, {url, title: String(title || pageTitle).slice(0,500),
      thumbnail: thumbnail ? httpURL(thumbnail) : '', duration: Number.isFinite(duration) ? duration : null, kind});
  };
  const knownPage = /(^|\.)(youtube\.com|youtu\.be|bilibili\.com|b23\.tv)$/.test(location.hostname)
    && (/\/watch|\/shorts\/|\/video\/|\/embed\//.test(location.pathname) || /^(youtu\.be|b23\.tv)$/.test(location.hostname));
  if (knownPage) {
    add(location.href, pageTitle, pagePoster, videos[0]?.duration, 'page');
  } else {
    videos.forEach((v, index) => {
      const box = v.closest('figure,article,[data-video]');
      const title = v.getAttribute('aria-label') || box?.querySelector('figcaption,h2,h3')?.textContent
        || (videos.length > 1 ? `${pageTitle} · 视频 ${index+1}` : pageTitle);
      const source = [v.currentSrc, v.src, ...[...v.querySelectorAll('source')].map(s=>s.src)].find(s=>s && /^https?:/.test(s));
      if (source) add(source, title, v.poster || pagePoster, v.duration);
    });
    if (!candidates.size) {
      const resources = performance.getEntriesByType('resource').map(r=>r.name)
        .filter(url => /\.(m3u8|mpd|mp4|webm)(?:[?#]|$)/i.test(url));
      // A master manifest contains all quality variants; prefer it when present.
      const masters = resources.filter(url=>/\/(master|playlist)\.m3u8(?:[?#]|$)/i.test(url));
      (masters.length ? masters : resources).forEach((url,i)=>add(url,resources.length>1?`${pageTitle} · 媒体 ${i+1}`:pageTitle,pagePoster,null));
    }
    if (!candidates.size) {
      const visit = object => {
        if (!object || typeof object !== 'object') return;
        if (object['@type'] === 'VideoObject' || (Array.isArray(object['@type']) && object['@type'].includes('VideoObject'))) {
          add(object.contentUrl || object.embedUrl, object.name, Array.isArray(object.thumbnailUrl)?object.thumbnailUrl[0]:object.thumbnailUrl, null);
        }
        Object.values(object).forEach(value=> { if(Array.isArray(value)) value.slice(0,100).forEach(visit); else if(value && typeof value==='object') visit(value); });
      };
      for (const script of document.querySelectorAll('script[type="application/ld+json"]')) {
        if (script.textContent.length > 500000) continue;
        try { visit(JSON.parse(script.textContent)); } catch { /* Not executable page code. */ }
      }
    }
    if (!candidates.size) {
      for (const iframe of document.querySelectorAll('iframe[src]')) {
        if (/youtube\.com\/embed\/|player\.bilibili\.com\//.test(iframe.src)) add(iframe.src,iframe.title||pageTitle,pagePoster,null,'page');
      }
    }
    // A blob URL cannot be downloaded outside its browser. Fall back to the
    // actual playback page and let yt-dlp resolve a supported extractor.
    if (!candidates.size && videos.length) add(location.href,pageTitle,pagePoster,videos[0]?.duration,'page');
  }
  return {page_url: location.href, title: pageTitle, candidates: [...candidates.values()].slice(0,30)};
})();
