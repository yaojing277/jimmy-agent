import os
import pickle
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

SCOPES = ['https://www.googleapis.com/auth/drive.readonly']
CREDS_FILE = '/Users/jimmy/Downloads/jimmy-agent/client_secret_613914242956-6fgpdmuqi8bgv5veccnm0iub5i3p2fdq.apps.googleusercontent.com.json'
TOKEN_FILE = '/Users/jimmy/Downloads/jimmy-agent/token.pickle'
KEYWORD = '股價試算'

def get_credentials():
    creds = None
    if os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE, 'rb') as f:
            creds = pickle.load(f)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(CREDS_FILE, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(TOKEN_FILE, 'wb') as f:
            pickle.dump(creds, f)
    return creds

def search_files(keyword):
    creds = get_credentials()
    service = build('drive', 'v3', credentials=creds)

    query = f"name contains '{keyword}' and trashed = false"
    results = service.files().list(
        q=query,
        pageSize=50,
        fields="files(id, name, mimeType, modifiedTime, webViewLink)"
    ).execute()

    files = results.get('files', [])
    if not files:
        print(f'找不到包含「{keyword}」的檔案。')
        return

    print(f'\n找到 {len(files)} 個檔案：\n')
    print(f'{"名稱":<40} {"類型":<25} {"修改時間":<25} 連結')
    print('-' * 130)
    for f in files:
        name = f.get('name', '')[:38]
        mime = f.get('mimeType', '').replace('application/vnd.google-apps.', 'Google ')
        modified = f.get('modifiedTime', '')[:19].replace('T', ' ')
        link = f.get('webViewLink', '（無）')
        print(f'{name:<40} {mime:<25} {modified:<25} {link}')

if __name__ == '__main__':
    search_files(KEYWORD)
