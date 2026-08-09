#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
trade_entry_server.py — 買進紀錄快速輸入網頁

開瀏覽器填「代號 / 買進價格 / 買進股數 / 買進日期」,自動算出
成交金額、元大手續費、買進成本,並插入到「股票買賣紀錄」分頁
最前一筆資料列(動態偵測)。欄位對應:
  A 代號 / B 幾天前(公式) / C~E 均價(公式) / F 買進日 / G 買進價 /
  H 買進股數 / I 手續費 / J 買進成本(=金額+手續費)。

啟動:
  python3 trade_entry_server.py
  然後瀏覽器開 http://localhost:8765

需求:同資料夾 token.json(沿用既有 OAuth);零額外套件(用 Python 內建 http.server)。
"""
import os, sys, json, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import twse_hist          # 收盤價查詢:TWSE/TPEx 官方,不使用 Yahoo
TOKEN_FILE = os.path.join(HERE, "token.json")
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
SPREADSHEET_ID = "1UiqAHT2GUhKiviSz5NaLNclttlLVP3ujQMxJUn7Jyr8"
SHEET_NAME = "股票買賣紀錄"
PORT = 8765
MIN_FEE = 20            # 元大單筆最低手續費(元)
HEADER_ROW = 1

# 中文/英文名 -> 台股代號(查當日收盤參考用)
ALIAS = {"鴻海": "2317", "穎崴": "6515", "台達電": "2308",
         "聯發科": "2454", "國巨": "2327", "台積電": "2330"}

# ---------------- Google Sheets ----------------
def get_service():
    creds = Credentials.from_authorized_user_file(TOKEN_FILE)
    if not creds.valid and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        with open(TOKEN_FILE, "w") as f:
            f.write(creds.to_json())
    return build("sheets", "v4", credentials=creds)

def get_sheet_id(svc):
    meta = svc.spreadsheets().get(spreadsheetId=SPREADSHEET_ID).execute()
    for sh in meta["sheets"]:
        if sh["properties"]["title"] == SHEET_NAME:
            return sh["properties"]["sheetId"]
    raise RuntimeError(f"找不到分頁「{SHEET_NAME}」")

def first_data_row(svc):
    """標頭下方第一個 A 欄有值的列(=目前最新一筆);沒有則回 HEADER_ROW+1。"""
    vals = svc.spreadsheets().values().get(
        spreadsheetId=SPREADSHEET_ID,
        range=f"{SHEET_NAME}!A{HEADER_ROW+1}:A200").execute().get("values", [])
    for k, row in enumerate(vals):
        if row and str(row[0]).strip() != "":
            return HEADER_ROW + 1 + k
    return HEADER_ROW + 1

def serial(date_str):
    """'2026-05-28' -> Google 日期序號。"""
    d = datetime.datetime.strptime(date_str, "%Y-%m-%d").date()
    return (d - datetime.date(1899, 12, 30)).days

# ---------------- 計算 ----------------
def compute(price, shares, fee_rate):
    amount = price * shares
    fee = max(round(amount * fee_rate), MIN_FEE)
    cost = round(amount + fee)
    return amount, fee, cost

def to_code(code):
    """名稱/代號 -> 純台股代號;沿用 ALIAS 名稱對照。"""
    code = str(code).strip()
    return ALIAS.get(code, code.lstrip("'"))

def close_on(code, date_str):
    """指定日收盤(TWSE/TPEx 官方);非台股或查無回 None。"""
    px, _ = twse_hist.close_on(to_code(code), date_str)
    return px

def preview(p):
    price, shares = float(p["price"]), int(p["shares"])
    amount, fee, cost = compute(price, shares, float(p["fee_rate"]) / 100)
    px = close_on(p["code"], p["date"])
    dev = round((price / px - 1) * 100, 2) if px else None
    return {"amount": amount, "fee": fee, "cost": cost, "close": px, "dev": dev}

def add(p):
    price, shares = float(p["price"]), int(p["shares"])
    amount, fee, cost = compute(price, shares, float(p["fee_rate"]) / 100)
    svc = get_service()
    sid = get_sheet_id(svc)
    r = first_data_row(svc)
    # 1) 在 r 上方插入一列
    svc.spreadsheets().batchUpdate(spreadsheetId=SPREADSHEET_ID, body={"requests": [{
        "insertDimension": {"range": {"sheetId": sid, "dimension": "ROWS",
                                       "startIndex": r - 1, "endIndex": r},
                            "inheritFromBefore": False}}]}).execute()
    code = str(p["code"]).strip()
    a_val = ("'" + code) if code.isdigit() else code      # 純數字代號存成文字保留前導零
    data = [
        {"range": f"{SHEET_NAME}!A{r}", "values": [[a_val]]},
        {"range": f"{SHEET_NAME}!B{r}", "values": [[f"=TODAY()-F{r}"]]},
        {"range": f"{SHEET_NAME}!C{r}", "values": [[f"=AVERAGE(FILTER(G{r}:G, A{r}:A=A{r}, F{r}:F<=(TODAY()-B{r})))"]]},
        {"range": f"{SHEET_NAME}!D{r}", "values": [[f"=AVERAGE(FILTER(G{r}:G, A{r}:A=A{r}, F{r}:F>=(TODAY()-180)))"]]},
        {"range": f"{SHEET_NAME}!E{r}", "values": [[f"=AVERAGE(FILTER(G{r}:G, A{r}:A=A{r}, F{r}:F>=(TODAY()-365)))"]]},
        {"range": f"{SHEET_NAME}!F{r}", "values": [[serial(p["date"])]]},
        {"range": f"{SHEET_NAME}!G{r}", "values": [[price]]},
        {"range": f"{SHEET_NAME}!H{r}", "values": [[shares]]},
        {"range": f"{SHEET_NAME}!I{r}", "values": [[fee]]},
        {"range": f"{SHEET_NAME}!J{r}", "values": [[cost]]},
    ]
    svc.spreadsheets().values().batchUpdate(
        spreadsheetId=SPREADSHEET_ID,
        body={"valueInputOption": "USER_ENTERED", "data": data}).execute()
    return {"row": r, "amount": amount, "fee": fee, "cost": cost}

# ---------------- 網頁 ----------------
HTML = """<!doctype html><html lang="zh-Hant"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>買進紀錄輸入</title>
<style>
 body{font-family:-apple-system,"PingFang TC",sans-serif;background:#f4f6f8;margin:0;padding:24px;color:#222}
 .card{max-width:520px;margin:0 auto;background:#fff;border-radius:14px;box-shadow:0 2px 14px rgba(0,0,0,.08);padding:24px}
 h1{font-size:20px;margin:0 0 4px} .sub{color:#888;font-size:13px;margin-bottom:18px}
 label{display:block;font-size:13px;color:#555;margin:12px 0 4px}
 input{width:100%;box-sizing:border-box;padding:10px 12px;border:1px solid #d0d5dd;border-radius:8px;font-size:16px}
 .row{display:flex;gap:12px} .row>div{flex:1}
 .btns{display:flex;gap:12px;margin-top:18px}
 button{flex:1;padding:12px;border:0;border-radius:8px;font-size:15px;cursor:pointer}
 #calc{background:#eef2f7;color:#333} #submit{background:#2563eb;color:#fff}
 button:disabled{opacity:.5;cursor:not-allowed}
 #out{margin-top:18px;font-size:14px;line-height:1.7;display:none;background:#f8fafc;border:1px solid #e5e9ef;border-radius:10px;padding:14px}
 #out table{width:100%;border-collapse:collapse} #out td{padding:3px 0} #out td:last-child{text-align:right;font-variant-numeric:tabular-nums}
 .warn{color:#b45309} .ok{color:#15803d} .err{color:#dc2626}
 .hint{font-size:12px;color:#999;margin-top:4px}
</style></head><body>
<div class="card">
 <h1>買進紀錄輸入</h1>
 <div class="sub">寫入「股價試算 → 股票買賣紀錄」最前一筆資料列</div>
 <label>股票代號 / 名稱</label>
 <input id="code" placeholder="如 0050、2330、鴻海" autocomplete="off">
 <div class="row">
  <div><label>買進價格</label><input id="price" type="number" step="0.01" placeholder="成交均價"></div>
  <div><label>買進股數</label><input id="shares" type="number" step="1" placeholder="股"></div>
 </div>
 <div class="row">
  <div><label>買進日期</label><input id="date" type="date"></div>
  <div><label>手續費率 %</label><input id="fee" type="number" step="0.0001" value="0.1425"><div class="hint">元大標準0.1425%，最低20元；有折讓自行改</div></div>
 </div>
 <div class="btns">
  <button id="calc" onclick="run('preview')">試算</button>
  <button id="submit" onclick="run('add')" disabled>確認新增</button>
 </div>
 <div id="out"></div>
</div>
<script>
const $=id=>document.getElementById(id);
$("date").value=new Date().toISOString().slice(0,10);
function payload(){return{code:$("code").value.trim(),price:$("price").value,shares:$("shares").value,date:$("date").value,fee_rate:$("fee").value};}
async function run(act){
 const p=payload();
 if(!p.code||!p.price||!p.shares||!p.date){alert("請完整填寫代號/價格/股數/日期");return;}
 const out=$("out");out.style.display="block";out.innerHTML="處理中…";
 try{
  const res=await fetch("/"+act,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(p)});
  const d=await res.json();
  if(!res.ok){out.innerHTML='<span class="err">錯誤：'+(d.error||res.status)+'</span>';return;}
  if(act==="preview"){
   let dev="";
   if(d.close!=null){const c=Math.abs(d.dev)>6?"warn":"ok";dev=`<tr><td>當日收盤(參考)</td><td>${d.close}　<span class="${c}">買價${d.dev>=0?'+':''}${d.dev}%</span></td></tr>`;}
   else dev=`<tr><td>當日收盤</td><td>查無</td></tr>`;
   out.innerHTML=`<table>
    <tr><td>成交金額 (價×股)</td><td>${d.amount.toLocaleString()}</td></tr>
    <tr><td>手續費 (寫入I欄)</td><td>${d.fee.toLocaleString()}</td></tr>
    <tr><td><b>買進成本 (寫入J欄)</b></td><td><b>${d.cost.toLocaleString()}</b></td></tr>
    ${dev}</table>
    <div class="hint" style="margin-top:8px">確認無誤後按「確認新增」寫入分頁。</div>`;
   $("submit").disabled=false;
  }else{
   out.innerHTML=`<span class="ok">✅ 已新增到第 ${d.row} 列</span>
    <table style="margin-top:8px">
    <tr><td>買進成本</td><td>${d.cost.toLocaleString()}（含手續費 ${d.fee}）</td></tr></table>`;
   $("submit").disabled=true;
   ["code","price","shares"].forEach(i=>$(i).value="");$("code").focus();
  }
 }catch(e){out.innerHTML='<span class="err">連線失敗：'+e+'</span>';}
}
["code","price","shares","date","fee"].forEach(i=>$(i).addEventListener("input",()=>$("submit").disabled=true));
</script></body></html>"""

class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="application/json"):
        b = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", f"{ctype}; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            self._send(200, HTML, "text/html")
        else:
            self._send(404, json.dumps({"error": "not found"}))

    def do_POST(self):
        try:
            n = int(self.headers.get("Content-Length", 0))
            p = json.loads(self.rfile.read(n) or b"{}")
            if self.path == "/preview":
                self._send(200, json.dumps(preview(p)))
            elif self.path == "/add":
                self._send(200, json.dumps(add(p)))
            else:
                self._send(404, json.dumps({"error": "not found"}))
        except Exception as e:
            self._send(500, json.dumps({"error": str(e)}))

    def log_message(self, *a):
        pass

if __name__ == "__main__":
    print(f"買進紀錄輸入網頁啟動 → 請用瀏覽器開  http://localhost:{PORT}")
    print("（按 Ctrl+C 結束）")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
