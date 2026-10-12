// Research OS.app -- the native shell around the Research OS web app (DESIGN.md section 8.9,
// task V4 phase 3).
//
// One Swift file, AppKit + WebKit only, no Xcode project: `researchos app`
// (researchos.webapp.macapp) compiles it with the system `swiftc -O` into
// Research OS.app/Contents/MacOS/ResearchOS. The engine stays Python and the interface
// stays app.html; this program is only a real macOS window around it --
// Dock icon, menu bar, Cmd-shortcuts, no address bar -- the same split
// Obsidian, Notion and VS Code use, so every feature is still written once.
//
// What it does, and the security reasoning behind each piece:
//
// - Configuration comes from two sidecar files in Contents/Resources that
//   macapp.py writes at build time, read here at RUNTIME: `engine-path` (the
//   absolute path of the venv's `researchos` entry point, read byte-exact) and
//   `engine-port` (the port, default 7357). No value is ever interpolated
//   into this source text, so a hostile venv path (quotes, spaces, CJK)
//   can never become Swift code -- it is only ever a Process executable
//   URL.
// - Lifecycle: probe http://127.0.0.1:<port>/api/projects. If an engine
//   already answers (e.g. one the researcher started from a terminal),
//   show it and never touch it. Otherwise spawn `<engine> serve --port <port>
//   --no-browser` as a child Process, output appended to serve.log in the
//   Research OS home ($RESEARCHOS_HOME when set -- the same override researchos.paths.researchos_home
//   honours -- else ~/.researchos), and show a placeholder page (paper, the serif
//   mark, 「正在启动引擎…」) until the probe answers. After a few seconds
//   without an answer it adds that macOS may be asking for the Documents
//   folder (9.12: a rebuilt app's first read under ~/Documents waits for
//   that answer, and the log has nothing in it then). Only if the child
//   EXITS does the placeholder show the log's tail, AS TEXT: every
//   character is HTML-escaped, so a log line can never become markup. The
//   child runs with PYTHONUNBUFFERED=1, so its startup line and any error
//   reach serve.log at once. On quit, and only if THIS app spawned the engine and it is
//   still running, POST /api/shutdown with body {"pid": <child's pid>} and
//   wait for it to exit (terminate it after a grace period). The pid is
//   what keeps a terminal-started engine safe: the child may still be
//   alive WITHOUT having bound the port (blocked in a slow first-touch
//   migration) while an engine the researcher started by hand holds it --
//   the server answers 409 to a pid that is not its own and keeps
//   serving, and our own child is then stopped by signal alone (8.9: an
//   engine the user started is left alone). SIGTERM/SIGINT take the same
//   quit path.
// - Bridge, native -> page: every menu command runs the fixed string
//   `window.RCE && RCE.command('<name>')`, the name taken from the
//   compiled-in whitelist `shellCommands` below and checked again right
//   before use -- never from data. Page -> native: one message handler,
//   `researchos`, accepting exactly `{type: "title", text: String}` from the main
//   frame of the engine's own origin; the label is stripped of control
//   characters and length-capped, then the window reads 「Research OS — <label>」.
//   Since V5 phase 9 one more shape, `{type: "choose-folder", request:
//   String}` (「选择新位置…」, DESIGN.md 9.4): an NSOpenPanel limited to one
//   directory, whose answer goes back through the fixed callback
//   `RCE.folderChosen(request, path)` -- called with callAsyncJavaScript,
//   the request id and the path passed as ARGUMENTS (serialized by WebKit),
//   never spliced into script text. Since V6 (DESIGN.md 10.7) it may carry
//   a third key, `purpose`, exactly "add" or "locate", which changes only
//   the panel's message (folderPanelMessage); the two-key shape is the
//   locate panel, as before, so a page from before V6 still works. The page
//   learns it may ask (and may name a purpose) from
//   `window.RCEShellFeatures`, injected at document start. Every other
//   message shape -- any other purpose included -- is ignored.
//   Since V8 U4 (DESIGN.md 12.3, 12.5): two more folder purposes, "shared"
//   (the shared folder) and "working" (where a member's working copy goes;
//   this panel may make a new folder), and one file panel, `{type:
//   "choose-file", request, purpose: "key"}` (a member's public key file,
//   for the PI), answered through the same callback. The page learns of
//   them from "choose-folder-share" and "choose-file-key".
//   Since V8 12.13: one more folder purpose, "mine" (a member's own
//   proposals folder, shared with them on its own), from "choose-folder-mine".
//   Since V8 U5 (DESIGN.md 12.9): `{type: "mode", mode: String}`, mode
//   exactly one of teamModes, sent by the page whenever it learns the open
//   project's role. It only shows or hides 文件 > 提交提议… (a member) and
//   发布 (the PI); both commands only take the page to the place that says
//   what they would write -- nothing is written from a menu.
//   Since V9 (DESIGN.md 13.3): `{type: "language"}`, exactly one key, sent
//   when the researcher switches the interface language in the page. The
//   shell re-reads settings.json -- the message names no language and the
//   shell would not trust one -- and rebuilds its menus.
// - Language (DESIGN.md 13.2): `interface_language` ("zh" or "en") in
//   settings.json in the Research OS home, read with JSONSerialization at
//   launch and on the language message; with none saved (or anything else
//   saved), the Mac's first preferred language: "zh..." is Chinese, any
//   other English. Menus, the waiting page, alert buttons and panel texts
//   follow it. The Chinese stays in this source as the key (englishText).
//   The page is told the Mac's answer (never the saved choice, which it
//   reads from /i18n.js) at document start, `window.ROI18N_SYSTEM = "zh"`
//   or `"en"`: a web view in an app that declares no localizations may
//   report English in navigator.languages on a Chinese Mac.
// - Navigation: only http://127.0.0.1:<port> (and the placeholder's
//   about:blank) load in the window. Any other http(s)/mailto link opens
//   in the default browser; every other scheme is refused. Developer
//   extras stay off; no WebKit preference is touched.
// - The page's window.confirm/alert are answered by native sheets
//   (WKWebView returns false/does nothing without a UI delegate, which
//   would silently cancel every 删除标注 confirmation).

import AppKit
@preconcurrency import WebKit

// The page-side dispatcher (app.html, "Native shell bridge") accepts the
// same names. Only [a-z-] characters: safe inside a single-quoted JS string.
// V8 U5 (DESIGN.md 12.9): one command per sidebar section, plus 提交提议
// (a member) and 发布 (the PI).
let shellCommands: Set<String> = [
    "desk", "routes", "results", "variables", "literature", "data", "proposals", "members",
    "add-project", "propose", "publish", "reload", "zoom-in", "zoom-out", "zoom-reset", "open-map",
]

// What the page says the open project is (V8 U5): exactly one of these, or
// the message is ignored. 提交提议… shows for "member", 发布 for "pi".
let teamModes: Set<String> = ["single", "pi", "member", "none"]

let defaultPort = 7357
let probeTimeout: TimeInterval = 1.0
// After this long without an answer the waiting page names the likeliest
// cause on a first launch after an install (DESIGN.md 9.12): macOS asking
// whether Research OS may read the Documents folder, which suspends the engine's
// first read there until the researcher answers.
let startupBudget: TimeInterval = 4.0
let documentsHint = "正在启动引擎… 如果系统询问是否允许 Research OS 访问\"文稿\"文件夹，请点\"允许\"。"
let titleLimit = 60
// What the page may ask the shell for (app.html, shellCan).
let shellFeaturesScript = "window.RCEShellFeatures = [\"choose-folder\", \"choose-folder-purpose\", \"choose-folder-share\", \"choose-folder-mine\", \"choose-file-key\"];"
// The page's own request ids ("folder-<time>-<random>"): anything else is ignored.
let folderRequestLimit = 80

// The folder panel's message per purpose (DESIGN.md 10.2, 10.7); nil for
// anything else, and such a message is ignored.
func folderPanelMessage(_ purpose: String) -> String? {
    switch purpose {
    case "add": return "选择要加入 Research OS 的项目文件夹"
    case "locate": return "选择项目文件夹的新位置"
    case "shared": return "选择公盘文件夹（和团队共享的 OneDrive 文件夹）"
    case "working": return "选择一个空文件夹，用来放你的工作副本"
    case "mine": return "选择你自己的提议文件夹（PI 共享给你、你可以编辑的那个）"
    default: return nil
    }
}

// The file panel's message per purpose (V8 U4); nil for anything else.
func filePanelMessage(_ purpose: String) -> String? {
    switch purpose {
    case "key": return "选择成员发来的公钥文件（.pub）"
    default: return nil
    }
}

// -- Two languages (DESIGN.md 13.2, 13.4, 13.6) --------------------------------
// Every text the shell writes is looked up by its Chinese when it is shown:
// Chinese shows the key itself, byte for byte; English shows its entry here,
// and a missing entry shows the Chinese, never a key or a blank. The menus
// follow macOS's title case and standard names; the eight sections take
// 13.4's words. `{path}` is filled in after the lookup, once.

let settingsFilename = "settings.json"
let interfaceLanguageKey = "interface_language"

let englishText: [String: String] = [
    // the Research OS menu
    "关于 Research OS": "About Research OS",
    "退出": "Quit Research OS",
    // 文件
    "文件": "File",
    "添加项目…": "Add Project…",
    "提交提议…": "Submit Proposal…",
    "发布": "Publish",
    "关闭": "Close",
    // 编辑
    "编辑": "Edit",
    "撤销": "Undo",
    "重做": "Redo",
    "剪切": "Cut",
    "拷贝": "Copy",
    "粘贴": "Paste",
    "全选": "Select All",
    // 视图
    "视图": "View",
    "工作台": "Desk",
    "研究路线": "Research Routes",
    "成果": "Outputs",
    "变量": "Variables",
    "文献": "Literature",
    "数据与代码": "Data & Code",
    "提议": "Proposals",
    "成员": "Members",
    "重新载入": "Reload",
    "放大": "Zoom In",
    "缩小": "Zoom Out",
    "实际大小": "Actual Size",
    // 窗口, 帮助
    "窗口": "Window",
    "最小化": "Minimize",
    "缩放": "Zoom",
    "帮助": "Help",
    "打开项目地图": "Open Project Map",
    // the waiting page
    "正在启动引擎…": "Starting the engine…",
    documentsHint: "Starting the engine… If macOS asks whether Research OS may access the “Documents” folder, click “Allow”.",
    "找不到 Research OS 引擎的位置": "Research OS does not know where its engine is",
    "请在终端里重新运行 researchos app。": "Run researchos app again in Terminal.",
    "找不到 Research OS 引擎": "Research OS cannot find its engine",
    "{path} 不存在或不可执行。请在终端里重新运行 researchos app。":
        "{path} does not exist or cannot be run. Run researchos app again in Terminal.",
    "无法启动 Research OS 引擎": "Research OS cannot start its engine",
    "引擎启动失败": "The engine failed to start",
    "还没有日志文件：{path}": "No log file yet: {path}",
    "以下是 {path} 的最后几行（⌘R 重试）：": "The last lines of {path} (⌘R to try again):",
    // the page's alert and confirm sheets
    "好": "OK",
    "取消": "Cancel",
    // the open panels
    "选择": "Choose",
    "选择要加入 Research OS 的项目文件夹": "Choose the project folder to add to Research OS",
    "选择项目文件夹的新位置": "Choose the new location of the project folder",
    "选择公盘文件夹（和团队共享的 OneDrive 文件夹）": "Choose the shared folder (the OneDrive folder shared with the team)",
    "选择一个空文件夹，用来放你的工作副本": "Choose an empty folder for your working copy",
    "选择你自己的提议文件夹（PI 共享给你、你可以编辑的那个）":
        "Choose your own proposals folder (the one the PI shared with you, which you can edit)",
    "选择成员发来的公钥文件（.pub）": "Choose the public key file the member sent (.pub)",
]

func shellText(_ zh: String, _ language: String, path: String? = nil) -> String {
    let text = language == "en" ? (englishText[zh] ?? zh) : zh
    guard let path = path else { return text }
    return text.replacingOccurrences(of: "{path}", with: path)
}

// <html lang> of the waiting page (13.3).
func htmlLanguage(_ language: String) -> String {
    language == "en" ? "en" : "zh-CN"
}

// The language saved in settings.json, or nil: no file, not JSON, not an
// object, no key, or a value other than exactly "zh" or "en". The bytes
// the engine accepts and no others (settings.read_json, 13.2): UTF-8, a
// BOM allowed, never UTF-16 or UTF-32 (whose NUL bytes are valid UTF-8).
func savedInterfaceLanguage(_ settings: URL) -> String? {
    guard let data = try? Data(contentsOf: settings),
          String(data: data, encoding: .utf8) != nil, !data.contains(0),
          let object = try? JSONSerialization.jsonObject(with: data),
          let values = object as? [String: Any],
          let language = values[interfaceLanguageKey] as? String,
          language == "zh" || language == "en" else { return nil }
    return language
}

// The saved choice wins; without one, the Mac's first preferred language.
func interfaceLanguage(saved: String?, preferred: [String]) -> String {
    if let saved = saved { return saved }
    return preferred.first?.lowercased().hasPrefix("zh") == true ? "zh" : "en"
}

// 13.2: the shell's own answer for the page, injected at document start.
// i18n.js takes the saved choice first, then this, then navigator.languages,
// which inside this shell may say English on a Chinese Mac. The Mac's
// languages only (the page reads the saved choice from /i18n.js on every
// load, so a switch never leaves this stale), and only "zh" or "en".
func systemLanguageScript(preferred: [String]) -> String {
    "window.ROI18N_SYSTEM = \"" + interfaceLanguage(saved: nil, preferred: preferred) + "\";"
}

// -- end of the two languages ---------------------------------------------------

func isFolderRequest(_ request: String) -> Bool {
    !request.isEmpty && request.count <= folderRequestLimit
        && request.unicodeScalars.allSatisfy { CharacterSet.alphanumerics.contains($0) || $0 == "-" }
        && request.allSatisfy { $0.isASCII }
}

let paperColor = NSColor(srgbRed: 0xF7 / 255.0, green: 0xF2 / 255.0, blue: 0xE9 / 255.0, alpha: 1)

// -- Pure helpers -------------------------------------------------------------

func htmlEscape(_ text: String) -> String {
    var out = ""
    out.reserveCapacity(text.count)
    for ch in text {
        switch ch {
        case "&": out += "&amp;"
        case "<": out += "&lt;"
        case ">": out += "&gt;"
        case "\"": out += "&quot;"
        case "'": out += "&#39;"
        default: out.append(ch)
        }
    }
    return out
}

// A project label for the window title: control and format characters
// (newlines, bidi overrides) dropped, whitespace runs collapsed, capped.
func sanitizedLabel(_ raw: String) -> String {
    var cleaned = ""
    var lastWasSpace = false
    for scalar in raw.unicodeScalars {
        let category = scalar.properties.generalCategory
        if category == .control || category == .format || category == .lineSeparator
            || category == .paragraphSeparator {
            continue
        }
        if scalar.properties.isWhitespace {
            if !lastWasSpace && !cleaned.isEmpty { cleaned.append(" ") }
            lastWasSpace = true
        } else {
            cleaned.unicodeScalars.append(scalar)
            lastWasSpace = false
        }
    }
    cleaned = cleaned.trimmingCharacters(in: .whitespaces)
    if cleaned.count > titleLimit {
        cleaned = String(cleaned.prefix(titleLimit)) + "…"
    }
    return cleaned
}

func resourceText(_ name: String) -> String? {
    guard let url = Bundle.main.resourceURL?.appendingPathComponent(name),
          let data = try? Data(contentsOf: url) else { return nil }
    return String(data: data, encoding: .utf8)
}

func configuredPort() -> Int {
    guard let text = resourceText("engine-port"),
          let value = Int(text.trimmingCharacters(in: .whitespacesAndNewlines)),
          (1...65535).contains(value) else { return defaultPort }
    return value
}

// The home the engine will use (researchos.paths.researchos_home): the
// folder $RESEARCHOS_HOME names, else the old $RCE_HOME, else ~/.researchos
// -- or the old ~/.rce while it is the only one there, so the shell never
// creates the new home beside the old one before the engine renames it
// (DESIGN.md 12.8; the open serve.log follows the rename).
func rceHome() -> URL {
    let environment = ProcessInfo.processInfo.environment
    for name in ["RESEARCHOS_HOME", "RCE_HOME"] {
        if let override = environment[name], !override.isEmpty {
            return URL(fileURLWithPath: (override as NSString).expandingTildeInPath, isDirectory: true)
        }
    }
    let home = FileManager.default.homeDirectoryForCurrentUser
    let current = home.appendingPathComponent(".researchos", isDirectory: true)
    let legacy = home.appendingPathComponent(".rce", isDirectory: true)
    var isDirectory: ObjCBool = false
    if !FileManager.default.fileExists(atPath: current.path),
       FileManager.default.fileExists(atPath: legacy.path, isDirectory: &isDirectory), isDirectory.boolValue {
        return legacy
    }
    return current
}

// The language the shell shows now (13.2), read from the file each time.
func currentInterfaceLanguage() -> String {
    interfaceLanguage(saved: savedInterfaceLanguage(rceHome().appendingPathComponent(settingsFilename)),
                      preferred: Locale.preferredLanguages)
}

func logTail(_ url: URL, maxBytes: Int = 16 * 1024, maxLines: Int = 40) -> String? {
    guard let handle = try? FileHandle(forReadingFrom: url) else { return nil }
    defer { try? handle.close() }
    let size = (try? handle.seekToEnd()) ?? 0
    let start = size > UInt64(maxBytes) ? size - UInt64(maxBytes) : 0
    try? handle.seek(toOffset: start)
    let data = (try? handle.readToEnd()) ?? Data()
    let text = String(decoding: data, as: UTF8.self)
    let lines = text.split(separator: "\n", omittingEmptySubsequences: false)
    return lines.suffix(maxLines).joined(separator: "\n")
}

// -- Placeholder pages (fixed markup; only escaped text is ever inserted) -----

func placeholderPage(headline: String, detail: String?, logText: String?, htmlLang: String) -> String {
    var body = "<div class=\"mark\">Research OS</div><p class=\"msg\">\(htmlEscape(headline))</p>"
    if let detail = detail {
        body += "<p class=\"detail\">\(htmlEscape(detail))</p>"
    }
    if let logText = logText {
        body += "<pre>\(htmlEscape(logText))</pre>"
    }
    return """
    <!doctype html><html lang="\(htmlEscape(htmlLang))"><head><meta charset="utf-8">
    <meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'">
    <style>
    html, body { margin: 0; height: 100%; background: #F7F2E9; color: #26221B; }
    body { display: flex; flex-direction: column; align-items: center; justify-content: center;
           font-family: "PingFang SC", -apple-system, sans-serif; }
    .mark { font-family: "Songti SC", "STSong", serif; font-size: 56px; letter-spacing: 0.04em; }
    .msg { font-size: 15px; color: #5B564A; margin: 14px 0 0; }
    .detail { font-size: 13px; color: #5B564A; margin: 8px 24px 0; text-align: center; }
    pre { max-width: 86%; max-height: 50%; overflow: auto; margin: 18px 0 0; padding: 12px 14px;
          background: #F0E9DA; border: 1px solid rgba(38, 34, 27, 0.14); border-radius: 6px;
          font: 12px ui-monospace, Menlo, monospace; white-space: pre-wrap; word-break: break-all; }
    </style></head><body>\(body)</body></html>
    """
}

// -- The application ------------------------------------------------------------

final class AppDelegate: NSObject, NSApplicationDelegate, NSMenuItemValidation,
    WKNavigationDelegate, WKUIDelegate, WKScriptMessageHandler {

    let port = configuredPort()
    var window: NSWindow!
    var webView: WKWebView!
    var child: Process?
    var probeTimer: Timer?
    var launchStarted = Date()
    var appLoaded = false
    var showingLog = false
    var hintShown = false
    var quitting = false
    var signalSources: [DispatchSourceSignal] = []
    var choosingFolder = false
    var teamMode = "single"
    var language = "zh"
    var proposeItem: NSMenuItem!
    var publishItem: NSMenuItem!
    var teamSeparator: NSMenuItem!

    lazy var session: URLSession = {
        let config = URLSessionConfiguration.ephemeral
        config.timeoutIntervalForRequest = probeTimeout
        config.requestCachePolicy = .reloadIgnoringLocalCacheData
        return URLSession(configuration: config)
    }()

    var baseURL: URL { URL(string: "http://127.0.0.1:\(port)/")! }
    var logURL: URL { rceHome().appendingPathComponent("serve.log") }

    // MARK: launch

    func applicationDidFinishLaunching(_ notification: Notification) {
        language = currentInterfaceLanguage()
        NSApp.mainMenu = buildMainMenu()
        buildWindow()
        installSignalHandlers()
        startEngine()
        NSApp.activate(ignoringOtherApps: true)
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        true
    }

    func buildWindow() {
        window = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 1280, height: 840),
            styleMask: [.titled, .closable, .miniaturizable, .resizable],
            backing: .buffered, defer: false)
        window.title = "Research OS"
        window.contentMinSize = NSSize(width: 900, height: 600)
        window.isReleasedWhenClosed = false
        window.backgroundColor = paperColor
        window.center()
        window.setFrameAutosaveName("RCEMain")

        let config = WKWebViewConfiguration()
        config.userContentController.add(self, name: "rce")
        config.userContentController.addUserScript(WKUserScript(
            source: shellFeaturesScript, injectionTime: .atDocumentStart, forMainFrameOnly: true))
        config.userContentController.addUserScript(WKUserScript(
            source: systemLanguageScript(preferred: Locale.preferredLanguages),
            injectionTime: .atDocumentStart, forMainFrameOnly: true))
        webView = WKWebView(frame: window.contentView!.bounds, configuration: config)
        webView.autoresizingMask = [.width, .height]
        webView.navigationDelegate = self
        webView.uiDelegate = self
        webView.setValue(false, forKey: "drawsBackground")
        window.contentView!.addSubview(webView)
        window.makeKeyAndOrderFront(nil)
    }

    func installSignalHandlers() {
        for sig in [SIGTERM, SIGINT] {
            signal(sig, SIG_IGN)
            let source = DispatchSource.makeSignalSource(signal: sig, queue: .main)
            source.setEventHandler { NSApp.terminate(nil) }
            source.resume()
            signalSources.append(source)
        }
    }

    // MARK: language

    // A text in the language the shell shows now (englishText).
    func tr(_ zh: String, path: String? = nil) -> String {
        shellText(zh, language, path: path)
    }

    // 13.3: the page saved a new choice. Re-read the file, never the
    // message; buildMainMenu recreates 提交提议… / 发布 and re-applies the
    // team mode to them.
    func applyLanguage() {
        language = currentInterfaceLanguage()
        NSApp.mainMenu = buildMainMenu()
    }

    // MARK: engine lifecycle

    func showPlaceholder(_ headline: String, detail: String? = nil, log: String? = nil) {
        appLoaded = false
        let page = placeholderPage(headline: headline, detail: detail, logText: log,
                                   htmlLang: htmlLanguage(language))
        webView.loadHTMLString(page, baseURL: nil)
    }

    func probe(_ done: @escaping (Bool) -> Void) {
        var request = URLRequest(url: baseURL.appendingPathComponent("api/projects"))
        request.timeoutInterval = probeTimeout
        session.dataTask(with: request) { _, response, error in
            let ok = error == nil && (response as? HTTPURLResponse)?.statusCode == 200
            DispatchQueue.main.async { done(ok) }
        }.resume()
    }

    // Probe once; an engine that answers is used as-is (and never stopped
    // by us). Otherwise spawn our own and poll until it answers.
    func startEngine() {
        probeTimer?.invalidate()
        showingLog = false
        hintShown = false
        launchStarted = Date()
        showPlaceholder(tr("正在启动引擎…"))
        probe { [weak self] ok in
            guard let self = self else { return }
            if ok { self.loadApp(); return }
            if self.child?.isRunning != true {
                guard self.spawnEngine() else { return }
            }
            self.schedulePolling()
        }
    }

    func spawnEngine() -> Bool {
        guard let rcePath = resourceText("engine-path"), !rcePath.isEmpty else {
            showPlaceholder(tr("找不到 Research OS 引擎的位置"), detail: tr("请在终端里重新运行 researchos app。"))
            return false
        }
        guard FileManager.default.isExecutableFile(atPath: rcePath) else {
            showPlaceholder(tr("找不到 Research OS 引擎"),
                            detail: tr("{path} 不存在或不可执行。请在终端里重新运行 researchos app。", path: rcePath))
            return false
        }
        let home = rceHome()
        try? FileManager.default.createDirectory(at: home, withIntermediateDirectories: true)
        if !FileManager.default.fileExists(atPath: logURL.path) {
            FileManager.default.createFile(atPath: logURL.path, contents: nil)
        }
        let process = Process()
        process.executableURL = URL(fileURLWithPath: rcePath)
        process.arguments = ["serve", "--port", String(port), "--no-browser"]
        // Unbuffered, so serve.log has the engine's words by the time the
        // placeholder shows its tail -- a block-buffered child that hangs
        // would otherwise leave the log empty.
        var environment = ProcessInfo.processInfo.environment
        environment["PYTHONUNBUFFERED"] = "1"
        process.environment = environment
        process.standardInput = FileHandle.nullDevice
        if let handle = try? FileHandle(forWritingTo: logURL) {
            _ = try? handle.seekToEnd()
            process.standardOutput = handle
            process.standardError = handle
        }
        process.terminationHandler = { [weak self] _ in
            DispatchQueue.main.async { self?.engineExited() }
        }
        do {
            try process.run()
        } catch {
            showPlaceholder(tr("无法启动 Research OS 引擎"), detail: error.localizedDescription)
            return false
        }
        child = process
        return true
    }

    func engineExited() {
        if quitting || appLoaded { return }
        probeTimer?.invalidate()
        showLog(headline: tr("引擎启动失败"))
    }

    func schedulePolling() {
        probeTimer?.invalidate()
        let timer = Timer(timeInterval: 0.25, repeats: true) { [weak self] _ in self?.pollTick() }
        RunLoop.main.add(timer, forMode: .common)
        probeTimer = timer
    }

    func pollTick() {
        probe { [weak self] ok in
            guard let self = self, !self.appLoaded, !self.quitting else { return }
            if ok {
                self.probeTimer?.invalidate()
                self.loadApp()
            } else if !self.showingLog && !self.hintShown
                        && Date().timeIntervalSince(self.launchStarted) > startupBudget {
                // Keep polling afterwards: a slow start still recovers. No
                // log tail here -- a suspended engine has written nothing;
                // the log is shown only once the engine has EXITED.
                self.hintShown = true
                self.showPlaceholder(self.tr(documentsHint))
            }
        }
    }

    func showLog(headline: String) {
        showingLog = true
        let tail = logTail(logURL)
        let detail = tail == nil
            ? tr("还没有日志文件：{path}", path: logURL.path)
            : tr("以下是 {path} 的最后几行（⌘R 重试）：", path: logURL.path)
        showPlaceholder(headline, detail: detail, log: tail ?? nil)
    }

    func loadApp() {
        appLoaded = true
        webView.load(URLRequest(url: baseURL))
    }

    // MARK: quit

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        guard let process = child, process.isRunning else { return .terminateNow }
        if quitting { return .terminateLater }
        quitting = true
        probeTimer?.invalidate()
        var request = URLRequest(url: baseURL.appendingPathComponent("api/shutdown"))
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        // Only the engine whose pid this is may act on it (header comment).
        request.httpBody = Data("{\"pid\": \(process.processIdentifier)}".utf8)
        session.dataTask(with: request) { _, _, _ in }.resume()

        let deadline = Date().addingTimeInterval(3)
        var terminatedAt: Date?
        let timer = Timer(timeInterval: 0.1, repeats: true) { timer in
            if !process.isRunning {
                timer.invalidate()
                NSApp.reply(toApplicationShouldTerminate: true)
            } else if terminatedAt == nil && Date() > deadline {
                terminatedAt = Date()
                process.terminate()
            } else if let t = terminatedAt, Date().timeIntervalSince(t) > 2 {
                kill(process.processIdentifier, SIGKILL)
                timer.invalidate()
                NSApp.reply(toApplicationShouldTerminate: true)
            }
        }
        RunLoop.main.add(timer, forMode: .common)
        return .terminateLater
    }

    // MARK: menus

    // Every title below is written in Chinese and shown through tr.
    func item(_ title: String, _ action: Selector?, _ key: String = "",
              _ modifiers: NSEvent.ModifierFlags = [.command], command: String? = nil) -> NSMenuItem {
        let menuItem = NSMenuItem(title: tr(title), action: action, keyEquivalent: key)
        menuItem.keyEquivalentModifierMask = modifiers
        if let command = command {
            precondition(shellCommands.contains(command))
            menuItem.target = self
            menuItem.representedObject = command
        }
        return menuItem
    }

    func cmd(_ title: String, _ name: String, _ key: String = "",
             _ modifiers: NSEvent.ModifierFlags = [.command]) -> NSMenuItem {
        item(title, #selector(sendCommand(_:)), key, modifiers, command: name)
    }

    func submenu(_ title: String, _ items: [NSMenuItem]) -> NSMenuItem {
        let top = NSMenuItem(title: tr(title), action: nil, keyEquivalent: "")
        let menu = NSMenu(title: tr(title))
        items.forEach { menu.addItem($0) }
        top.submenu = menu
        return top
    }

    func buildMainMenu() -> NSMenu {
        let main = NSMenu()
        main.addItem(submenu("Research OS", [
            item("关于 Research OS", #selector(NSApplication.orderFrontStandardAboutPanel(_:)), ""),
            .separator(),
            item("退出", #selector(NSApplication.terminate(_:)), "q"),
        ]))
        // 提交提议… / 发布 appear only in a collaborative project, by role
        // (applyTeamMode); a project of one person has neither.
        proposeItem = cmd("提交提议…", "propose")
        publishItem = cmd("发布", "publish")
        teamSeparator = .separator()
        main.addItem(submenu("文件", [
            cmd("添加项目…", "add-project", "o"),
            teamSeparator,
            proposeItem,
            publishItem,
            .separator(),
            item("关闭", #selector(NSWindow.performClose(_:)), "w"),
        ]))
        applyTeamMode()
        main.addItem(submenu("编辑", [
            item("撤销", Selector(("undo:")), "z"),
            item("重做", Selector(("redo:")), "z", [.command, .shift]),
            .separator(),
            item("剪切", #selector(NSText.cut(_:)), "x"),
            item("拷贝", #selector(NSText.copy(_:)), "c"),
            item("粘贴", #selector(NSText.paste(_:)), "v"),
            item("全选", #selector(NSText.selectAll(_:)), "a"),
        ]))
        // ⌘= reaches 放大 too (no Shift needed on most layouts) through a
        // hidden twin, as Safari does.
        let zoomInAlt = cmd("放大", "zoom-in", "=")
        zoomInAlt.isHidden = true
        zoomInAlt.allowsKeyEquivalentWhenHidden = true
        let viewMenu = submenu("视图", [
            cmd("工作台", "desk", "1"),
            cmd("研究路线", "routes", "2"),
            cmd("成果", "results", "3"),
            cmd("变量", "variables", "4"),
            cmd("文献", "literature", "5"),
            cmd("数据与代码", "data", "6"),
            cmd("提议", "proposals", "7"),
            cmd("成员", "members", "8"),
            .separator(),
            item("重新载入", #selector(reloadPage(_:)), "r"),
            .separator(),
            cmd("放大", "zoom-in", "+"),
            zoomInAlt,
            cmd("缩小", "zoom-out", "-"),
            cmd("实际大小", "zoom-reset", "0"),
        ])
        viewMenu.submenu?.items.first { $0.action == #selector(reloadPage(_:)) }?.target = self
        main.addItem(viewMenu)
        let windowMenu = submenu("窗口", [
            item("最小化", #selector(NSWindow.performMiniaturize(_:)), "m"),
            item("缩放", #selector(NSWindow.performZoom(_:)), ""),
        ])
        main.addItem(windowMenu)
        NSApp.windowsMenu = windowMenu.submenu
        let helpMenu = submenu("帮助", [cmd("打开项目地图", "open-map")])
        main.addItem(helpMenu)
        NSApp.helpMenu = helpMenu.submenu
        return main
    }

    func validateMenuItem(_ menuItem: NSMenuItem) -> Bool {
        if menuItem === proposeItem { return appLoaded && teamMode == "member" }
        if menuItem === publishItem { return appLoaded && teamMode == "pi" }
        if menuItem.action == #selector(sendCommand(_:)) { return appLoaded }
        return true
    }

    // 文件 > 提交提议… for a member, 发布 for the PI; neither otherwise.
    func applyTeamMode() {
        proposeItem.isHidden = teamMode != "member"
        publishItem.isHidden = teamMode != "pi"
        teamSeparator.isHidden = teamMode != "member" && teamMode != "pi"
    }

    @objc func sendCommand(_ sender: NSMenuItem) {
        guard let name = sender.representedObject as? String, shellCommands.contains(name),
              name.allSatisfy({ ("a"..."z").contains($0) || $0 == "-" }), appLoaded else { return }
        webView.evaluateJavaScript("window.RCE && RCE.command('\(name)')", completionHandler: nil)
    }

    // 重新载入: the page reloads itself; the placeholder re-runs the probe
    // (and respawns the engine if ours is no longer running).
    @objc func reloadPage(_ sender: Any?) {
        if appLoaded {
            webView.evaluateJavaScript("window.RCE && RCE.command('reload')") { [weak self] result, _ in
                if (result as? Bool) != true { self?.webView.reload() }
            }
        } else {
            startEngine()
        }
    }

    // MARK: navigation

    func isEngineURL(_ url: URL?) -> Bool {
        guard let url = url else { return false }
        return url.scheme == "http" && url.host == "127.0.0.1" && url.port == port
    }

    func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        let url = navigationAction.request.url
        if isEngineURL(url) || url?.absoluteString == "about:blank" {
            decisionHandler(.allow)
            return
        }
        decisionHandler(.cancel)
        openExternally(url)
    }

    func openExternally(_ url: URL?) {
        guard let url = url, let scheme = url.scheme?.lowercased(),
              ["http", "https", "mailto"].contains(scheme) else { return }
        NSWorkspace.shared.open(url)
    }

    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!,
                 withError error: Error) {
        // The engine went away between the probe and the load: start over.
        if appLoaded && !quitting {
            appLoaded = false
            startEngine()
        }
    }

    // target=_blank / window.open: never a second web view.
    func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration,
                 for navigationAction: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
        let url = navigationAction.request.url
        if isEngineURL(url), let url = url {
            webView.load(URLRequest(url: url))
        } else {
            openExternally(url)
        }
        return nil
    }

    // MARK: page dialogs (window.alert / window.confirm)

    func webView(_ webView: WKWebView, runJavaScriptAlertPanelWithMessage message: String,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping () -> Void) {
        let alert = NSAlert()
        alert.messageText = message
        alert.addButton(withTitle: tr("好"))
        alert.beginSheetModal(for: window) { _ in completionHandler() }
    }

    func webView(_ webView: WKWebView, runJavaScriptConfirmPanelWithMessage message: String,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping (Bool) -> Void) {
        let alert = NSAlert()
        alert.messageText = message
        alert.addButton(withTitle: tr("好"))
        alert.addButton(withTitle: tr("取消"))
        alert.beginSheetModal(for: window) { response in
            completionHandler(response == .alertFirstButtonReturn)
        }
    }

    // MARK: bridge (page -> native)

    func userContentController(_ userContentController: WKUserContentController,
                               didReceive message: WKScriptMessage) {
        guard message.name == "rce", message.frameInfo.isMainFrame else { return }
        let origin = message.frameInfo.securityOrigin
        guard origin.protocol == "http", origin.host == "127.0.0.1", origin.port == port else { return }
        guard let body = message.body as? [String: Any] else { return }
        if body.count == 2, body["type"] as? String == "title", let text = body["text"] as? String {
            let label = sanitizedLabel(text)
            window.title = label.isEmpty ? "Research OS" : "Research OS — " + label
        } else if body.count == 2, body["type"] as? String == "mode",
                  let mode = body["mode"] as? String, teamModes.contains(mode) {
            teamMode = mode
            applyTeamMode()
        } else if body.count == 2, body["type"] as? String == "choose-folder",
                  let request = body["request"] as? String, isFolderRequest(request) {
            chooseFolder(request, purpose: "locate")
        } else if body.count == 3, body["type"] as? String == "choose-folder",
                  let request = body["request"] as? String, isFolderRequest(request),
                  let purpose = body["purpose"] as? String, folderPanelMessage(purpose) != nil {
            chooseFolder(request, purpose: purpose)
        } else if body.count == 3, body["type"] as? String == "choose-file",
                  let request = body["request"] as? String, isFolderRequest(request),
                  let purpose = body["purpose"] as? String, filePanelMessage(purpose) != nil {
            chooseFile(request, purpose: purpose)
        } else if body.count == 1, body["type"] as? String == "language" {
            applyLanguage()
        }
    }

    // A member's public key file (V8 U4): one file, chosen in a sheet; the
    // path goes back through the same fixed callback as a folder's.
    func chooseFile(_ request: String, purpose: String) {
        guard !choosingFolder, appLoaded, let panelMessage = filePanelMessage(purpose) else {
            answerFolder(request, nil)
            return
        }
        choosingFolder = true
        let panel = NSOpenPanel()
        panel.canChooseDirectories = false
        panel.canChooseFiles = true
        panel.allowsMultipleSelection = false
        panel.canCreateDirectories = false
        panel.prompt = tr("选择")
        panel.message = tr(panelMessage)
        panel.beginSheetModal(for: window) { [weak self] response in
            guard let self = self else { return }
            self.choosingFolder = false
            let path = response == .OK ? panel.url?.path : nil
            self.answerFolder(request, path)
        }
    }

    // 「选择新位置…」 / 「添加项目…」: one directory, chosen in a sheet; the
    // answer (or null when cancelled) goes back through the page's fixed
    // callback. The purpose picks the panel's message, nothing else.
    func chooseFolder(_ request: String, purpose: String) {
        guard !choosingFolder, appLoaded, let panelMessage = folderPanelMessage(purpose) else {
            answerFolder(request, nil)
            return
        }
        choosingFolder = true
        let panel = NSOpenPanel()
        panel.canChooseDirectories = true
        panel.canChooseFiles = false
        panel.allowsMultipleSelection = false
        panel.canCreateDirectories = purpose == "working"
        panel.prompt = tr("选择")
        panel.message = tr(panelMessage)
        panel.beginSheetModal(for: window) { [weak self] response in
            guard let self = self else { return }
            self.choosingFolder = false
            let path = response == .OK ? panel.url?.path : nil
            self.answerFolder(request, path)
        }
    }

    func answerFolder(_ request: String, _ path: String?) {
        let arguments: [String: Any] = ["request": request, "path": path ?? NSNull()]
        webView.callAsyncJavaScript("window.RCE && RCE.folderChosen(request, path)", arguments: arguments,
                                    in: nil, in: .page, completionHandler: nil)
    }
}

@main
struct RCEShellMain {
    static func main() {
        let app = NSApplication.shared
        let delegate = AppDelegate()
        app.delegate = delegate
        app.setActivationPolicy(.regular)
        withExtendedLifetime(delegate) { app.run() }
    }
}
