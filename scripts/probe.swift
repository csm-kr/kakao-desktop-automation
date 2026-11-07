// Read-only AX probe. No activation, focus, mutation, keyboard, or send action.
import AppKit
import ApplicationServices
import CryptoKit
import Foundation

var scanIncomplete = false

func value(_ element: AXUIElement, _ attribute: String) -> CFTypeRef? {
    var result: CFTypeRef?
    guard AXUIElementCopyAttributeValue(element, attribute as CFString, &result) == .success else { return nil }
    return result
}

func children(_ element: AXUIElement) -> [AXUIElement] {
    value(element, kAXChildrenAttribute) as? [AXUIElement] ?? []
}

func find(_ root: AXUIElement, role: String, description: String? = nil, skipTables: Bool = true) -> [AXUIElement] {
    var queue: [(AXUIElement, Int)] = [(root, 0)]
    var result: [AXUIElement] = []
    var index = 0
    while index < queue.count && index < 600 {
        let (element, depth) = queue[index]
        index += 1
        let current = value(element, kAXRoleAttribute) as? String
        if current == role && (description == nil || value(element, kAXDescriptionAttribute) as? String == description) {
            result.append(element)
        }
        if depth < 12 && !(skipTables && ["AXTable", "AXOutline"].contains(current ?? "")) {
            queue += children(element).map { ($0, depth + 1) }
        } else if depth >= 12 && !children(element).isEmpty {
            scanIncomplete = true
        }
    }
    if index < queue.count { scanIncomplete = true }
    return result
}

func center(_ element: AXUIElement) -> CGPoint? {
    guard let position = value(element, kAXPositionAttribute), CFGetTypeID(position) == AXValueGetTypeID(),
          let size = value(element, kAXSizeAttribute), CFGetTypeID(size) == AXValueGetTypeID() else { return nil }
    var point = CGPoint.zero
    var dimensions = CGSize.zero
    guard AXValueGetValue(unsafeBitCast(position, to: AXValue.self), .cgPoint, &point),
          AXValueGetValue(unsafeBitCast(size, to: AXValue.self), .cgSize, &dimensions),
          dimensions.width > 0, dimensions.height > 0 else { return nil }
    return CGPoint(x: point.x + dimensions.width / 2, y: point.y + dimensions.height / 2)
}

let arguments = Array(CommandLine.arguments.dropFirst())
if arguments == ["--help"] {
    print("Usage: kakao-probe <exact-open-chat-title> <message-to-check>\nRead-only; outputs metadata and matching counts, not message bodies.")
    exit(0)
}
guard arguments.count == 2, !arguments[0].isEmpty, !arguments[1].isEmpty else {
    print("{\"ready\":false,\"error\":\"exact chat title and message are required\"}")
    exit(2)
}
let title = arguments[0]
let message = arguments[1]
let limit = 80
var output: [String: Any] = ["ready": false, "target_title": title, "scan_limit": limit,
    "message_sha256": SHA256.hash(data: Data(message.utf8)).map { String(format: "%02x", $0) }.joined(),
    "transport": "macOS AX read-only", "foreground_changed_by_probe": false,
    "ax_observation_attempted": false]
let session = CGSessionCopyCurrentDictionary() as? [String: Any] ?? [:]
let flags = session.filter {
    ["CGSSessionScreenIsLocked", "kCGSessionLoginDoneKey", "kCGSSessionOnConsoleKey"].contains($0.key)
}
output["session_flags"] = flags
// In observed unlocked sessions the lock key was absent. Preserve that absence
// rather than claiming an explicit false or an unlocked guarantee.
if flags["CGSSessionScreenIsLocked"] as? Bool == true {
    output["error"] = "session_screen_locked"
} else if flags["kCGSessionLoginDoneKey"] as? Bool == false
            || flags["kCGSSessionOnConsoleKey"] as? Bool == false {
    output["error"] = "session_not_interactive"
} else if !AXIsProcessTrusted() {
    output["error"] = "accessibility_permission_missing"
} else if let app = NSRunningApplication.runningApplications(withBundleIdentifier: "com.kakao.KakaoTalkMac").first {
    output["ax_observation_attempted"] = true
    let application = AXUIElementCreateApplication(app.processIdentifier)
    AXUIElementSetMessagingTimeout(application, 0.5)
    output["pid"] = app.processIdentifier
    let windows = value(application, kAXWindowsAttribute) as? [AXUIElement] ?? []
    let exact = windows.filter { value($0, kAXTitleAttribute) as? String == title }
    output["exact_window_count"] = exact.count
    if exact.count == 1 {
        let fields = find(exact[0], role: "AXTextArea", description: "메시지 입력")
        let tables = find(exact[0], role: "AXTable")
        output["composer_count"] = fields.count
        output["table_count"] = tables.count
        if fields.count == 1, let draft = value(fields[0], kAXValueAttribute) as? String,
           tables.count == 1, let tableCenter = center(tables[0]) {
            let rows = value(tables[0], "AXRows") as? [AXUIElement] ?? children(tables[0])
            var count = 0
            var unreadable = false
            for row in rows.suffix(limit) {
                for text in find(row, role: "AXTextArea", skipTables: false) {
                    guard let body = value(text, kAXValueAttribute) as? String else { unreadable = true; continue }
                    if body == message {
                        if let textCenter = center(text) {
                            if textCenter.x > tableCenter.x { count += 1 }
                        } else { unreadable = true }
                    }
                }
            }
            output["composer_empty"] = draft.isEmpty
            output["matching_outgoing_count"] = count
            output["rows_examined"] = min(rows.count, limit)
            output["ready"] = !unreadable && !scanIncomplete
            if unreadable || scanIncomplete { output["error"] = "message_observation_incomplete" }
        } else { output["error"] = "composer_or_message_table_not_unique_or_readable" }
    } else { output["error"] = "exact_window_not_unique_or_not_exposed" }
} else { output["error"] = "kakaotalk_not_running" }
let data = try JSONSerialization.data(withJSONObject: output, options: [.prettyPrinted, .sortedKeys])
print(String(data: data, encoding: .utf8)!)
exit(output["ready"] as? Bool == true ? 0 : 2)
