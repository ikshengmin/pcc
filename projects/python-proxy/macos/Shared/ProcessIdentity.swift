import Foundation
import Darwin

struct ProcessIdentity {
    let pid: Int32?
    let candidates: [String]

    init(signingIdentifier: String, executable: String?, pid: Int32? = nil,
         applicationIdentifiers: [String] = []) {
        self.pid = pid
        var names = [signingIdentifier] + applicationIdentifiers
        if let executable = executable {
            names.append(executable)
            names.append((executable as NSString).lastPathComponent)
            var parent = URL(fileURLWithPath: executable).deletingLastPathComponent()
            while parent.path != "/" {
                if parent.pathExtension.lowercased() == "app" {
                    names.append(parent.path)
                    names.append(parent.lastPathComponent)
                    names.append(parent.deletingPathExtension().lastPathComponent)
                }
                parent.deleteLastPathComponent()
            }
        }
        candidates = Array(Set(names.filter { !$0.isEmpty }.map { $0.lowercased() }))
    }

    static func resolve(signingIdentifier: String, auditToken: Data?) -> ProcessIdentity {
        var pid: Int32?
        if let token = auditToken, token.count == 32 {
            var words = [UInt32](repeating: 0, count: 8)
            _ = words.withUnsafeMutableBytes { token.copyBytes(to: $0) }
            pid = Int32(bitPattern: words[5])
        }
        var path: String?
        var appIDs: [String] = []
        if let pid = pid, pid > 0 {
            var buffer = [CChar](repeating: 0, count: 4096)
            if proc_pidpath(pid, &buffer, UInt32(buffer.count)) > 0 {
                path = String(cString: buffer)
                var parent = URL(fileURLWithPath: path!).deletingLastPathComponent()
                while parent.path != "/" {
                    if parent.pathExtension.lowercased() == "app",
                       let id = Bundle(path: parent.path)?.bundleIdentifier { appIDs.append(id) }
                    parent.deleteLastPathComponent()
                }
            }
        }
        return ProcessIdentity(signingIdentifier: signingIdentifier, executable: path, pid: pid,
                               applicationIdentifiers: appIDs)
    }
}
