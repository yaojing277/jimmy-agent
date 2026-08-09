/**
 * trade_entry_appscript.gs — 「股票買賣紀錄」買進輸入 後端橋接
 * ───────────────────────────────────────────────────────────
 * 搭配 trade_entry.html(純前端)使用。前端用 fetch 呼叫本 Web App,
 * 由本腳本以「試算表擁有者」身分寫入,前端不需任何金鑰或 OAuth。
 *
 * 【一次性部署步驟】
 * 1. 開啟「股價試算」試算表 → 上方選單「擴充功能 → Apps Script」。
 * 2. 把本檔全部內容貼進 Code.gs(覆蓋原本內容),存檔。
 * 3. 右上「部署 → 新增部署作業 → 類型選『網頁應用程式』」。
 *      - 執行身分:我(你自己)
 *      - 具有存取權者:「任何人」(才能讓前端 fetch;URL 不外流即可)
 * 4. 按「部署」,授權後複製產生的「網頁應用程式 URL」。
 * 5. 把該 URL 貼到 trade_entry.html 最上方的 WEB_APP_URL。
 * 6. (選用)把下方 SECRET 改成自訂字串,並在 html 同步改一致。
 *
 * 改版後要重新「部署 → 管理部署作業 → 編輯 → 版本選『新版本』」才會生效。
 */

const SHEET_NAME = '股票買賣紀錄';
const HEADER_ROW = 1;
const SECRET     = 'pE26Cw3td4dfESIkKk9o5ptbngxv8WjK';   // 須與 html 一致;改這裡記得同步改 html 並重新部署

// 中文/英文名 -> 台股代號(查當日收盤參考用)
const ALIAS = {
  '鴻海': '2317', '穎崴': '6515', '台達電': '2308',
  '聯發科': '2454', '國巨': '2327', '台積電': '2330'
};

function doPost(e) {
  try {
    const p = JSON.parse(e.postData.contents);
    if (p.secret !== SECRET) return json({ error: '未授權(secret 不符)' });
    if (p.action === 'add')   return json(addRow(p));
    if (p.action === 'close') return json({ close: closeOn(p.code, p.date) });
    return json({ error: '未知的 action' });
  } catch (err) {
    return json({ error: String(err) });
  }
}

function doGet() {
  return json({ ok: true, msg: 'trade entry endpoint alive' });
}

function json(o) {
  return ContentService.createTextOutput(JSON.stringify(o))
                       .setMimeType(ContentService.MimeType.JSON);
}

function sheet() {
  return SpreadsheetApp.getActiveSpreadsheet().getSheetByName(SHEET_NAME);
}

// 與前端一致:成交金額 / 手續費(費率% × 折數,無條件捨去,無最低) / 買進成本
function compute(price, shares, feeRatePct, disc) {
  const amount = price * shares;
  const fee    = Math.floor(amount * feeRatePct / 100 * disc);
  const cost   = Math.round(amount + fee);
  return { amount: amount, fee: fee, cost: cost };
}

// 標頭下方第一個 A 欄有值的列(=目前最新一筆);沒有則回 HEADER_ROW+1
function firstDataRow(sh) {
  const last = sh.getLastRow();
  if (last < HEADER_ROW + 1) return HEADER_ROW + 1;
  const vals = sh.getRange(HEADER_ROW + 1, 1, last - HEADER_ROW, 1).getValues();
  for (let k = 0; k < vals.length; k++) {
    if (String(vals[k][0]).trim() !== '') return HEADER_ROW + 1 + k;
  }
  return HEADER_ROW + 1;
}

function addRow(p) {
  const sh = sheet();
  if (!sh) throw new Error('找不到分頁「' + SHEET_NAME + '」');
  const price  = parseFloat(p.price);
  const shares = parseInt(p.shares, 10);
  const disc   = (p.disc != null && String(p.disc) !== '') ? parseFloat(p.disc) : 1;
  const c = compute(price, shares, parseFloat(p.fee_rate), disc);
  const r = firstDataRow(sh);
  sh.insertRowBefore(r);                       // 在最新一筆上方插入新列

  const code = String(p.code).trim();
  const aVal = /^\d+$/.test(code) ? "'" + code : code;   // 純數字代號存成文字保留前導零
  const dt   = new Date(p.date + 'T00:00:00');           // 買進日

  sh.getRange(r, 1).setValue(aVal);                                   // A 代號
  sh.getRange(r, 2).setFormula('=TODAY()-F' + r);                     // B 幾天前
  sh.getRange(r, 3).setFormula('=AVERAGE(FILTER(G' + r + ':G, A' + r + ':A=A' + r + ', F' + r + ':F<=(TODAY()-B' + r + ')))'); // C 至今均價
  sh.getRange(r, 4).setFormula('=AVERAGE(FILTER(G' + r + ':G, A' + r + ':A=A' + r + ', F' + r + ':F>=(TODAY()-180)))');        // D 半年均價
  sh.getRange(r, 5).setFormula('=AVERAGE(FILTER(G' + r + ':G, A' + r + ':A=A' + r + ', F' + r + ':F>=(TODAY()-365)))');        // E 一年均價
  sh.getRange(r, 6).setValue(dt);          // F 買進日
  sh.getRange(r, 7).setValue(price);       // G 買進價
  sh.getRange(r, 8).setValue(shares);      // H 買進股數
  sh.getRange(r, 9).setValue(c.fee);       // I 手續費
  sh.getRange(r, 10).setValue(c.cost);     // J 買進成本

  return { row: r, amount: c.amount, fee: c.fee, cost: c.cost };
}

// ── 收盤價:TWSE(上市)為主、TPEx(上櫃)補;不使用 Yahoo;美股回 null ──

// 民國日期 -> UTC 毫秒時間戳;支援 '115/06/12' 與 '115年06月12日';失敗回 null
function rocToUtc(s) {
  s = String(s).trim().replace(/年|月/g, '/').replace(/日/g, '');
  const p = s.split('/').filter(function (x) { return x !== ''; });
  if (p.length !== 3) return null;
  const y = parseInt(p[0], 10), m = parseInt(p[1], 10), d = parseInt(p[2], 10);
  if (isNaN(y) || isNaN(m) || isNaN(d)) return null;
  return Date.UTC(y + 1911, m - 1, d);
}

// '2,355.00' -> 2355;'-'、''、'--'、'N/A' -> null
function toNum(x) {
  const s = String(x).replace(/,/g, '').trim();
  if (s === '' || s === '-' || s === '--' || s === 'N/A') return null;
  const v = parseFloat(s);
  return isNaN(v) ? null : v;
}

// A欄值/代號 -> 純台股代號(去前導 ' 與 .TW/.TWO);非台股(美股等)回 null
function normCode(a) {
  let v = String(a).trim().replace(/^'/, '').toUpperCase().replace(/\.TWO$/, '').replace(/\.TW$/, '');
  return /^[0-9][0-9A-Z]*$/.test(v) ? v : null;
}

// 抓某月每日收盤 {utcMillis: close};source: 'twse' | 'tpex'
function monthCloses(code, y, m, source) {
  const mm = ('0' + m).slice(-2);
  const url = source === 'twse'
    ? 'https://www.twse.com.tw/rwd/zh/afterTrading/STOCK_DAY?date=' + y + mm + '01&stockNo=' + code + '&response=json'
    : 'https://www.tpex.org.tw/www/zh-tw/afterTrading/tradingStock?code=' + code + '&date=' + y + '/' + mm + '/01&response=json';
  const out = {};
  try {
    const d = JSON.parse(UrlFetchApp.fetch(url, { muteHttpExceptions: true, headers: { 'User-Agent': 'Mozilla/5.0' } }).getContentText());
    let rows;
    if (source === 'twse') {
      if (d.stat !== 'OK') return out;
      rows = d.data || [];
    } else {
      rows = (d.tables && d.tables[0] && d.tables[0].data) || [];
    }
    for (let i = 0; i < rows.length; i++) {
      const t = rocToUtc(rows[i][0]), px = toNum(rows[i][6]);
      if (t != null && px) out[t] = px;     // px 為 0 視同無效收盤
    }
  } catch (err) {}
  return out;
}

// 指定日收盤(當日無交易取 ±4 天最近);供前端對照
function closeOn(code, dateStr) {
  const c = normCode(ALIAS[code] || code);
  if (!c) return null;                       // 美股/非台股
  const want = new Date(dateStr + 'T00:00:00');
  const y = want.getFullYear(), m = want.getMonth() + 1;
  const wantUTC = Date.UTC(y, want.getMonth(), want.getDate());

  let src = 'twse', map = monthCloses(c, y, m, 'twse');
  if (Object.keys(map).length === 0) { src = 'tpex'; map = monthCloses(c, y, m, 'tpex'); }
  if (Object.keys(map).length === 0) return null;
  if (map[wantUTC] != null) return Math.round(map[wantUTC] * 100) / 100;

  // 當日非交易日 -> 補同來源鄰月再找 ±4 天最近
  const adj = [m === 1 ? [y - 1, 12] : [y, m - 1], m === 12 ? [y + 1, 1] : [y, m + 1]];
  for (let i = 0; i < adj.length; i++) {
    const mm = monthCloses(c, adj[i][0], adj[i][1], src);
    for (const k in mm) map[k] = mm[k];
  }
  let best = null, bestDiff = Infinity;
  for (const k in map) {
    const diff = Math.abs(Number(k) - wantUTC);
    if (diff <= 4 * 86400000 && diff < bestDiff) { bestDiff = diff; best = map[k]; }
  }
  return best != null ? Math.round(best * 100) / 100 : null;
}
