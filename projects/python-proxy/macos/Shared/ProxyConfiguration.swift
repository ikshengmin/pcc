import Foundation

struct ProxyConfiguration: Codable {
    let host: String
    let port: Int
    let username: String?
    let password: String?
    let rules: ProcessRules
    let ownerPID: Int32
    let udpIdle: Double

    func validate() throws {
        let bytes = ProcessRule.address(host)
        let loopback = bytes.map { ($0.count == 4 && $0[0] == 127) ||
            ($0.count == 16 && $0.dropLast().allSatisfy { $0 == 0 } && $0.last == 1) } ?? false
        guard loopback, (1...65535).contains(port) else {
            throw NSError(domain: "pproxy", code: 1, userInfo: [NSLocalizedDescriptionKey:
                "Process capture requires a loopback SOCKS5 listener and a port between 1 and 65535."])
        }
        guard ownerPID > 1, udpIdle.isFinite, udpIdle > 0, rules.rules.count <= 1024 else {
            throw NSError(domain: "pproxy", code: 3, userInfo: [NSLocalizedDescriptionKey: "Invalid capture owner or limits."])
        }
        for rule in rules.rules {
            guard !rule.processes.isEmpty, !rule.hosts.isEmpty, !rule.ports.isEmpty,
                  rule.processes.count <= 256, rule.hosts.count <= 256, rule.ports.count <= 256,
                  (rule.processes + rule.hosts + rule.ports).allSatisfy({ !$0.isEmpty && $0.utf8.count <= 1024 }) else {
                throw NSError(domain: "pproxy", code: 4, userInfo: [NSLocalizedDescriptionKey: "Empty or oversized rule pattern."])
            }
            for port in rule.ports where port != "*" {
                let parts = port.split(separator: "-", omittingEmptySubsequences: false)
                guard (1...2).contains(parts.count), let lo = UInt16(parts[0]), let hi = UInt16(parts.last!),
                      lo > 0, lo <= hi else {
                    throw NSError(domain: "pproxy", code: 5, userInfo: [NSLocalizedDescriptionKey: "Invalid rule port range."])
                }
            }
        }
        guard (username == nil) == (password == nil),
              (username?.utf8.count ?? 0) <= 255, (password?.utf8.count ?? 0) <= 255 else {
            throw NSError(domain: "pproxy", code: 2, userInfo: [NSLocalizedDescriptionKey: "Invalid SOCKS5 credentials."])
        }
    }

    var dictionary: [String: Any] {
        // The validated configuration consists only of JSON-compatible values.
        return (try? JSONSerialization.jsonObject(with: JSONEncoder().encode(self))) as? [String: Any] ?? [:]
    }

    static func decode(_ dictionary: [String: Any]) throws -> ProxyConfiguration {
        let result = try JSONDecoder().decode(Self.self, from: JSONSerialization.data(withJSONObject: dictionary))
        try result.validate()
        return result
    }
}
