"""
本機 HTML 報告同步到 Google 雲端硬碟（2026-10-19 起；排程：wealth_sync.yml 每個交易日）。

做法與 Wealth OS xlsm 相同：檔案由 yaojing277@gmail.com 建立（擁有者是本人、佔本人空間），
分享給 Service Account（writer），排程用 SA 以 files().update 就地覆蓋——檔案 ID 不變，
Drive 保留版本歷史。⚠ SA 沒有自己的儲存空間，**不能由 SA 建新檔**，所以 create 一定要在本機跑。

用法：
  python3 drive_report_sync.py create investment_since_0703_1016_report.html   # 本機：建檔＋分享給 SA，印出檔案 ID
  python3 drive_report_sync.py update investment_since_0703_1016_report.html <檔案ID>
                                                                                # 覆蓋內容（排程用 SA，本機用 token.json）
報告內容含個人金額：只放本人雲端硬碟，**不要改成「知道連結的人都能檢視」**。
"""
import argparse
import os
import sys
import warnings

warnings.filterwarnings("ignore")
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

from update_stock_price import get_sa_creds

HERE = os.path.dirname(os.path.abspath(__file__))
TOKEN_FILE = os.path.join(HERE, "token.json")
SA_EMAIL = "wealth-os-sync@stock-sheet-498817.iam.gserviceaccount.com"
DRIVE_SCOPE = "https://www.googleapis.com/auth/drive"


def drive_service():
    creds = get_sa_creds()
    if not creds:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        if not os.path.exists(TOKEN_FILE):
            sys.exit("找不到 token.json，請先執行：python3 reauth_sheets.py --drive")
        creds = Credentials.from_authorized_user_file(TOKEN_FILE)
        if DRIVE_SCOPE not in (creds.scopes or []):
            sys.exit("token.json 沒有 Drive 權限，請先執行：python3 reauth_sheets.py --drive")
        if not creds.valid:
            creds.refresh(Request())
    return build("drive", "v3", credentials=creds)


def media(path):
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        sys.exit(f"✗ 找不到或是空檔：{path}（不上傳，避免把雲端那份蓋成空白）")
    return MediaFileUpload(path, mimetype="text/html", resumable=False)


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("create")
    c.add_argument("path")
    c.add_argument("--name", help="雲端檔名（預設同本機檔名）")
    u = sub.add_parser("update")
    u.add_argument("path")
    u.add_argument("file_id")
    a = ap.parse_args()

    drive = drive_service()
    if a.cmd == "create":
        if get_sa_creds():
            sys.exit("✗ create 需用本人帳號（SA 沒有儲存空間）；請在本機、未設定 GOOGLE_SA_JSON 時執行")
        f = drive.files().create(body={"name": a.name or os.path.basename(a.path), "mimeType": "text/html"},
                                 media_body=media(a.path), fields="id,name,webViewLink").execute()
        drive.permissions().create(fileId=f["id"], sendNotificationEmail=False,
                                   body={"type": "user", "role": "writer", "emailAddress": SA_EMAIL}).execute()
        print(f"✓ 已建立「{f['name']}」並分享給 SA\n  檔案 ID：{f['id']}\n  {f['webViewLink']}")
    else:
        f = drive.files().update(fileId=a.file_id, media_body=media(a.path),
                                 fields="name,modifiedTime").execute()
        print(f"✓ 已覆蓋雲端「{f['name']}」（{f['modifiedTime']}）")


if __name__ == "__main__":
    main()
