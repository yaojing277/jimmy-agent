// ===================================================
// Audio Tab Highlighter - background.js
// 偵測正在播放聲音的分頁，將 Favicon 換成藍色喇叭
// 並在標題前加上 🔊 符號，讓側邊欄一眼可辨識
// ===================================================

chrome.tabs.onUpdated.addListener((tabId, changeInfo, tab) => {
  if (changeInfo.audible === true) {
    injectHighlight(tabId, true);
  } else if (changeInfo.audible === false) {
    injectHighlight(tabId, false);
  }
});

chrome.runtime.onStartup.addListener(scanAllTabs);
chrome.runtime.onInstalled.addListener(scanAllTabs);

function scanAllTabs() {
  chrome.tabs.query({ audible: true }, (tabs) => {
    tabs.forEach((tab) => injectHighlight(tab.id, true));
  });
}

function injectHighlight(tabId, isPlaying) {
  chrome.scripting.executeScript({
    target: { tabId },
    func: isPlaying ? applyAudioHighlight : removeAudioHighlight,
  }).catch(() => {});
}

// -------------------------------------------------------
// 注入頁面執行：套用藍色 Favicon + 標題前綴
// -------------------------------------------------------

function applyAudioHighlight() {
  const MARKER_ID = '__audio_highlight_marker__';
  const TITLE_PREFIX = '🔊 ';

  if (document.getElementById(MARKER_ID)) return;

  // 儲存原始狀態
  const originalLink = document.querySelector('link[rel~="icon"]');
  const originalHref = originalLink ? originalLink.href : '';
  const originalTitle = document.title;

  const marker = document.createElement('meta');
  marker.id = MARKER_ID;
  marker.setAttribute('data-original-href', originalHref);
  marker.setAttribute('data-original-title', originalTitle);
  document.head.appendChild(marker);

  // ── 1. 修改標題（側邊欄最明顯的效果）──
  if (!document.title.startsWith(TITLE_PREFIX)) {
    document.title = TITLE_PREFIX + document.title;
  }

  // ── 2. 畫藍色喇叭 Favicon ──
  const canvas = document.createElement('canvas');
  canvas.width = 32;
  canvas.height = 32;
  const ctx = canvas.getContext('2d');

  // 藍色圓形背景
  ctx.fillStyle = '#1A73E8';
  ctx.beginPath();
  ctx.arc(16, 16, 16, 0, Math.PI * 2);
  ctx.fill();

  // 喇叭本體（白色矩形）
  ctx.fillStyle = '#FFFFFF';
  ctx.fillRect(7, 11, 7, 10);

  // 喇叭擴音錐
  ctx.beginPath();
  ctx.moveTo(14, 11);
  ctx.lineTo(23, 5);
  ctx.lineTo(23, 27);
  ctx.lineTo(14, 21);
  ctx.closePath();
  ctx.fill();

  // 音波弧線
  ctx.strokeStyle = '#FFFFFF';
  ctx.lineWidth = 2;
  ctx.lineCap = 'round';
  ctx.beginPath();
  ctx.arc(23, 16, 3.5, -Math.PI / 2.5, Math.PI / 2.5);
  ctx.stroke();
  ctx.beginPath();
  ctx.arc(23, 16, 6.5, -Math.PI / 2.5, Math.PI / 2.5);
  ctx.stroke();

  // 套用 Favicon
  let link = document.querySelector('link[rel~="icon"]');
  if (!link) {
    link = document.createElement('link');
    link.rel = 'icon';
    document.head.appendChild(link);
  }
  link.href = canvas.toDataURL('image/png');
}

// -------------------------------------------------------
// 注入頁面執行：還原 Favicon + 標題
// -------------------------------------------------------

function removeAudioHighlight() {
  const MARKER_ID = '__audio_highlight_marker__';
  const marker = document.getElementById(MARKER_ID);
  if (!marker) return;

  const originalHref = marker.getAttribute('data-original-href');
  const originalTitle = marker.getAttribute('data-original-title');

  // 還原標題
  if (originalTitle !== null) {
    document.title = originalTitle;
  }

  // 還原 Favicon
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
