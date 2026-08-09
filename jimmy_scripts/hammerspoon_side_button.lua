-- 滑鼠側鍵：將視窗移到下一個螢幕
-- 按鍵：側邊下方按鈕（Button index 3 = 一般滑鼠的 Back 鍵）
-- 若按下無反應，可改 button == 4 試試

local sideButtonWatcher = hs.eventtap.new(
    { hs.eventtap.event.types.otherMouseDown },
    function(event)
        local button = event:getProperty(
            hs.eventtap.event.properties.mouseEventButtonNumber
        )

        if button == 3 then
            local win = hs.window.focusedWindow()
            if win then
                local screens = hs.screen.allScreens()
                local currentScreen = win:screen()
                local nextScreen

                for i, screen in ipairs(screens) do
                    if screen == currentScreen then
                        nextScreen = screens[(i % #screens) + 1]
                        break
                    end
                end

                if nextScreen then
                    win:moveToScreen(nextScreen, true, true)
                end
            end
            return true  -- 攔截原始按鍵事件，不觸發預設行為
        end

        return false
    end
)

sideButtonWatcher:start()
