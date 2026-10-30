// Wildcard and address matching adapted from ProxyBridge (MIT).
import Foundation
import Network

enum FlowAction: String, Codable { case proxy, direct, block }
enum FlowProtocol: String, Codable { case tcp, udp, both }

struct ProcessRule: Codable {
    let name: String?
    let processes: [String]
    let hosts: [String]
    let ports: [String]
    let transport: FlowProtocol
    let action: FlowAction
    let enabled: Bool

    enum CodingKeys: String, CodingKey {
        case name, processes, hosts, ports, action, enabled
        case transport = "protocol"
    }

    func matches(_ identity: ProcessIdentity, _ proto: FlowProtocol) -> Bool {
        enabled && (transport == .both || transport == proto) &&
            processes.contains { pattern in identity.candidates.contains { Self.glob(pattern, $0) } || pattern == "*" }
    }

    func matches(host: String, port: UInt16, hostname: String?) -> Bool {
        hosts.contains { Self.host($0, matches: host, hostname: hostname) } && ports.contains { item in
            if item == "*" { return true }
            let parts = item.split(separator: "-")
            if parts.count == 1 { return UInt16(parts[0]) == port }
            guard parts.count == 2, let lo = UInt16(parts[0]), let hi = UInt16(parts[1]) else { return false }
            return lo <= port && port <= hi
        }
    }

    static func glob(_ pattern: String, _ text: String) -> Bool {
        let p = Array(pattern.lowercased()), t = Array(text.lowercased())
        var pi = 0, ti = 0, star = -1, mark = 0
        while ti < t.count {
            if pi < p.count && (p[pi] == t[ti] || p[pi] == "?") { pi += 1; ti += 1 }
            else if pi < p.count && p[pi] == "*" { star = pi; mark = ti; pi += 1 }
            else if star >= 0 { pi = star + 1; mark += 1; ti = mark }
            else { return false }
        }
        while pi < p.count && p[pi] == "*" { pi += 1 }
        return pi == p.count
    }

    static func address(_ value: String) -> [UInt8]? {
        if let v4 = IPv4Address(value) { return Array(v4.rawValue) }
        if let v6 = IPv6Address(value) { return Array(v6.rawValue) }
        return nil
    }

    static func host(_ pattern: String, matches target: String, hostname: String?) -> Bool {
        if pattern == "*" { return true }
        if pattern.contains("/") {
            let parts = pattern.split(separator: "/")
            guard parts.count == 2, let prefix = Int(parts[1]), let a = address(String(parts[0])),
                  let b = address(target), a.count == b.count, prefix >= 0, prefix <= a.count * 8 else { return false }
            for i in 0..<a.count {
                let bits = min(8, max(0, prefix - i * 8))
                let mask = bits == 0 ? UInt8(0) : UInt8(0xff << (8 - bits) & 0xff)
                if a[i] & mask != b[i] & mask { return false }
            }
            return true
        }
        let range = pattern.split(separator: "-", maxSplits: 1)
        if range.count == 2, let lo = address(String(range[0])), let hi = address(String(range[1])),
           let dst = address(target), lo.count == dst.count, hi.count == dst.count {
            return !dst.lexicographicallyPrecedes(lo) && !hi.lexicographicallyPrecedes(dst)
        }
        if let a = address(pattern), let b = address(target) { return a == b }
        for value in [target, hostname ?? ""] where !value.isEmpty {
            if glob(pattern, value) { return true }
            if pattern.hasPrefix("*."), value.lowercased() == String(pattern.dropFirst(2)).lowercased() { return true }
        }
        return false
    }
}

struct ProcessRules: Codable {
    let rules: [ProcessRule]
    let fallback: FlowAction
    enum CodingKeys: String, CodingKey { case rules; case fallback = "default" }

    func action(for identity: ProcessIdentity, protocol proto: FlowProtocol, host: String, port: UInt16,
                hostname: String? = nil) -> FlowAction {
        for rule in rules where rule.matches(identity, proto) && rule.matches(host: host, port: port, hostname: hostname) {
            return rule.action
        }
        return fallback
    }

    // UDP may send to several destinations on one flow. Never release that flow
    // based only on its first endpoint if a later datagram could hit another rule.
    func needsUDPInterception(_ identity: ProcessIdentity) -> Bool {
        if fallback != .direct { return true }
        return rules.contains { $0.action != .direct && $0.matches(identity, .udp) }
    }
}
