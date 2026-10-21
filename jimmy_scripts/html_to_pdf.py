"""
HTML 報告轉 PDF（2026-10-21 起；雲端硬碟不會把 HTML 當網頁顯示，PDF 才能在電腦／手機直接點開看）。

以無頭瀏覽器「列印成 PDF」：版面依報告內的 @page / @media print 樣式（A4、區塊不跨頁截斷）。
瀏覽器優先用系統的 Google Chrome（GitHub Actions 的 ubuntu runner 內建，免下載），
沒有就退回 Playwright 自帶的 Chromium（本機：pip3 install playwright && python3 -m playwright install chromium）。

用法：
  python3 html_to_pdf.py investment_since_1020_report.html investment_since_1020_report.pdf
"""
import pathlib
import sys

from playwright.sync_api import sync_playwright


def convert(html_path, pdf_path):
    src = pathlib.Path(html_path).resolve()
    if not src.exists() or src.stat().st_size == 0:
        sys.exit(f"✗ 找不到或是空檔：{html_path}")
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(channel="chrome")
        except Exception:
            browser = p.chromium.launch()
        page = browser.new_page()
        page.goto(src.as_uri(), wait_until="networkidle")   # 等 Google Fonts 載完
        page.evaluate("document.fonts.ready")
        page.emulate_media(media="print", color_scheme="light")
        # 頁面沒寫 @page 時用 A4＋頁邊距；有寫（如加碼損益報告）則以 CSS 為準
        page.pdf(path=str(pdf_path), print_background=True, prefer_css_page_size=True, format="A4",
                 margin={"top": "12mm", "bottom": "12mm", "left": "10mm", "right": "10mm"})
        browser.close()
    print(f"✓ 已產出 {pdf_path}")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    convert(sys.argv[1], sys.argv[2])
