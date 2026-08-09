#!/bin/bash
sleep 5  # 等開機穩定後再執行
/usr/local/bin/blueutil --power 0
sleep 2
/usr/local/bin/blueutil --power 1
