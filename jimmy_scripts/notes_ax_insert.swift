// 備忘錄編輯區文字插入小工具（供 notes_audio_digest.py 的 apply 階段呼叫）
//
//   notes_ax_insert read                      印出編輯區全文（附件以 U+FFFC 表示）
//   notes_ax_insert caret <loc>               把游標移到 UTF-16 位置 loc
//   notes_ax_insert replace <loc> <len> <檔>   把 UTF-16 範圍 [loc, loc+len) 換成檔案內容
//
// 走輔助使用 API 只改選取範圍內的文字，不整則覆寫，錄音附件因此不受影響。
// （AppleScript 的 System Events 讀得到 AXSelectedTextRange 但設不進去，所以才需要這支。）
import ApplicationServices
import AppKit

func fail(_ msg: String) -> Never {
    FileHandle.standardError.write((msg + "\n").data(using: .utf8)!)
    exit(1)
}

func attr(_ el: AXUIElement, _ name: String) -> CFTypeRef? {
    var v: CFTypeRef?
    return AXUIElementCopyAttributeValue(el, name as CFString, &v) == .success ? v : nil
}

// 編輯區是視窗裡面積最大的 AXTextArea
func findTextArea(_ el: AXUIElement, _ depth: Int = 0) -> AXUIElement? {
    if (attr(el, kAXRoleAttribute) as? String) == "AXTextArea" { return el }
    if depth > 8 { return nil }
    for c in (attr(el, kAXChildrenAttribute) as? [AXUIElement]) ?? [] {
        if let hit = findTextArea(c, depth + 1) { return hit }
    }
    return nil
}

guard let app = NSRunningApplication.runningApplications(withBundleIdentifier: "com.apple.Notes").first
else { fail("備忘錄沒有在執行") }
let axApp = AXUIElementCreateApplication(app.processIdentifier)
guard let win = (attr(axApp, kAXMainWindowAttribute) ?? (attr(axApp, kAXWindowsAttribute) as? [AXUIElement])?.first as CFTypeRef?)
else { fail("找不到備忘錄視窗") }
guard let ta = findTextArea(win as! AXUIElement) else { fail("找不到編輯區") }

let args = CommandLine.arguments
if args.count >= 2 && args[1] == "read" {
    print((attr(ta, kAXValueAttribute) as? String) ?? "", terminator: "")
} else if args.count == 5 && args[1] == "replace" {
    guard let loc = Int(args[2]), let len = Int(args[3]),
          let text = try? String(contentsOfFile: args[4], encoding: .utf8) else { fail("參數錯誤") }
    var range = CFRange(location: loc, length: len)
    guard let rv = AXValueCreate(.cfRange, &range) else { fail("無法建立範圍") }
    var e = AXUIElementSetAttributeValue(ta, kAXSelectedTextRangeAttribute as CFString, rv)
    if e != .success { fail("設定選取範圍失敗（\(e.rawValue)）") }
    e = AXUIElementSetAttributeValue(ta, kAXSelectedTextAttribute as CFString, text as CFString)
    if e != .success { fail("寫入文字失敗（\(e.rawValue)）") }
} else if args.count == 3 && args[1] == "caret" {
    guard let loc = Int(args[2]) else { fail("參數錯誤") }
    var range = CFRange(location: loc, length: 0)
    guard let rv = AXValueCreate(.cfRange, &range) else { fail("無法建立範圍") }
    if AXUIElementSetAttributeValue(ta, kAXSelectedTextRangeAttribute as CFString, rv) != .success { fail("移動游標失敗") }
} else {
    fail("用法：notes_ax_insert read | caret <loc> | replace <loc> <len> <檔>")
}
