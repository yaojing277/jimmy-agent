#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
reclassify_einvoice.py —— CWMoney 「電子發票帶入」記錄重新分類工具

背景：CWMoney 備份檔中，電子發票自動帶入的消費全部錯掛在
      大分類 12 電子發票帶入 / 子分類 74 EASYCARD，沒有正確分類。
本工具依 i_remark 品項關鍵字 + 發票 invoiceTime（餐別）自動歸到正確的大類+子類，
不確定者一律歸 Other Expense（使用者之後在手機自行歸類，寧可不猜）。

用法：
    python3 reclassify_einvoice.py preview [--month YYYY-MM]
        不動 DB，輸出對照 CSV 供檢視。省略 --month = 全部。
    python3 reclassify_einvoice.py apply   [--month YYYY-MM] [--yes] [--out 路徑]
        在「副本」上寫入分類，原檔永不更動。

原檔（唯讀）：~/Library/Mobile Documents/com~apple~CloudDocs/CSV/2026_07_26_CHT.iDB
"""
import argparse
import csv
import os
import re
import shutil
import sqlite3
import sys
from datetime import datetime

HOME = os.path.expanduser("~")
ICLOUD_CSV = os.path.join(HOME, "Library/Mobile Documents/com~apple~CloudDocs/CSV")
SRC_DB = os.path.join(ICLOUD_CSV, "2026_07_26_CHT.iDB")
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

EINVOICE_KIND = "12"  # 電子發票帶入 大分類
MARK_TAG = "自動分類｜"  # 可見標記：加在改動記錄的備註「最前端」，供 App 內辨識/搜尋
                        # 事後想清除：UPDATE rec_table SET i_remark=REPLACE(i_remark,'自動分類｜','');

# ---- 分類名稱對照（供 CSV 可讀輸出）----
KIND_NAME = {1: "Food,Drink", 2: "Home", 3: "Traffic", 5: "Entertainment",
             6: "Education", 8: "Healthcare", 10: "Other", 11: "3C"}
KINDS_NAME = {1: "Breakfast", 2: "Lunch", 3: "Dinner", 4: "Tea,Drink",
              47: "Snacks", 79: "Food", 97: "Electricity & Water",
              11: "Petrol", 67: "Focus Petrol", 60: "Parking fee", 80: "Car Accessory",
              23: "Clothes", 46: "Book & Magazine",
              102: "Medical Equipment", 31: "Medical Expenses",
              41: "Other Expense", 96: "Computer", 49: "Mobile", 55: "Accessory"}

OTHER = (10, 41)  # 不確定的統一去處

# ---- 品項關鍵字 ----
MEAL_KW = ["飯糰", "便當", "麵", "堡", "咖哩", "雞腿", "排骨", "水餃", "沾麵", "丼",
           "壽司", "漢堡", "薯條", "披薩", "鍋", "粥", "貝果", "可頌", "三明治",
           "吐司", "蛋餅", "炒飯", "白飯", "干絲", "冬粉", "關東煮", "熱狗", "肉粽"]
DRINK_KW = ["茶", "咖啡", "拿鐵", "奶茶", "可樂", "汽水", "可可", "珍珠", "波霸",
            "美式", "莫卡", "摩卡", "那堤", "歐蕾", "多多", "牛乳", "牛奶"]
SNACK_KW = ["餅乾", "巧克力", "洋芋片", "米果", "冰淇淋", "蠶豆", "口香糖", "糖果",
            "布丁", "果凍", "棒棒", "爆米花", "堅果"]


def parse_time(rev4):
    """從 i_rev4 JSON 取 invoiceTime 'HH:MM:SS' → 分鐘數，取不到回 None。"""
    m = re.search(r'"invoiceTime"\s*:\s*"(\d{1,2}):(\d{2})', rev4 or "")
    if not m:
        return None
    return int(m.group(1)) * 60 + int(m.group(2))


def meal_by_time(minutes):
    """依用餐時間定餐別子類 ID。取不到時間視為不確定。"""
    if minutes is None:
        return None
    if 5 * 60 <= minutes < 10 * 60 + 30:      # 05:00–10:29
        return 1   # Breakfast
    if 10 * 60 + 30 <= minutes < 15 * 60:     # 10:30–14:59
        return 2   # Lunch
    return 3       # Dinner（15:00–翌日04:59）


def positive_item_count(clean):
    """數有正金額的品項行（排除折扣、0 元贈品/點數）。"""
    return sum(1 for a in re.findall(r'x[\d.]+=(-?\d+)', clean) if int(a) > 0)


def classify(remark, money, minutes):
    """回傳 (kind_id, kinds_id, reason, confidence)。"""
    text = (remark or "").split("[")[0]          # 去掉 [手機條碼,...] 尾巴
    money = int(round(float(money))) if money not in (None, "") else 0
    n = positive_item_count(text)

    # 1) 非飲食明確關鍵字（最優先）
    if any(k in text for k in ("汽油", "無鉛", "柴油")):
        if money >= 300:
            return (3, 67, "汽油≥300→車(Focus)", "high")
        return (3, 11, "汽油<300→機車", "high")
    if "停車" in text:
        return (3, 60, "停車", "high")
    if "電費" in text:
        return (2, 97, "電費", "high")
    if "水費" in text:
        return (2, 97, "水費", "high")
    if any(k in text for k in ("機油", "雨刷", "胎壓", "輪胎")):
        return (3, 80, "汽車耗材", "high")
    if any(k in text for k in ("MacBook", "MBA ", "MBA/", "iPad", "筆電", "桌機", "主機板")):
        return (11, 96, "電腦/筆電", "high")
    if any(k in text for k in ("手冊", "雜誌", "漫畫書")):
        return (6, 46, "書籍/手冊", "high")
    if any(k in text for k in ("四角褲", "AIRism", "內褲", "內著", "褲", "鞋", "襪")):
        return (5, 23, "衣物", "high")
    if any(k in text for k in ("體溫計", "血壓計", "血糖", "口罩", "繃帶", "OK繃")):
        return (8, 102, "醫療器材", "high")

    # 2) 明顯模糊 → Other（不猜）
    if any(k in text for k in ("券", "憑證", "儲值", "禮盒", "禮券")):
        return (*OTHER, "禮券/憑證，待手機歸類", "low")
    if "行車紀錄" in text:
        return (*OTHER, "行車紀錄器(車用/3C待定)，待手機歸類", "low")

    # 3) 品項多且混合（≥5 項）一律 Other，不猜
    if n >= 5:
        return (*OTHER, "品項多且混合，待手機歸類", "low")

    # 4) 飲食
    has_meal = any(k in text for k in MEAL_KW)
    has_drink = any(k in text for k in DRINK_KW)
    has_snack = any(k in text for k in SNACK_KW)

    if has_meal:
        meal = meal_by_time(minutes)
        if meal is None:
            return (*OTHER, "餐點但無時間可判餐別", "low")
        return (1, meal, "餐點+發票時間定餐別", "med")
    if has_snack:
        return (1, 47, "零食", "med")
    if has_drink:
        if n <= 2:
            return (1, 4, "純飲品", "high")
        return (*OTHER, "含飲料但品項混合，待手機歸類", "low")

    # 4) 全部規則未命中
    return (*OTHER, "無法辨識，待手機歸類", "low")


def fetch_rows(conn, month):
    sql = ("SELECT _id, i_money, i_date, i_remark, i_rev4 FROM rec_table "
           "WHERE i_kind=?")
    args = [EINVOICE_KIND]
    if month:
        sql += " AND strftime('%Y-%m', i_date, 'unixepoch', 'localtime')=?"
        args.append(month)
    sql += " ORDER BY i_date"
    return conn.execute(sql, args).fetchall()


def build_plan(rows):
    plan = []
    for _id, money, i_date, remark, rev4 in rows:
        minutes = parse_time(rev4)
        kind, kinds, reason, conf = classify(remark, money, minutes)
        d = datetime.fromtimestamp(float(i_date))
        t = "%02d:%02d" % (minutes // 60, minutes % 60) if minutes is not None else ""
        item = (remark or "").split("[")[0].replace("\n", " ").strip()
        plan.append({
            "_id": _id, "date": d.strftime("%Y-%m-%d"), "time": t,
            "money": int(round(float(money))), "item": item[:60],
            "kind_id": kind, "kinds_id": kinds,
            "kind": KIND_NAME.get(kind, kind), "kinds": KINDS_NAME.get(kinds, kinds),
            "reason": reason, "confidence": conf,
        })
    return plan


def print_summary(plan):
    from collections import Counter
    c = Counter((p["kind"], p["kinds"]) for p in plan)
    money = {}
    for p in plan:
        key = (p["kind"], p["kinds"])
        money[key] = money.get(key, 0) + p["money"]
    print("  分類結果分佈：")
    for (k, ks), cnt in sorted(c.items(), key=lambda x: -money[x[0]]):
        print("    %-13s / %-20s  %3d 筆  $%d" % (k, ks, cnt, money[(k, ks)]))
    low = sum(1 for p in plan if p["confidence"] == "low")
    print("  低信心(歸 Other，待手機自理)：%d 筆" % low)


def cmd_preview(args):
    conn = sqlite3.connect(SRC_DB)
    rows = fetch_rows(conn, args.month)
    conn.close()
    plan = build_plan(rows)
    tag = args.month or "all"
    out = os.path.join(SCRIPT_DIR, "einvoice_reclassify_%s.csv" % tag)
    with open(out, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["_id", "date", "time", "money", "item",
                                          "kind_id", "kinds_id", "kind", "kinds",
                                          "reason", "confidence"])
        w.writeheader()
        w.writerows(plan)
    print("共 %d 筆（%s）。對照表已輸出：\n  %s\n" % (len(plan), tag, out))
    print_summary(plan)


def cmd_apply(args):
    if not os.path.exists(SRC_DB):
        sys.exit("找不到原檔：%s" % SRC_DB)
    conn = sqlite3.connect(SRC_DB)
    rows = fetch_rows(conn, args.month)
    conn.close()
    plan = build_plan(rows)
    tag = args.month or "all"
    out = args.out or os.path.join(ICLOUD_CSV, "2026_07_26_CHT_reclassified_%s.iDB" % tag)

    print("原檔：%s" % SRC_DB)
    print("將產出副本：%s" % out)
    print("預計重新分類 %d 筆（電子發票帶入 %s）\n" % (len(plan), tag))
    print_summary(plan)
    if not args.yes:
        if input("\n確認寫入副本？(yes/no) ").strip().lower() != "yes":
            sys.exit("已取消。")

    shutil.copy2(SRC_DB, out)
    conn = sqlite3.connect(out)
    before_total = conn.execute("SELECT COUNT(*) FROM rec_table").fetchone()[0]
    before_e = conn.execute("SELECT COUNT(*) FROM rec_table WHERE i_kind=?",
                            (EINVOICE_KIND,)).fetchone()[0]
    for p in plan:
        # 改分類，並在備註「最前端」補上可見標記（已存在則不重複附加）
        conn.execute(
            "UPDATE rec_table SET i_kind=?, i_kinds=?, "
            "i_remark = CASE WHEN i_remark LIKE '%'||?||'%' THEN i_remark "
            "ELSE ? || i_remark END WHERE _id=?",
            (str(p["kind_id"]), str(p["kinds_id"]), MARK_TAG, MARK_TAG, p["_id"]))
    conn.commit()
    after_total = conn.execute("SELECT COUNT(*) FROM rec_table").fetchone()[0]
    after_e = conn.execute("SELECT COUNT(*) FROM rec_table WHERE i_kind=?",
                           (EINVOICE_KIND,)).fetchone()[0]
    conn.close()

    conn2 = sqlite3.connect(out)
    n_mark = conn2.execute("SELECT COUNT(*) FROM rec_table WHERE i_remark LIKE ?",
                           ("%" + MARK_TAG + "%",)).fetchone()[0]
    conn2.close()

    print("\n✅ 完成。")
    print("  總筆數：%d → %d（應相等，只改分類不增刪）" % (before_total, after_total))
    print("  電子發票帶入(kind=12)：%d → %d 筆" % (before_e, after_e))
    print("  已加「%s」標記：%d 筆（App 內可見/可搜尋）" % (MARK_TAG, n_mark))
    print("  副本已存：%s" % out)


def main():
    ap = argparse.ArgumentParser(description="CWMoney 電子發票帶入重新分類")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p1 = sub.add_parser("preview", help="輸出對照 CSV，不動 DB")
    p1.add_argument("--month", help="YYYY-MM，省略=全部")
    p2 = sub.add_parser("apply", help="在副本上寫入分類")
    p2.add_argument("--month", help="YYYY-MM，省略=全部")
    p2.add_argument("--yes", action="store_true", help="跳過確認")
    p2.add_argument("--out", help="輸出 .iDB 路徑")
    args = ap.parse_args()
    if args.cmd == "preview":
        cmd_preview(args)
    else:
        cmd_apply(args)


if __name__ == "__main__":
    main()
