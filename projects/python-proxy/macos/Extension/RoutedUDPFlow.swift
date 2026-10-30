import Foundation
import NetworkExtension

final class NEControlStream: SOCKS5Stream {
    let connection: NWTCPConnection
    init(_ connection: NWTCPConnection) { self.connection = connection }
    func read(_ count: Int, completion: @escaping (Data?, Error?) -> Void) {
        connection.readMinimumLength(count, maximumLength: count, completionHandler: completion)
    }
    func write(_ data: Data, completion: @escaping (Error?) -> Void) { connection.write(data, completionHandler: completion) }
}

// Each flow has a serial queue: the pending proxy handshake, per-destination
// direct sessions, reads, writes, expiry and teardown cannot race each other.
final class RoutedUDPFlow {
    let flow: NEAppProxyUDPFlow
    let config: ProxyConfiguration
    let identity: ProcessIdentity
    weak var provider: NETransparentProxyProvider?
    let finished: () -> Void
    let queue = DispatchQueue(label: "pproxy.udp")
    var control: NWTCPConnection?
    var proxySession: NWUDPSession?
    var directSessions: [String: NWUDPSession] = [:]
    var pending: [Data] = []
    var pendingBytes = 0
    var starting = false
    var closed = false
    var lastActivity = ProcessInfo.processInfo.systemUptime
    var timer: DispatchSourceTimer?
    var initialHost: String?
    var writesInFlight = 0
    var bytesInFlight = 0
    var repliesInFlight = 0
    var replyBytesInFlight = 0

    init(flow: NEAppProxyUDPFlow, provider: NETransparentProxyProvider, config: ProxyConfiguration,
         identity: ProcessIdentity, initialHost: String?, finished: @escaping () -> Void) {
        self.flow = flow; self.provider = provider; self.config = config
        self.identity = identity; self.initialHost = initialHost; self.finished = finished
    }

    func start() {
        flow.open(withLocalEndpoint: nil) { error in
            self.queue.async {
                if let error = error { self.close(error); return }
                guard !self.closed else { return }
                let timer = DispatchSource.makeTimerSource(queue: self.queue)
                timer.schedule(deadline: .now() + 5, repeating: 5)
                timer.setEventHandler { [weak self] in
                    guard let self = self else { return }
                    if ProcessInfo.processInfo.systemUptime - self.lastActivity > self.config.udpIdle { self.close(nil) }
                }
                self.timer = timer; timer.resume()
                self.read()
            }
        }
    }

    func stop() { queue.async { self.close(nil) } }

    func close(_ error: Error?) {
        guard !closed else { return }
        closed = true; timer?.cancel(); timer = nil
        control?.cancel(); control = nil
        for session in Array(directSessions.values) + [proxySession].compactMap({ $0 }) {
            session.setReadHandler({ _, _ in }, maxDatagrams: 1); session.cancel()
        }
        proxySession = nil; directSessions.removeAll(); pending.removeAll(); pendingBytes = 0
        flow.closeReadWithError(error); flow.closeWriteWithError(error)
        finished()
    }

    func read() {
        guard !closed else { return }
        flow.readDatagrams { datagrams, endpoints, error in
            self.queue.async {
                guard !self.closed else { return }
                if let error = error { self.close(error); return }
                guard let datagrams = datagrams, let endpoints = endpoints, !datagrams.isEmpty,
                      datagrams.count == endpoints.count else { self.close(nil); return }
                self.lastActivity = ProcessInfo.processInfo.systemUptime
                for (data, endpoint) in zip(datagrams, endpoints) {
                    guard let endpoint = endpoint as? NWHostEndpoint, let port = UInt16(endpoint.port), port > 0 else { continue }
                    let hint = endpoint.hostname == self.initialHost ? self.flow.remoteHostname : nil
                    let isListener = endpoint.hostname == self.config.host && Int(port) == self.config.port
                    let action = isListener ? FlowAction.direct : self.config.rules.action(
                        for: self.identity, protocol: .udp, host: endpoint.hostname, port: port, hostname: hint)
                    switch action {
                    case .block: continue
                    case .direct: self.sendDirect(data, to: endpoint)
                    case .proxy:
                        guard let packet = SOCKS5Wire.datagram(data, host: endpoint.hostname, port: port) else { continue }
                        self.sendProxy(packet)
                    }
                    if self.closed { return }
                }
                self.read()
            }
        }
    }

    func sendDirect(_ data: Data, to endpoint: NWHostEndpoint) {
        let key = "\(endpoint.hostname):\(endpoint.port)"
        var session = directSessions[key]
        if session == nil {
            guard directSessions.count < 256, let provider = provider else { close(nil); return }
            let created = provider.createUDPSession(to: endpoint, from: nil)
            created.setReadHandler({ [weak self] packets, error in
                guard let self = self else { return }
                self.queue.async {
                    if let error = error { self.close(error); return }
                    for packet in packets ?? [] { self.reply(packet, from: endpoint) }
                }
            }, maxDatagrams: 32)
            directSessions[key] = created; session = created
        }
        if let session = session { write([data], to: session) }
    }

    func sendProxy(_ packet: Data) {
        if let session = proxySession {
            write([packet], to: session)
            return
        }
        guard pending.count < 256, pendingBytes + packet.count <= 1024 * 1024 else { close(nil); return }
        pending.append(packet); pendingBytes += packet.count
        guard !starting, let provider = provider else { return }
        starting = true
        let endpoint = NWHostEndpoint(hostname: config.host, port: String(config.port))
        let connection = provider.createTCPConnection(to: endpoint, enableTLS: false, tlsParameters: nil, delegate: nil)
        control = connection
        queue.asyncAfter(deadline: .now() + 20) {
            if !self.closed && self.starting { self.close(NSError(domain: "pproxy.udp", code: 1)) }
        }
        SOCKS5Handshake.run(NEControlStream(connection), command: 3, host: "0.0.0.0", port: 0,
                           username: config.username, password: config.password) { result in
            self.queue.async {
                guard !self.closed else { return }
                self.starting = false
                switch result {
                case .failure(let error): self.close(error)
                case .success(let (host, port)):
                    guard port > 0, let provider = self.provider else { self.close(nil); return }
                    let relayHost = ["0.0.0.0", "::"].contains(host) ? self.config.host : host
                    let relay = provider.createUDPSession(to: NWHostEndpoint(hostname: relayHost, port: String(port)), from: nil)
                    self.proxySession = relay
                    relay.setReadHandler({ [weak self] packets, error in
                        guard let self = self else { return }
                        self.queue.async {
                            if let error = error { self.close(error); return }
                            for packet in packets ?? [] {
                                if let (data, host, port) = SOCKS5Wire.decodeDatagram(packet) {
                                    self.reply(data, from: NWHostEndpoint(hostname: host, port: String(port)))
                                }
                            }
                        }
                    }, maxDatagrams: 32)
                    let packets = self.pending
                    self.pending.removeAll(); self.pendingBytes = 0
                    self.write(packets, to: relay)
                    // A UDP association ends when its TCP control channel ends.
                    connection.readMinimumLength(1, maximumLength: 1) { _, error in
                        self.queue.async { self.close(error) }
                    }
                }
            }
        }
    }

    func write(_ packets: [Data], to session: NWUDPSession) {
        let bytes = packets.reduce(0) { $0 + $1.count }
        guard !closed, writesInFlight + packets.count <= 256,
              bytesInFlight + bytes <= 1024 * 1024 else { close(nil); return }
        writesInFlight += packets.count; bytesInFlight += bytes
        session.writeMultipleDatagrams(packets) { error in
            self.queue.async {
                self.writesInFlight -= packets.count; self.bytesInFlight -= bytes
                if let error = error { self.close(error) }
            }
        }
    }

    func reply(_ data: Data, from endpoint: NWHostEndpoint) {
        guard !closed else { return }
        guard repliesInFlight < 256, replyBytesInFlight + data.count <= 1024 * 1024 else { close(nil); return }
        repliesInFlight += 1; replyBytesInFlight += data.count
        lastActivity = ProcessInfo.processInfo.systemUptime
        flow.writeDatagrams([data], sentBy: [endpoint]) { error in
            self.queue.async {
                self.repliesInFlight -= 1; self.replyBytesInFlight -= data.count
                if let error = error { self.close(error) }
            }
        }
    }
}
