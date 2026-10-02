-- claude_hotkey.lua（Hammerspoon 模組）
-- ⌥Q：切到 Claude → 點擊輸入框 → 送出 → 方向鍵 → 1 秒後送出 Enter
-- 安裝：ln -sf <本檔> ~/.hammerspoon/claude_hotkey.lua，並在 init.lua 末尾加
--       pcall(require, "claude_hotkey")
local M = {}

local APP_NAME       = "Claude"
local INPUT_OFFSET_Y = 70    -- 輸入框中心距視窗底部的像素，點不到輸入框就調這個
local ACTIVATE_WAIT  = 0.3   -- 切換 App 後等畫面就緒
local ENTER_DELAY    = 1.0   -- → 鍵與 Enter 之間的間隔

local function run()
  local app = hs.application.get(APP_NAME)
  if not app then
    hs.application.launchOrFocus(APP_NAME)
    hs.alert.show("Claude 尚未啟動，已開啟，請再按一次")
    return
  end

  local mousePos = hs.mouse.absolutePosition()   -- 記住游標位置，結束後還原
  app:activate(true)

  M.t1 = hs.timer.doAfter(ACTIVATE_WAIT, function()   -- 計時器存進 M，避免被 GC 回收而不觸發
    local win = app:mainWindow() or app:focusedWindow()
    if not win then hs.alert.show("找不到 Claude 視窗"); return end

    local f = win:frame()
    hs.eventtap.leftClick({ x = f.x + f.w / 2, y = f.y + f.h - INPUT_OFFSET_Y })
    hs.mouse.absolutePosition(mousePos)

    M.t2 = hs.timer.doAfter(0.2, function()
      hs.eventtap.keyStroke({}, "right", 0)
      M.t3 = hs.timer.doAfter(ENTER_DELAY, function()
        hs.eventtap.keyStroke({}, "return", 0)
      end)
    end)
  end)
end

M.hotkey = hs.hotkey.bind({ "alt" }, "q", run)
return M
