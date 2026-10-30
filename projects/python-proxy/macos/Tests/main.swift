// Compiles the production rule engine and SOCKS5 state machine into a CLI peer.
// This does not impersonate an installed Network Extension.
import Foundation
import Darwin

func die(_ message: String) -> Never {
    FileHandle.standardError.write(Data((message + "\n").utf8)); exit(1)
}
func emit(_ value: Any) {
    let data = try! JSONSerialization.data(withJSONObject: value, options: [.sortedKeys])
    print(String(data: data, encoding: .utf8)!); fflush(stdout)
}
final class SocketStream: SOCKS5Stream {
    let fd: Int32
    init(port: Int) {
        fd = socket(AF_INET, SOCK_STREAM, 0)
        var timeout = timeval(tv_sec: 5, tv_usec: 0)
        setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &timeout, socklen_t(MemoryLayout<timeval>.size))
        setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &timeout, socklen_t(MemoryLayout<timeval>.size))
        var one: Int32 = 1
        setsockopt(fd, SOL_SOCKET, SO_NOSIGPIPE, &one, 4)
        var address = sockaddr_in()
        address.sin_len = UInt8(MemoryLayout<sockaddr_in>.size); address.sin_family = sa_family_t(AF_INET)
        address.sin_port = UInt16(port).bigEndian; address.sin_addr.s_addr = inet_addr("127.0.0.1")
        let result = withUnsafePointer(to: &address) {
            $0.withMemoryRebound(to: sockaddr.self, capacity: 1) { connect(fd, $0, socklen_t(MemoryLayout<sockaddr_in>.size)) }
        }
        if result != 0 { die("TCP connect: \(errno)") }
    }
    deinit { close(fd) }
    func read(_ count: Int, completion: @escaping (Data?, Error?) -> Void) {
        var bytes = Data()
        while bytes.count < count {
            var buffer = [UInt8](repeating: 0, count: count - bytes.count)
            let n = recv(fd, &buffer, buffer.count, 0)
            if n <= 0 { completion(nil, NSError(domain: "read", code: Int(errno))); return }
            bytes.append(contentsOf: buffer.prefix(n))
        }
        completion(bytes, nil)
    }
    func write(_ data: Data, completion: @escaping (Error?) -> Void) {
        var offset = 0
        let bytes = Array(data)
        while offset < bytes.count {
            let n = bytes.withUnsafeBytes { send(fd, $0.baseAddress!.advanced(by: offset), bytes.count - offset, 0) }
            if n <= 0 { completion(NSError(domain: "write", code: Int(errno))); return }
            offset += n
        }
        completion(nil)
    }
}

let input = FileHandle.standardInput.readDataToEndOfFile()
guard let object = try JSONSerialization.jsonObject(with: input) as? [String: Any],
      let operation = object["operation"] as? String else { die("missing operation") }
if operation == "rules" {
    let rules = try JSONDecoder().decode(ProcessRules.self, from: JSONSerialization.data(withJSONObject: object["rules"]!))
    let cases = object["cases"] as! [[String: Any]]
    emit(cases.map { item -> [String: Any] in
        let identity = ProcessIdentity(signingIdentifier: item["identifier"] as? String ?? "",
            executable: item["executable"] as? String, applicationIdentifiers: item["applicationIdentifiers"] as? [String] ?? [])
        let proto = FlowProtocol(rawValue: item["protocol"] as? String ?? "tcp")!
        let action = rules.action(for: identity, protocol: proto, host: item["host"] as? String ?? "203.0.113.1",
                                 port: UInt16(item["port"] as? Int ?? 443), hostname: item["hostname"] as? String)
        return ["action": action.rawValue, "udpIntercept": rules.needsUDPInterception(identity)]
    })
} else if operation == "identity" {
    var words = [UInt32](repeating: 0, count: 8)
    words[5] = UInt32(getpid())
    let token = words.withUnsafeBytes { Data($0) }
    let identity = ProcessIdentity.resolve(signingIdentifier: "test.unbundled", auditToken: token)
    emit(["pid": Int(identity.pid ?? 0), "candidates": identity.candidates])
} else if operation == "decode" {
    let packet = Data(base64Encoded: object["packet"] as! String)!
    if let (data, host, port) = SOCKS5Wire.decodeDatagram(packet) {
        emit(["data": data.base64EncodedString(), "host": host, "port": Int(port)])
    } else { emit(["dropped": true]) }
} else {
    let stream = SocketStream(port: object["proxyPort"] as! Int)
    let host = object["host"] as? String ?? "127.0.0.1"
    let port = UInt16(object["port"] as! Int)
    let payload = Data(base64Encoded: object["payload"] as! String)!
    var reply: Result<(String, UInt16), Error>?
    SOCKS5Handshake.run(stream, command: operation == "tcp" ? 1 : 3,
        host: operation == "tcp" ? host : "0.0.0.0", port: operation == "tcp" ? port : 0,
        username: object["username"] as? String, password: object["password"] as? String) { reply = $0 }
    guard let result = reply else { die("handshake did not complete") }
    let (relayHost, relayPort) = try result.get()
    if operation == "tcp" {
        stream.write(payload) { if let error = $0 { die(String(describing: error)) } }
        stream.read(object["replyLength"] as? Int ?? payload.count) { data, error in
            guard let data = data else { die(String(describing: error)) }
            emit(["data": data.base64EncodedString()])
        }
    } else {
        let fd = socket(AF_INET, SOCK_DGRAM, 0)
        var timeout = timeval(tv_sec: 5, tv_usec: 0)
        setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &timeout, socklen_t(MemoryLayout<timeval>.size))
        var address = sockaddr_in()
        address.sin_len = UInt8(MemoryLayout<sockaddr_in>.size); address.sin_family = sa_family_t(AF_INET)
        address.sin_port = relayPort.bigEndian
        address.sin_addr.s_addr = inet_addr(relayHost == "0.0.0.0" ? "127.0.0.1" : relayHost)
        let connected = withUnsafePointer(to: &address) {
            $0.withMemoryRebound(to: sockaddr.self, capacity: 1) { connect(fd, $0, socklen_t(MemoryLayout<sockaddr_in>.size)) }
        }
        if connected != 0 { die("UDP connect: \(errno)") }
        let packet = SOCKS5Wire.datagram(payload, host: host, port: port)!
        let sent = packet.withUnsafeBytes { send(fd, $0.baseAddress!, packet.count, 0) }
        if sent != packet.count { die("UDP send failed") }
        var buffer = [UInt8](repeating: 0, count: 65535)
        let received = recv(fd, &buffer, buffer.count, 0)
        guard received >= 0, let (data, host, port) = SOCKS5Wire.decodeDatagram(Data(buffer.prefix(received))) else {
            die("invalid or missing UDP reply")
        }
        emit(["data": data.base64EncodedString(), "host": host, "port": Int(port)])
        close(fd)
    }
    withExtendedLifetime(stream) {}
}
