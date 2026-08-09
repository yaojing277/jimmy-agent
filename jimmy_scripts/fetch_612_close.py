#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""抓 2026-06-12 收盤價。資料來源:TWSE(上市)/ TPEx(上櫃)官方,不使用 Yahoo。

美股(TWSE/TPEx 無資料)一律標示略過。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import twse_hist

TARGET = "2026-06-12"

# A 欄顯示 -> 台股代號(None = 美股,TWSE/TPEx 無資料)
CODES = {
    "0050":   "0050",
    "0052":   "0052",
    "0056":   "0056",
    "00631L": "00631L",
    "662":    "00662",
    "00663L": "00663L",
    "00713":  "00713",
    "00878":  "00878",
    "00919":  "00919",
    "00934":  "00934",
    "00981A": "00981A",
    "台達電":  "2308",
    "鴻海":    "2317",
    "TSMC":   "2330",
    "聯發科":  "2454",
    "凱基金":  "2883",
    "台新金":  "2887",
    "中信金":  "2891",
    "WW":     None,        # 美股 Weight Watchers,TWSE 無資料
}

for disp, code in CODES.items():
    if code is None:
        print(f"{disp:>8} -> (美股,TWSE 無資料,略過)")
        continue
    px, note = twse_hist.close_on(code, TARGET)
    if px is not None:
        print(f"{disp:>8} -> {code} = {px} TWD")
    else:
        print(f"{disp:>8} -> {code} ({note})")
