import Foundation
import Network

enum SOCKS5Wire {
    static func address(_ host: String, _ port: UInt16) -> Data? {
        var result: Data
        if let ip = IPv4Address(host) { result = Data([1]) + ip.rawValue }
        else if let ip = IPv6Address(host) { result = Data([4]) + ip.rawValue }
        else {
            let name = Data(host.utf8)
            guard !name.isEmpty, name.count <= 255 else { return nil }
            result = Data([3, UInt8(name.count)]) + name
        }
        result.append(contentsOf: [UInt8(port >> 8), UInt8(port & 255)])
        return result
    }

    static func decodeAddress(_ bytes: Data) -> (String, UInt16, Int)? {
        let data = Array(bytes)
        guard !data.isEmpty else { return nil }
        let host: String
        let end: Int
        switch data[0] {
        case 1:
            end = 5
            guard data.count >= 7, let ip = IPv4Address(Data(data[1..<5])) else { return nil }
            host = ip.debugDescription
        case 4:
            end = 17
            guard data.count >= 19, let ip = IPv6Address(Data(data[1..<17])) else { return nil }
            host = ip.debugDescription
        case 3:
            guard data.count >= 2, data[1] > 0 else { return nil }
            end = 2 + Int(data[1])
            guard data.count >= end + 2, let name = String(data: Data(data[2..<end]), encoding: .utf8) else { return nil }
            host = name
        default: return nil
        }
        return (host, UInt16(data[end]) << 8 | UInt16(data[end + 1]), end + 2)
    }

    static func datagram(_ payload: Data, host: String, port: UInt16) -> Data? {
        guard let address = address(host, port), payload.count + address.count + 3 <= 65507 else { return nil }
        return Data([0, 0, 0]) + address + payload
    }

    static func decodeDatagram(_ packet: Data) -> (Data, String, UInt16)? {
        guard packet.count >= 4, packet.prefix(3) == Data([0, 0, 0]),
              let (host, port, count) = decodeAddress(Data(packet.dropFirst(3))), port > 0 else { return nil }
        return (Data(packet.dropFirst(3 + count)), host, port)
    }
}

protocol SOCKS5Stream: AnyObject {
    func read(_ count: Int, completion: @escaping (Data?, Error?) -> Void)
    func write(_ data: Data, completion: @escaping (Error?) -> Void)
}

// Exact reads preserve application bytes which may follow a CONNECT reply.
// This state machine is shared by the extension and the loopback wire harness.
enum SOCKS5Handshake {
    static func run(_ stream: SOCKS5Stream, command: UInt8, host: String, port: UInt16,
                    username: String?, password: String?, completion: @escaping (Result<(String, UInt16), Error>) -> Void) {
        func failed(_ text: String) {
            completion(.failure(NSError(domain: "pproxy.socks5", code: 1,
                                        userInfo: [NSLocalizedDescriptionKey: text])))
        }
        func read(_ n: Int, _ next: @escaping (Data) -> Void) {
            stream.read(n) { data, error in
                if let error = error { completion(.failure(error)); return }
                guard let data = data, data.count == n else { failed("short SOCKS5 reply"); return }
                next(data)
            }
        }
        func write(_ data: Data, _ next: @escaping () -> Void) {
            stream.write(data) { error in
                if let error = error { completion(.failure(error)) } else { next() }
            }
        }
        func request() {
            guard let address = SOCKS5Wire.address(host, port) else { failed("invalid SOCKS5 destination"); return }
            write(Data([5, command, 0]) + address) {
                read(4) { header in
                    guard header[0] == 5, header[1] == 0, header[2] == 0 else { failed("SOCKS5 request rejected"); return }
                    func finish(_ address: Data) {
                        guard let (host, port, _) = SOCKS5Wire.decodeAddress(address) else { failed("invalid SOCKS5 address"); return }
                        completion(.success((host, port)))
                    }
                    switch header[3] {
                    case 1: read(6) { finish(Data([1]) + $0) }
                    case 4: read(18) { finish(Data([4]) + $0) }
                    case 3: read(1) { size in
                        guard size[0] > 0 else { failed("empty SOCKS5 domain"); return }
                        read(Int(size[0]) + 2) { finish(Data([3, size[0]]) + $0) }
                    }
                    default: failed("unsupported SOCKS5 address type")
                    }
                }
            }
        }
        // Supplying credentials requires authentication; never accept a downgrade.
        let method: UInt8 = username == nil ? 0 : 2
        write(Data([5, 1, method])) {
            read(2) { greeting in
                guard greeting == Data([5, method]) else { failed("SOCKS5 authentication method rejected"); return }
                if method == 0 { request(); return }
                let user = Data((username ?? "").utf8), pass = Data((password ?? "").utf8)
                guard user.count <= 255, pass.count <= 255 else { failed("SOCKS5 credentials too long"); return }
                write(Data([1, UInt8(user.count)]) + user + Data([UInt8(pass.count)]) + pass) {
                    read(2) { auth in
                        guard auth == Data([1, 0]) else { failed("SOCKS5 authentication rejected"); return }
                        request()
                    }
                }
            }
        }
    }
}
