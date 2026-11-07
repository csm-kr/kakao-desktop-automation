// Read-only, exact exposed window only. No action, mutation, focus or scrolling.
import AppKit
import ApplicationServices
import CryptoKit
import Foundation

func attribute(_ element: AXUIElement, _ name: String) -> (AXError, CFTypeRef?) {
    var value: CFTypeRef?
    let error = AXUIElementCopyAttributeValue(element, name as CFString, &value)
    return (error, value)
}

enum ReadFailure: Error { case incomplete }

func children(_ element: AXUIElement) throws -> [AXUIElement] {
    let (error, value) = attribute(element, kAXChildrenAttribute)
    if error == .attributeUnsupported || error == .noValue { return [] }
    guard error == .success, let elements = value as? [AXUIElement] else { throw ReadFailure.incomplete }
    return elements
}

func descendants(_ root: AXUIElement, role: String, skipTables: Bool) throws -> [AXUIElement] {
    var queue: [(AXUIElement, Int)] = [(root, 0)]
    var visited: [AXUIElement] = []
    var found: [AXUIElement] = []
    var index = 0
    while index < queue.count {
        guard index < 600 else { throw ReadFailure.incomplete }
        let (element, depth) = queue[index]; index += 1
        if visited.contains(where: { CFEqual($0, element) }) { continue }
        visited.append(element)
        guard let current = attribute(element, kAXRoleAttribute).1 as? String else { throw ReadFailure.incomplete }
        if current == role { found.append(element) }
        if skipTables && ["AXTable", "AXOutline"].contains(current) { continue }
        let next = try children(element)
        guard depth < 12 || next.isEmpty else { throw ReadFailure.incomplete }
        queue += next.map { ($0, depth + 1) }
    }
    return found
}

let args = Array(CommandLine.arguments.dropFirst())
if args == ["--help"] {
    print("Usage: kakao-collect <exact-open-chat-title> <tail-rows:1...200>\nRead-only AX text fragments; no history scrolling, author/date inference, or sending.")
    exit(0)
}
guard args.count == 2, !args[0].isEmpty, let limit = Int(args[1]), (1...200).contains(limit) else {
    print("{\"ready\":false,\"error\":\"exact title and tail row limit 1...200 required\"}")
    exit(2)
}
let session = CGSessionCopyCurrentDictionary() as? [String: Any] ?? [:]
let flags = session.filter {
    ["CGSSessionScreenIsLocked", "kCGSessionLoginDoneKey", "kCGSSessionOnConsoleKey"].contains($0.key)
}
var result: [String: Any] = [
    "version": 1, "ready": false, "target_title": args[0], "tail_rows": limit,
    "capture_id": UUID().uuidString, "captured_at": ISO8601DateFormatter().string(from: Date()),
    "session_flags": flags, "transport": "macOS AX read-only", "ax_observation_attempted": false,
    "scope": "exposed AX row tail; no history scroll; row chronology not independently validated",
    "text_only": true, "author_date_verified": false
]
if flags["CGSSessionScreenIsLocked"] as? Bool == true {
    result["error"] = "session_screen_locked"
} else if flags["kCGSessionLoginDoneKey"] as? Bool == false || flags["kCGSSessionOnConsoleKey"] as? Bool == false {
    result["error"] = "session_not_interactive"
} else if !AXIsProcessTrusted() {
    result["error"] = "accessibility_permission_missing"
} else if let app = NSRunningApplication.runningApplications(withBundleIdentifier: "com.kakao.KakaoTalkMac").first {
    result["ax_observation_attempted"] = true
    result["pid"] = app.processIdentifier
    let application = AXUIElementCreateApplication(app.processIdentifier)
    AXUIElementSetMessagingTimeout(application, 0.5)
    do {
        let (windowError, windowValue) = attribute(application, kAXWindowsAttribute)
        guard windowError == .success, let windows = windowValue as? [AXUIElement] else { throw ReadFailure.incomplete }
        let exact = windows.filter { attribute($0, kAXTitleAttribute).1 as? String == args[0] }
        result["exact_window_count"] = exact.count
        if exact.count != 1 {
            result["error"] = "exact_window_not_unique_or_not_exposed"
        } else {
            let tables = try descendants(exact[0], role: "AXTable", skipTables: true)
            guard tables.count == 1 else { throw ReadFailure.incomplete }
            let (rowsError, rowsValue) = attribute(tables[0], "AXRows")
            let rows: [AXUIElement]
            if rowsError == .success, let exposed = rowsValue as? [AXUIElement] {
                rows = exposed; result["rows_source"] = "AXRows"
            } else if rowsError == .attributeUnsupported || rowsError == .noValue {
                rows = try children(tables[0]).filter { attribute($0, kAXRoleAttribute).1 as? String == "AXRow" }
                result["rows_source"] = "direct AXRow children"
            } else { throw ReadFailure.incomplete }
            var captured: [[String: Any]] = []
            var bytes = 0
            var withoutBody = 0
            for (offset, row) in rows.suffix(limit).enumerated() {
                let fields = try descendants(row, role: "AXTextArea", skipTables: false)
                var parts: [String] = []
                for field in fields {
                    guard let body = attribute(field, kAXValueAttribute).1 as? String else { throw ReadFailure.incomplete }
                    bytes += body.utf8.count
                    guard body.utf8.count <= 32_768, bytes <= 1_048_576 else { throw ReadFailure.incomplete }
                    if !body.isEmpty { parts.append(body) }
                }
                if parts.isEmpty { withoutBody += 1 }
                let text = parts.joined(separator: "\n")
                captured.append(["row_index": max(0, rows.count - limit) + offset,
                    "text_parts": parts, "body_sha256": SHA256.hash(data: Data(text.utf8)).map { String(format: "%02x", $0) }.joined(),
                    "author": NSNull(), "date": NSNull()])
            }
            // Do not publish a successful capture after the target has changed.
            guard attribute(exact[0], kAXTitleAttribute).1 as? String == args[0],
                  NSRunningApplication.runningApplications(withBundleIdentifier: "com.kakao.KakaoTalkMac").first?.processIdentifier == app.processIdentifier else {
                throw ReadFailure.incomplete
            }
            result["rows"] = captured
            result["rows_available"] = rows.count
            result["rows_examined"] = captured.count
            result["rows_without_readable_body"] = withoutBody
            result["ready"] = true
        }
    } catch { result["error"] = "scoped_observation_incomplete" }
} else { result["error"] = "kakaotalk_not_running" }
let data = try JSONSerialization.data(withJSONObject: result, options: [.prettyPrinted, .sortedKeys])
print(String(data: data, encoding: .utf8)!)
exit(result["ready"] as? Bool == true ? 0 : 2)
