const MARKER_ID = '__audio_highlight_marker__';
const TITLE_PREFIX = '🔊 ';

let playingCount = 0;
let removeTimer = null;
let guardInterval = null;
let cachedFaviconUrl = null;

function buildAudioFaviconUrl() {
  if (cachedFaviconUrl) return cachedFaviconUrl;
  const canvas = document.createElement('canvas');
  canvas.width = 32;
  canvas.height = 32;
  const ctx = canvas.getContext('2d');

  ctx.fillStyle = '#1A73E8';
  ctx.beginPath();
  ctx.arc(16, 16, 16, 0, Math.PI * 2);
  ctx.fill();

  ctx.fillStyle = '#FFFFFF';
  ctx.fillRect(7, 11, 7, 10);

  ctx.beginPath();
  ctx.moveTo(14, 11);
  ctx.lineTo(23, 5);
  ctx.lineTo(23, 27);
  ctx.lineTo(14, 21);
  ctx.closePath();
  ctx.fill();

  ctx.strokeStyle = '#FFFFFF';
  ctx.lineWidth = 2;
  ctx.lineCap = 'round';
  ctx.beginPath();
  ctx.arc(23, 16, 3.5, -Math.PI / 2.5, Math.PI / 2.5);
  ctx.stroke();
  ctx.beginPath();
  ctx.arc(23, 16, 6.5, -Math.PI / 2.5, Math.PI / 2.5);
  ctx.stroke();

  cachedFaviconUrl = canvas.toDataURL('image/png');
  return cachedFaviconUrl;
}

// 每 500ms 檢查一次 favicon/title 是否被頁面覆蓋，若是則補回
function startGuard() {
  if (guardInterval) return;
  guardInterval = setInterval(() => {
    if (!document.getElementById(MARKER_ID)) return;

    const url = buildAudioFaviconUrl();
    let link = document.querySelector('link[rel~="icon"]');
    if (!link) {
      link = document.createElement('link');
      link.rel = 'icon';
      document.head.appendChild(link);
    }
    if (link.href !== url) link.href = url;

    if (!document.title.startsWith(TITLE_PREFIX)) {
      document.title = TITLE_PREFIX + document.title;
    }
  }, 500);
}

function stopGuard() {
  clearInterval(guardInterval);
  guardInterval = null;
}

function applyHighlight() {
  if (document.getElementById(MARKER_ID)) return;

  const originalLink = document.querySelector('link[rel~="icon"]');
  const originalHref = originalLink ? originalLink.href : '';
  const originalTitle = document.title;

  const marker = document.createElement('meta');
  marker.id = MARKER_ID;
  marker.setAttribute('data-original-href', originalHref);
  marker.setAttribute('data-original-title', originalTitle);
  document.head.appendChild(marker);

  if (!document.title.startsWith(TITLE_PREFIX)) {
    document.title = TITLE_PREFIX + document.title;
  }

  let link = document.querySelector('link[rel~="icon"]');
  if (!link) {
    link = document.createElement('link');
    link.rel = 'icon';
    document.head.appendChild(link);
  }
  link.href = buildAudioFaviconUrl();

  startGuard();
}

function removeHighlight() {
  stopGuard();
  const marker = document.getElementById(MARKER_ID);
  if (!marker) return;

  const originalHref = marker.getAttribute('data-original-href');
  const originalTitle = marker.getAttribute('data-original-title');

  if (originalTitle !== null) document.title = originalTitle;

  const link = document.querySelector('link[rel~="icon"]');
  if (link) {
    if (originalHref) {
      link.href = originalHref;
    } else {
      link.remove();
    }
  }

  marker.remove();
}

function scheduleRemove() {
  clearTimeout(removeTimer);
  removeTimer = setTimeout(() => {
    if (playingCount === 0) removeHighlight();
  }, 2000);
}

function attachToMedia(el) {
  if (el._audioHighlightBound) return;
  el._audioHighlightBound = true;

  el.addEventListener('play', () => {
    clearTimeout(removeTimer);
    playingCount++;
    applyHighlight();
  });

  el.addEventListener('pause', () => {
    playingCount = Math.max(0, playingCount - 1);
    if (playingCount === 0) scheduleRemove();
  });

  el.addEventListener('ended', () => {
    playingCount = Math.max(0, playingCount - 1);
    if (playingCount === 0) scheduleRemove();
  });

  if (!el.paused && !el.ended) {
    playingCount++;
    applyHighlight();
  }
}

function scanMediaElements() {
  document.querySelectorAll('audio, video').forEach(attachToMedia);
}

const observer = new MutationObserver((mutations) => {
  mutations.forEach((mutation) => {
    mutation.addedNodes.forEach((node) => {
      if (node.nodeType !== 1) return;
      if (node.matches('audio, video')) attachToMedia(node);
      node.querySelectorAll && node.querySelectorAll('audio, video').forEach(attachToMedia);
    });
  });
});

observer.observe(document.documentElement, { childList: true, subtree: true });

scanMediaElements();
setTimeout(scanMediaElements, 2000);
