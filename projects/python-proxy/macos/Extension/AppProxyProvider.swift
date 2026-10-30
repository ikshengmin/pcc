// TCP relay and transparent-provider structure adapted from ProxyBridge (MIT).
// See third_party/ProxyBridge.LICENSE and macos/README.md for source identity.
import Foundation
import NetworkExtension

final class AppProxyProvider: NETransparentProxyProvider {
    private var config: ProxyConfiguration?
    private let lock = NSLock()
    private struct TCPState {
        let flow: NEAppProxyTCPFlow
        let connection: NWTCPConnection
        var deadline: TimeInterval?
        var clientEOF = false
        var remoteEOF = false
    }
    private var tcp: [ObjectIdentifier: TCPState] = [:]
    private var udp: [ObjectIdentifier: RoutedUDPFlow] = [:]
    private var timer: DispatchSourceTimer?
    private var heartbeat = ProcessInfo.processInfo.systemUptime
    private static let flowLimit = 4096

    override func startProxy(options: [String: Any]?, completionHandler: @escaping (Error?) -> Void) {
        do {
            let values = (protocolConfiguration as? NETunnelProviderProtocol)?.providerConfiguration ?? [:]
            config = try ProxyConfiguration.decode(values)
            heartbeat = ProcessInfo.processInfo.systemUptime
        } catch { completionHandler(error); return }
        let settings = NETransparentProxyNetworkSettings(tunnelRemoteAddress: "127.0.0.1")
        settings.includedNetworkRules = [NENetworkRule(remoteNetwork: nil, remotePrefix: 0,
            localNetwork: nil, localPrefix: 0, protocol: .any, direction: .outbound)]
        // Do not install a global DNS proxy or change any system HTTP proxy setting.
        setTunnelNetworkSettings(settings) { error in
            if error == nil { self.startTimer() }
            completionHandler(error)
        }
    }

    private func startTimer() {
        let timer = DispatchSource.makeTimerSource(queue: DispatchQueue.global(qos: .utility))
        timer.schedule(deadline: .now() + 5, repeating: 5)
        timer.setEventHandler { [weak self] in
            guard let self = self else { return }
            let now = ProcessInfo.processInfo.systemUptime
            self.lock.lock()
            let lostController = now - self.heartbeat > 15
            let expired = self.tcp.filter { $0.value.deadline.map { $0 <= now } ?? false }.map { $0.key }
            self.lock.unlock()
            if lostController {
                self.cancelProxyWithError(NSError(domain: "pproxy.controller", code: 1))
                return
            }
            for key in expired { self.closeTCP(key, error: NSError(domain: "pproxy.handshake", code: 1)) }
        }
        self.timer = timer; timer.resume()
    }

    override func handleAppMessage(_ messageData: Data, completionHandler: ((Data?) -> Void)?) {
        guard messageData == Data("heartbeat".utf8) else { completionHandler?(nil); return }
        lock.lock(); heartbeat = ProcessInfo.processInfo.systemUptime; lock.unlock()
        completionHandler?(Data("ok".utf8))
    }

    override func stopProxy(with reason: NEProviderStopReason, completionHandler: @escaping () -> Void) {
        timer?.cancel(); timer = nil
        lock.lock()
        let tcpKeys = Array(tcp.keys), datagrams = Array(udp.values)
        udp.removeAll()
        lock.unlock()
        for key in tcpKeys { closeTCP(key, error: nil) }
        for item in datagrams { item.stop() }
        config = nil
        completionHandler()
    }

    private func identity(_ flow: NEAppProxyFlow) -> ProcessIdentity? {
        guard let config = config else { return nil }
        let metadata = flow.metaData
        let id = ProcessIdentity.resolve(signingIdentifier: metadata.sourceAppSigningIdentifier,
                                         auditToken: metadata.sourceAppAuditToken)
        // Only the owning proxy process is excluded, not every Python executable.
        if id.pid == config.ownerPID { return nil }
        let ownID = Bundle.main.bundleIdentifier ?? "io.pproxy.process.extension"
        if metadata.sourceAppSigningIdentifier == ownID { return nil }
        return id
    }

    override func handleNewFlow(_ flow: NEAppProxyFlow) -> Bool {
        guard let config = config, let identity = identity(flow) else { return false }
        if let flow = flow as? NEAppProxyTCPFlow {
            guard let endpoint = flow.remoteEndpoint as? NWHostEndpoint, let port = UInt16(endpoint.port) else {
                flow.closeReadWithError(nil); flow.closeWriteWithError(nil); return true
            }
            if endpoint.hostname == config.host && Int(port) == config.port { return false }
            let action = config.rules.action(for: identity, protocol: .tcp, host: endpoint.hostname,
                                             port: port, hostname: flow.remoteHostname)
            switch action {
            case .direct: return false
            case .block: flow.closeReadWithError(nil); flow.closeWriteWithError(nil); return true
            case .proxy: return proxyTCP(flow, endpoint: endpoint, port: port, config: config)
            }
        }
        if let flow = flow as? NEAppProxyUDPFlow { return proxyUDP(flow, config: config, identity: identity, initialHost: nil) }
        return false
    }

    override func handleNewUDPFlow(_ flow: NEAppProxyUDPFlow, initialRemoteEndpoint: NWEndpoint) -> Bool {
        guard let config = config, let identity = identity(flow) else { return false }
        return proxyUDP(flow, config: config, identity: identity,
                        initialHost: (initialRemoteEndpoint as? NWHostEndpoint)?.hostname)
    }

    private func proxyUDP(_ flow: NEAppProxyUDPFlow, config: ProxyConfiguration,
                          identity: ProcessIdentity, initialHost: String?) -> Bool {
        guard config.rules.needsUDPInterception(identity) else { return false }
        let key = ObjectIdentifier(flow)
        lock.lock()
        if udp.count + tcp.count >= Self.flowLimit {
            lock.unlock(); flow.closeReadWithError(nil); flow.closeWriteWithError(nil); return true
        }
        let state = RoutedUDPFlow(flow: flow, provider: self, config: config, identity: identity,
                                  initialHost: initialHost) { [weak self] in
            guard let self = self else { return }
            self.lock.lock(); self.udp.removeValue(forKey: key); self.lock.unlock()
        }
        udp[key] = state
        lock.unlock()
        state.start()
        return true
    }

    private func proxyTCP(_ flow: NEAppProxyTCPFlow, endpoint: NWHostEndpoint, port: UInt16,
                          config: ProxyConfiguration) -> Bool {
        lock.lock()
        guard tcp.count + udp.count < Self.flowLimit else {
            lock.unlock(); flow.closeReadWithError(nil); flow.closeWriteWithError(nil); return true
        }
        let connection = createTCPConnection(to: NWHostEndpoint(hostname: config.host, port: String(config.port)),
                                             enableTLS: false, tlsParameters: nil, delegate: nil)
        let key = ObjectIdentifier(connection)
        tcp[key] = TCPState(flow: flow, connection: connection, deadline: ProcessInfo.processInfo.systemUptime + 20)
        lock.unlock()
        SOCKS5Handshake.run(NEControlStream(connection), command: 1, host: endpoint.hostname, port: port,
                           username: config.username, password: config.password) { [weak self] result in
            guard let self = self else { connection.cancel(); return }
            switch result {
            case .failure(let error): self.closeTCP(key, error: error)
            case .success:
                self.lock.lock()
                let exists = self.tcp[key] != nil
                self.lock.unlock()
                guard exists else { return }
                flow.open(withLocalEndpoint: nil) { error in
                    if let error = error { self.closeTCP(key, error: error); return }
                    self.lock.lock()
                    let exists = self.tcp[key] != nil
                    self.tcp[key]?.deadline = nil
                    self.lock.unlock()
                    guard exists else { return }
                    self.readClient(flow, connection, key)
                    self.readRemote(flow, connection, key)
                }
            }
        }
        return true
    }

    private func closeTCP(_ key: ObjectIdentifier, error: Error?) {
        lock.lock(); let state = tcp.removeValue(forKey: key); lock.unlock()
        guard let state = state else { return }
        state.connection.cancel()
        state.flow.closeReadWithError(error); state.flow.closeWriteWithError(error)
    }

    private func ended(_ key: ObjectIdentifier, client: Bool) {
        lock.lock()
        if client { tcp[key]?.clientEOF = true } else { tcp[key]?.remoteEOF = true }
        let done = tcp[key].map { $0.clientEOF && $0.remoteEOF } ?? false
        lock.unlock()
        if done { closeTCP(key, error: nil) }
    }

    private func readClient(_ flow: NEAppProxyTCPFlow, _ connection: NWTCPConnection, _ key: ObjectIdentifier) {
        flow.readData { [weak self] data, error in
            guard let self = self else { return }
            if let error = error { self.closeTCP(key, error: error); return }
            guard let data = data, !data.isEmpty else {
                connection.writeClose(); self.ended(key, client: true); return
            }
            connection.write(data) { error in
                if let error = error { self.closeTCP(key, error: error) }
                else { self.readClient(flow, connection, key) }
            }
        }
    }

    private func readRemote(_ flow: NEAppProxyTCPFlow, _ connection: NWTCPConnection, _ key: ObjectIdentifier) {
        connection.readMinimumLength(1, maximumLength: 65536) { [weak self] data, error in
            guard let self = self else { return }
            if let error = error { self.closeTCP(key, error: error); return }
            guard let data = data, !data.isEmpty else {
                flow.closeWriteWithError(nil); self.ended(key, client: false); return
            }
            flow.write(data) { error in
                if let error = error { self.closeTCP(key, error: error) }
                else { self.readRemote(flow, connection, key) }
            }
        }
    }
}
