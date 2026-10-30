import Foundation
import NetworkExtension
import SystemExtensions
import Darwin

func fail(_ message: String, code: Int32 = 1) -> Never {
    FileHandle.standardError.write(Data(("pproxy process: \(message)\n").utf8))
    exit(code)
}

func say(_ message: String) {
    print(message)
    fflush(stdout)
}

final class Controller: NSObject, OSSystemExtensionRequestDelegate {
    let operation: String
    let config: ProxyConfiguration?
    let extensionID: String
    var manager: NETransparentProxyManager?
    var observer: NSObjectProtocol?
    var signals: [DispatchSourceSignal] = []
    var active = false
    var stopping = false
    var lockFD: Int32 = -1
    var parentTimer: Timer?
    var heartbeatTimer: Timer?
    let parentPID = getppid()
    var ownsManager = false

    init(operation: String, config: ProxyConfiguration?) {
        self.operation = operation
        self.config = config
        self.extensionID = (Bundle.main.bundleIdentifier ?? "io.pproxy.process") + ".extension"
        super.init()
    }

    func run() {
        if operation == "start" {
            let path = NSTemporaryDirectory() + extensionID + ".lock"
            lockFD = open(path, O_CREAT | O_RDWR | O_NOFOLLOW, 0o600)
            guard lockFD >= 0, flock(lockFD, LOCK_EX | LOCK_NB) == 0 else {
                fail("another Process controller is already running")
            }
            for sig in [SIGINT, SIGTERM, SIGHUP] {
                signal(sig, SIG_IGN)
                let source = DispatchSource.makeSignalSource(signal: sig, queue: .main)
                source.setEventHandler { [weak self] in self?.stop(code: 0) }
                source.resume()
                signals.append(source)
            }
            guard config?.ownerPID == parentPID else { fail("configuration owner is not the invoking proxy process") }
            parentTimer = Timer.scheduledTimer(withTimeInterval: 1, repeats: true) { [weak self] _ in
                guard let self = self else { return }
                if getppid() != self.parentPID { self.stop(code: 1) }
            }
            let request = OSSystemExtensionRequest.activationRequest(forExtensionWithIdentifier: extensionID, queue: .main)
            request.delegate = self
            OSSystemExtensionManager.shared.submitRequest(request)
            DispatchQueue.main.asyncAfter(deadline: .now() + 300) { [weak self] in
                guard let self = self, !self.active, !self.stopping else { return }
                self.abort("system extension did not become ready within 300 seconds")
            }
        } else {
            loadManager()
        }
    }

    func request(_ request: OSSystemExtensionRequest, didFinishWithResult result: OSSystemExtensionRequest.Result) {
        if result == .willCompleteAfterReboot { abort("macOS requires a restart to activate the extension"); return }
        loadManager()
    }

    func request(_ request: OSSystemExtensionRequest, didFailWithError error: Error) {
        abort("extension activation failed: \(error.localizedDescription)")
    }

    func requestNeedsUserApproval(_ request: OSSystemExtensionRequest) {
        say("Approve the Python Proxy system extension in macOS System Settings, then allow its network configuration.")
    }

    func request(_ request: OSSystemExtensionRequest, actionForReplacingExtension existing: OSSystemExtensionProperties,
                 withExtension replacement: OSSystemExtensionProperties) -> OSSystemExtensionRequest.ReplacementAction {
        return .replace
    }

    func loadManager() {
        NETransparentProxyManager.loadAllFromPreferences { managers, error in
            DispatchQueue.main.async {
                if let error = error { self.abort(error.localizedDescription); return }
                // Other transparent proxies belong to other applications. Never take
                // the first manager or overwrite an existing ProxyBridge configuration.
                let existing = managers?.first {
                    ($0.protocolConfiguration as? NETunnelProviderProtocol)?.providerBundleIdentifier == self.extensionID
                }
                if self.operation == "status" {
                    let status = existing?.connection.status.rawValue ?? NEVPNStatus.invalid.rawValue
                    say("{\"installed\":\(existing != nil),\"enabled\":\(existing?.isEnabled ?? false),\"status\":\(status)}")
                    exit(0)
                }
                if self.operation == "stop" {
                    self.manager = existing
                    self.ownsManager = existing != nil
                    self.stop(code: 0)
                    return
                }
                guard let config = self.config else { self.abort("missing configuration"); return }
                let manager = existing ?? NETransparentProxyManager()
                self.manager = manager
                let proto = NETunnelProviderProtocol()
                proto.providerBundleIdentifier = self.extensionID
                proto.serverAddress = "python-proxy Process TCP/UDP"
                proto.providerConfiguration = config.dictionary
                manager.protocolConfiguration = proto
                manager.localizedDescription = "Python Proxy — Process TCP/UDP"
                manager.isEnabled = true
                // A live provider must reload configuration by a controlled restart.
                if manager.connection.status == .connected || manager.connection.status == .connecting {
                    self.abort("Process capture is already active; stop it before changing its configuration")
                    return
                }
                self.ownsManager = true
                manager.saveToPreferences { error in
                    if let error = error { self.abort(error.localizedDescription); return }
                    manager.loadFromPreferences { error in
                        DispatchQueue.main.async {
                            if let error = error { self.abort(error.localizedDescription); return }
                            self.startTunnel(manager)
                        }
                    }
                }
            }
        }
    }

    func startTunnel(_ manager: NETransparentProxyManager) {
        guard !stopping, let session = manager.connection as? NETunnelProviderSession else { return }
        observer = NotificationCenter.default.addObserver(forName: .NEVPNStatusDidChange, object: session, queue: .main) { [weak self] _ in
            guard let self = self, !self.stopping else { return }
            if session.status == .connected, !self.active {
                self.active = true
                self.heartbeatTimer = Timer.scheduledTimer(withTimeInterval: 2, repeats: true) { [weak self] _ in
                    guard let self = self, !self.stopping else { return }
                    do {
                        try session.sendProviderMessage(Data("heartbeat".utf8)) { response in
                            if response != Data("ok".utf8) { DispatchQueue.main.async { self.abort("provider heartbeat failed") } }
                        }
                    } catch { self.abort(error.localizedDescription) }
                }
                say("Process TCP/UDP capture active. Press Ctrl+C to stop.")
            } else if self.active && [.disconnected, .invalid].contains(session.status) {
                self.abort("Process capture stopped unexpectedly")
            }
        }
        do { try session.startTunnel() }
        catch { abort(error.localizedDescription) }
    }

    func abort(_ message: String) {
        guard Thread.isMainThread else { DispatchQueue.main.async { self.abort(message) }; return }
        FileHandle.standardError.write(Data(("pproxy process: \(message)\n").utf8))
        stop(code: 1)
    }

    func stop(code: Int32) {
        guard Thread.isMainThread else { DispatchQueue.main.async { self.stop(code: code) }; return }
        guard !stopping else { return }
        stopping = true
        parentTimer?.invalidate()
        heartbeatTimer?.invalidate()
        guard ownsManager, let manager = manager else { exit(code) }
        manager.connection.stopVPNTunnel()
        manager.isEnabled = false
        manager.saveToPreferences { error in
            if let error = error { fail("could not disable Process capture: \(error.localizedDescription)") }
            say("Process capture stopped.")
            exit(code)
        }
        DispatchQueue.main.asyncAfter(deadline: .now() + 10) { fail("timed out disabling Process capture") }
    }
}

let args = Array(CommandLine.arguments.dropFirst())
if args == ["--help"] || args.isEmpty {
    say("Python Proxy Process controller: start (JSON config on stdin), stop, status. Use python -m pproxy --process-rules rules.json.")
    exit(0)
}
guard args.count == 1, ["start", "stop", "status", "check-config"].contains(args[0]) else { fail("invalid command", code: 2) }
var config: ProxyConfiguration?
if args[0] == "start" || args[0] == "check-config" {
    var data = Data()
    while true {
        let chunk = FileHandle.standardInput.readData(ofLength: 16384)
        if chunk.isEmpty { break }
        data.append(chunk)
        guard data.count <= 524288 else { fail("configuration exceeds 512 KiB", code: 2) }
    }
    do {
        let value = try JSONDecoder().decode(ProxyConfiguration.self, from: data)
        try value.validate()
        config = value
    } catch { fail(error.localizedDescription, code: 2) }
    if args[0] == "check-config" { say("Configuration valid: Process TCP/UDP via loopback SOCKS5."); exit(0) }
}
let controller = Controller(operation: args[0], config: config)
controller.run()
RunLoop.main.run()
