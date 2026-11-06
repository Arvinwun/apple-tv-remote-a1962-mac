// Siri Remote 菜单栏程序：点图标弹出紧凑面板（WebView 加载本地 python 服务）。
// 构建：./build-app.sh   →  SiriRemote.app
import Cocoa
import WebKit
import CoreBluetooth

let PORT: UInt16 = 8787
let BASE = URL(string: "http://127.0.0.1:\(PORT)")!

func dbg(_ msg: String) {
    let line = "\(Date()) \(msg)\n"
    if let h = FileHandle(forWritingAtPath: "/tmp/siriremote-app.log") {
        h.seekToEndOfFile(); h.write(line.data(using: .utf8)!); h.closeFile()
    } else {
        try? line.write(toFile: "/tmp/siriremote-app.log", atomically: true, encoding: .utf8)
    }
}

func portOpen(_ port: UInt16) -> Bool {
    let fd = socket(AF_INET, SOCK_STREAM, 0)
    if fd < 0 { return false }
    defer { close(fd) }
    var addr = sockaddr_in()
    addr.sin_family = sa_family_t(AF_INET)
    addr.sin_port = port.bigEndian
    addr.sin_addr.s_addr = inet_addr("127.0.0.1")
    addr.sin_len = UInt8(MemoryLayout<sockaddr_in>.size)
    let r = withUnsafePointer(to: &addr) {
        $0.withMemoryRebound(to: sockaddr.self, capacity: 1) {
            connect(fd, $0, socklen_t(MemoryLayout<sockaddr_in>.size))
        }
    }
    return r == 0
}


// MARK: - BLE 探测
// 目的：绕过系统 HOGP 驱动，直接看遥控器的 GATT 特征。
// 背景：macOS 的 HID 层把厂商报告 0xFC 重映射成 0xFA，payload（音频）在驱动层就被丢了。
// 协议文档说音频是 ATT handle 0x0023 的 notification、每帧 101 字节。
// 这里只做**只读探测**：枚举服务/特征、订阅 notify、统计包长，先确认那个特征存不存在。
final class BLEProbe: NSObject, CBCentralManagerDelegate, CBPeripheralDelegate {
    static let logPath = "/tmp/siriremote-ble.log"
    /// 遥控器的蓝牙广播名就是它的序列号（11 位大写字母数字）。
    /// 想锁定某台设备就设环境变量 SIRI_REMOTE_NAME；不设则按格式自动识别，
    /// 这样换设备、别人用都不必改代码。
    static func isTarget(_ name: String) -> Bool {
        let want = ProcessInfo.processInfo.environment["SIRI_REMOTE_NAME"] ?? ""
        if !want.isEmpty { return name.localizedCaseInsensitiveContains(want) }
        return name.count >= 10 && name.allSatisfy { $0.isUppercase || $0.isNumber }
    }

    private var central: CBCentralManager?
    private var target: CBPeripheral?
    private var lines: [String] = []
    private var seen = Set<String>()
    private var finished = false
    private var subscribed = 0
    private var packetStats: [Int: Int] = [:]       // 字节长度 -> 次数
    private var samples: [Int: String] = [:]

    func start(seconds: Double = 30) {
        lines.removeAll(); seen.removeAll(); packetStats.removeAll(); samples.removeAll()
        subscribed = 0; finished = false
        log("=== BLE 探测开始（最多 \(Int(seconds)) 秒）===")
        log("请按一下遥控器任意键，让它开始广播")
        central = CBCentralManager(delegate: self, queue: nil)
        DispatchQueue.main.asyncAfter(deadline: .now() + seconds) { [weak self] in
            self?.finish("时间到")
        }
    }

    private func log(_ s: String) {
        lines.append(s)
        dbg("[ble] " + s)
        try? lines.joined(separator: "\n")
            .write(toFile: BLEProbe.logPath, atomically: true, encoding: .utf8)
    }

    private func propText(_ p: CBCharacteristicProperties) -> String {
        var out: [String] = []
        if p.contains(.read) { out.append("read") }
        if p.contains(.write) { out.append("write") }
        if p.contains(.writeWithoutResponse) { out.append("writeNoResp") }
        if p.contains(.notify) { out.append("notify") }
        if p.contains(.indicate) { out.append("indicate") }
        return out.isEmpty ? "-" : out.joined(separator: ",")
    }

    private func finish(_ why: String) {
        guard !finished else { return }
        finished = true
        log("=== 结束：\(why) ===  订阅了 \(subscribed) 个 notify 特征")
        if packetStats.isEmpty {
            log("没收到任何特征数据")
        } else {
            log("收到的数据长度分布：")
            for (len, cnt) in packetStats.sorted(by: { $0.key < $1.key }) {
                log("  \(len) 字节 × \(cnt)   样例 \(samples[len] ?? "")")
            }
        }
        central?.stopScan()
        if let t = target { central?.cancelPeripheralConnection(t) }
        central = nil
    }

    // MARK: CBCentralManagerDelegate
    func centralManagerDidUpdateState(_ central: CBCentralManager) {
        log("蓝牙状态 = \(central.state.rawValue)")
        guard central.state == .poweredOn else {
            log("不可用（0=未知 1=重置 2=不支持 3=未授权 4=关闭）")
            return
        }
        // 遥控器已配对并连着 Mac 时**不会广播**（BLE 外设一连上就停止 advertising），
        // 扫描永远扫不到它。必须问系统要「已连接的外设」，再在这条连接上读 GATT。
        let uuids = [CBUUID(string: "1812"), CBUUID(string: "180F")]
        var known = central.retrieveConnectedPeripherals(withServices: uuids)
        if known.isEmpty {
            known = central.retrieveConnectedPeripherals(withServices: [])   // 兜底：要全部
        }
        log("系统已连接的外设 \(known.count) 个：")
        for p in known {
            log("   \(p.name ?? "(无名)")  state=\(p.state.rawValue)  id=\(p.identifier.uuidString)")
        }
        if let p = known.first(where: { BLEProbe.isTarget($0.name ?? "") }) {
            target = p
            p.delegate = self
            if p.state == .connected {
                log("→ 遥控器已在系统中连接，直接在这个连接上枚举服务")
                p.discoverServices(nil)
            } else {
                log("→ 连接遥控器")
                central.connect(p, options: nil)
            }
            return
        }
        log("没找到已连接的遥控器，转为扫描（需要它广播）")
        central.scanForPeripherals(withServices: nil, options: nil)
    }

    func centralManager(_ central: CBCentralManager, didDiscover peripheral: CBPeripheral,
                        advertisementData: [String: Any], rssi RSSI: NSNumber) {
        let name = peripheral.name
            ?? (advertisementData[CBAdvertisementDataLocalNameKey] as? String)
            ?? "?"
        if seen.contains(name) { return }
        seen.insert(name)
        let mfg = (advertisementData[CBAdvertisementDataManufacturerDataKey] as? Data)?
            .prefix(8).map { String(format: "%02x", $0) }.joined() ?? ""
        log("发现 \(name)  RSSI \(RSSI)  厂商数据 \(mfg)")
        if BLEProbe.isTarget(name) {
            target = peripheral
            central.stopScan()
            log("→ 是遥控器，连接（会短暂抢占系统 HID 连接）")
            central.connect(peripheral, options: nil)
        }
    }

    func centralManager(_ central: CBCentralManager, didConnect peripheral: CBPeripheral) {
        log("→ 已连接，枚举服务")
        peripheral.delegate = self
        peripheral.discoverServices(nil)
    }

    func centralManager(_ central: CBCentralManager, didFailToConnect peripheral: CBPeripheral,
                        error: Error?) {
        log("→ 连接失败：\(error?.localizedDescription ?? "未知")")
        finish("连接失败")
    }

    func centralManager(_ central: CBCentralManager, didDisconnectPeripheral peripheral: CBPeripheral,
                        error: Error?) {
        log("→ 已断开：\(error?.localizedDescription ?? "正常")")
    }

    // MARK: CBPeripheralDelegate
    func peripheral(_ peripheral: CBPeripheral, didDiscoverServices error: Error?) {
        if let e = error { log("→ 发现服务失败：\(e.localizedDescription)"); return }
        let svcs = peripheral.services ?? []
        log("→ 服务 \(svcs.count) 个：")
        for s in svcs { log("   \(s.uuid.uuidString)") }
        for s in svcs { peripheral.discoverCharacteristics(nil, for: s) }
    }

    func peripheral(_ peripheral: CBPeripheral, didDiscoverCharacteristicsFor service: CBService,
                    error: Error?) {
        let chs = service.characteristics ?? []
        log("   [\(service.uuid.uuidString)] 特征 \(chs.count) 个：")
        for c in chs {
            log("      \(c.uuid.uuidString)   \(propText(c.properties))")
            if c.properties.contains(.notify) || c.properties.contains(.indicate) {
                peripheral.setNotifyValue(true, for: c)
                subscribed += 1
            }
        }
    }

    func peripheral(_ peripheral: CBPeripheral, didUpdateValueFor characteristic: CBCharacteristic,
                    error: Error?) {
        guard let d = characteristic.value else { return }
        let n = d.count
        packetStats[n, default: 0] += 1
        if samples[n] == nil {
            samples[n] = d.prefix(24).map { String(format: "%02x", $0) }.joined()
        }
        if packetStats[n] == 1 || packetStats[n]! % 50 == 0 {
            log("   ← \(n) 字节  \(characteristic.uuid.uuidString)  \(samples[n] ?? "")")
        }
    }

    func peripheral(_ peripheral: CBPeripheral, didUpdateNotificationStateFor characteristic: CBCharacteristic,
                    error: Error?) {
        log("   订阅 \(characteristic.uuid.uuidString) -> \(characteristic.isNotifying ? "成功" : "失败")")
    }
}

final class AppDelegate: NSObject, NSApplicationDelegate, WKNavigationDelegate {
    var item: NSStatusItem!
    var pop = NSPopover()
    var web: WKWebView!
    var server: Process?
    let ble = BLEProbe()

    func applicationDidFinishLaunching(_ n: Notification) {
        NSApp.setActivationPolicy(.accessory)

        // 主程序是它的子进程，TCC 认的是「负责进程」也就是本 App。
        // 不申请的话，合成的键鼠事件会被系统静默丢弃（实测日志：没有辅助功能权限）。
        let opts = [kAXTrustedCheckOptionPrompt.takeUnretainedValue() as String: true] as CFDictionary
        let trusted = AXIsProcessTrustedWithOptions(opts)
        dbg("辅助功能已授权: \(trusted)")

        item = NSStatusBar.system.statusItem(withLength: NSStatusItem.squareLength)
        if let b = item.button {
            b.image = NSImage(systemSymbolName: "av.remote.fill", accessibilityDescription: "Siri Remote")
                ?? NSImage(systemSymbolName: "dot.radiowaves.left.and.right", accessibilityDescription: "Siri Remote")
            b.image?.isTemplate = true
            b.target = self
            b.action = #selector(clicked(_:))
            b.sendAction(on: [.leftMouseUp, .rightMouseUp])
        }

        web = WKWebView(frame: NSRect(x: 0, y: 0, width: 384, height: 562))
        web.navigationDelegate = self
        let vc = NSViewController()
        vc.view = web
        pop.contentViewController = vc
        pop.behavior = .transient
        pop.contentSize = NSSize(width: 384, height: 562)

        dbg("statusItem=\(item != nil) button=\(item.button != nil) visible=\(item.isVisible) len=\(item.length)")
        DispatchQueue.main.asyncAfter(deadline: .now() + 1.0) {
            let f = self.item.button?.window?.frame ?? .zero
            dbg("1s 后 button.window frame=\(f) screen=\(NSScreen.main?.frame ?? .zero)")
        }
        // 调试用：存在这个标记文件就在启动 2 秒后自动跑一次 BLE 探测，省得手动点菜单
        if FileManager.default.fileExists(atPath: "/tmp/ble-autostart") {
            DispatchQueue.main.asyncAfter(deadline: .now() + 2.0) {
                dbg("检测到 /tmp/ble-autostart，自动开始 BLE 探测")
                self.ble.start(seconds: 180)
            }
        }
        ensureServer { self.load() }
        // 看门狗：万一本地服务挂了（历史上被 hid.enumerate 崩过），自动重拉
        Timer.scheduledTimer(withTimeInterval: 5.0, repeats: true) { _ in
            if !portOpen(PORT) {
                dbg("服务无响应，重新拉起")
                self.ensureServer { self.load() }
            }
        }
    }

    func load() {
        web.load(URLRequest(url: BASE.appendingPathComponent("compact.html")))
    }

    // MARK: - 菜单栏交互
    @objc func clicked(_ sender: NSStatusBarButton) {
        if NSApp.currentEvent?.type == .rightMouseUp {
            showMenu()
        } else {
            toggle()
        }
    }

    func toggle() {
        if pop.isShown {
            pop.performClose(nil)
            return
        }
        guard let b = item.button else { return }
        load()                      // 每次打开都刷新状态
        pop.show(relativeTo: b.bounds, of: b, preferredEdge: .minY)
        NSApp.activate(ignoringOtherApps: true)
    }

    func showMenu() {
        let menu = NSMenu()
        menu.addItem(NSMenuItem(title: "打开面板", action: #selector(openPanel), keyEquivalent: ""))
        menu.addItem(NSMenuItem(title: "完整设置（浏览器）", action: #selector(openFull), keyEquivalent: ""))
        menu.addItem(.separator())
        menu.addItem(NSMenuItem(title: "BLE 探测（30 秒，写日志）", action: #selector(runBLE), keyEquivalent: ""))
        menu.addItem(.separator())
        menu.addItem(NSMenuItem(title: "退出", action: #selector(quit), keyEquivalent: "q"))
        for i in menu.items { i.target = self }
        item.menu = menu
        item.button?.performClick(nil)
        item.menu = nil
    }

    @objc func openPanel() { toggle() }

    @objc func runBLE() {
        ble.start(seconds: 30)
    }

    @objc func openFull() {
        NSWorkspace.shared.open(BASE)
    }

    @objc func quit() {
        server?.terminate()
        NSApp.terminate(nil)
    }

    // MARK: - 让 "完整设置" 跳到系统浏览器，而不是在弹窗里加载
    func webView(_ webView: WKWebView, decidePolicyFor a: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        if let u = a.request.url {
            let scheme = (u.scheme ?? "").lowercased()
            if scheme != "http" && scheme != "https" {      // x-apple.systempreferences: 之类
                NSWorkspace.shared.open(u)
                decisionHandler(.cancel)
                return
            }
            if u.path == "/" || u.path.isEmpty {
                NSWorkspace.shared.open(u)
                decisionHandler(.cancel)
                return
            }
        }
        decisionHandler(.allow)
    }

    // MARK: - 拉起本地服务
    func ensureServer(_ done: @escaping () -> Void) {
        if portOpen(PORT) { done(); return }

        let root = (Bundle.main.bundlePath as NSString).deletingLastPathComponent
        let py = root + "/.venv/bin/python"
        let script = root + "/mac/panel/server.py"
        guard FileManager.default.fileExists(atPath: py) else {
            NSLog("找不到 \(py)")
            done()
            return
        }
        let p = Process()
        p.executableURL = URL(fileURLWithPath: py)
        p.arguments = [script]
        p.currentDirectoryURL = URL(fileURLWithPath: root)
        do { try p.run() } catch {
            NSLog("启动服务失败: \(error)")
            done()
            return
        }
        server = p
        DispatchQueue.global().async {
            for _ in 0..<40 {
                if portOpen(PORT) { break }
                usleep(120_000)
            }
            DispatchQueue.main.async { done() }
        }
    }
}

NSSetUncaughtExceptionHandler { e in
    dbg("未捕获异常: \(e.name.rawValue) \(e.reason ?? "")")
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.run()
