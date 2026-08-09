#!/usr/bin/env python3
"""
今彩539 歷史開獎號碼爬蟲
資料來源: https://www.pilio.idv.tw/lto539/list.asp
輸出:
  - 539_history.csv   每筆開獎紀錄
  - 539_weekly.csv    每週彙整（週一到週六各期號碼）
"""
import requests
from bs4 import BeautifulSoup
import csv
import re
import time
from datetime import date
from collections import defaultdict

BASE_URL = "https://www.pilio.idv.tw/lto539/list.asp"
HISTORY_OUTPUT = "/Users/jimmy/Downloads/jimmy-agent/jimmy_scripts/539_history.csv"
WEEKLY_OUTPUT  = "/Users/jimmy/Downloads/jimmy-agent/jimmy_scripts/539_weekly.csv"
TOTAL_PAGES    = 256
SLEEP_BETWEEN  = 0.5   # 秒，避免對伺服器造成負擔

WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def parse_date_cell(text: str):
    """
    解析日期儲存格文字，例如 '01/01|07(...)' 或 '05/30\n26(...)'
    回傳 (date, weekday_en)，不依賴頁面的中文星期字（Big5 解碼易亂碼）
    """
    text = text.replace("\xa0", " ").strip()
    # MM/DD 後接非數字分隔，再接 2 位年份
    m = re.search(r"(\d{1,2})/(\d{1,2})\D+(\d{2})", text)
    if not m:
        return None, None
    month, day, year_2d = m.groups()
    year = 2000 + int(year_2d)
    try:
        d = date(year, int(month), int(day))
    except ValueError:
        return None, None
    return d, WEEKDAYS[d.weekday()]


def parse_numbers(text: str):
    """解析 '02, 03, 04, 13, 39' 為整數 list"""
    nums = re.findall(r"\d+", text)
    return [int(n) for n in nums if 1 <= int(n) <= 39]


def scrape_page(page: int) -> list[dict]:
    url = f"{BASE_URL}?indexpage={page}&orderby=old"
    for attempt in range(3):
        try:
            resp = requests.get(url, timeout=20,
                                headers={"User-Agent": "Mozilla/5.0"})
            resp.encoding = "big5"
            soup = BeautifulSoup(resp.text, "html.parser")
            break
        except Exception as e:
            print(f"  [重試 {attempt+1}] 第 {page} 頁發生錯誤: {e}")
            time.sleep(2)
    else:
        return []

    results = []
    # 直接用 CSS class 定位，比逐 table 解析更穩定
    date_cells = soup.find_all("td", class_="date-cell")
    num_cells  = soup.find_all("td", class_="number-cell")

    for date_td, num_td in zip(date_cells, num_cells):
        # 日期 cell: "01/01<br/>07(六)" → get_text(sep='\n') → "01/01\n07(六)"
        date_text = date_td.get_text(separator="\n")
        nums_text = num_td.get_text()

        draw_date, weekday = parse_date_cell(date_text)
        numbers = parse_numbers(nums_text)

        if draw_date and len(numbers) == 5:
            iso = draw_date.isocalendar()
            results.append({
                "date":     draw_date.isoformat(),
                "weekday":  weekday,
                "year":     draw_date.year,
                "iso_week": f"{iso[0]}-W{iso[1]:02d}",
                "n1": numbers[0],
                "n2": numbers[1],
                "n3": numbers[2],
                "n4": numbers[3],
                "n5": numbers[4],
            })
    return results


def build_weekly(draws: list[dict]) -> list[dict]:
    """將每筆資料依 iso_week 彙整成週表"""
    weeks: dict[str, dict] = defaultdict(lambda: {
        "iso_week": "", "year": 0,
        "Mon": "", "Tue": "", "Wed": "",
        "Thu": "", "Fri": "", "Sat": "",
        "draw_count": 0,
    })

    for d in draws:
        wk = d["iso_week"]
        wd = d["weekday"]
        nums_str = f"{d['n1']},{d['n2']},{d['n3']},{d['n4']},{d['n5']}"
        weeks[wk]["iso_week"]    = wk
        weeks[wk]["year"]        = d["year"]
        weeks[wk][wd]            = nums_str
        weeks[wk]["draw_count"] += 1

    return sorted(weeks.values(), key=lambda x: x["iso_week"])


def main():
    print("=== 今彩539 歷史開獎號碼爬蟲 ===")
    print(f"資料來源: {BASE_URL}")
    print(f"總頁數: {TOTAL_PAGES} 頁\n")

    all_draws: list[dict] = []

    for page in range(1, TOTAL_PAGES + 1):
        draws = scrape_page(page)
        all_draws.extend(draws)
        if page % 10 == 0 or page == TOTAL_PAGES:
            print(f"進度: {page}/{TOTAL_PAGES} 頁，累計 {len(all_draws)} 筆")
        time.sleep(SLEEP_BETWEEN)

    print(f"\n共抓取 {len(all_draws)} 筆開獎資料")

    # 去除重複（同一日期可能出現在相鄰兩頁的邊界）
    seen = set()
    unique_draws = []
    for d in all_draws:
        key = d["date"]
        if key not in seen:
            seen.add(key)
            unique_draws.append(d)
    unique_draws.sort(key=lambda x: x["date"])

    print(f"去除重複後: {len(unique_draws)} 筆")

    # 輸出每日明細
    with open(HISTORY_OUTPUT, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=["date","weekday","year","iso_week","n1","n2","n3","n4","n5"])
        writer.writeheader()
        writer.writerows(unique_draws)
    print(f"\n每日明細已存至: {HISTORY_OUTPUT}")

    # 輸出週彙整
    weekly = build_weekly(unique_draws)
    with open(WEEKLY_OUTPUT, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=["iso_week","year","draw_count","Mon","Tue","Wed","Thu","Fri","Sat"])
        writer.writeheader()
        writer.writerows(weekly)
    print(f"週彙整已存至:   {WEEKLY_OUTPUT}")
    print(f"\n完成！共 {len(weekly)} 週的資料。")


if __name__ == "__main__":
    main()
