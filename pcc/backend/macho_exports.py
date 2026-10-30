"""Shared Mach-O dyld export-trie encoding, independent of final image kind."""

from . import BackendUnavailable


def _uleb(value):
    if value < 0:
        raise ValueError("negative Mach-O export value")
    result = bytearray()
    while value >= 128:
        result.append((value & 127) | 128)
        value >>= 7
    result.append(value)
    return bytes(result)


def build_exports_trie(exports):
    # Radix edges keep arbitrarily long symbol names out of the recursion
    # stack. Node offsets use a small monotone fixed-point calculation.
    nodes = [{"terminal": None, "children": []}]
    pending = [(0, sorted(exports))]
    while pending:
        index, rows = pending.pop()
        groups = {}
        for name, address, flags in rows:
            if not name:
                nodes[index]["terminal"] = _uleb(flags) + _uleb(address)
            else:
                groups.setdefault(name[0], []).append((name, address, flags))
        for key in sorted(groups):
            group = groups[key]
            first, last = group[0][0], group[-1][0]
            length = 0
            while length < min(len(first), len(last)) and first[length] == last[length]:
                length += 1
            child = len(nodes)
            nodes.append({"terminal": None, "children": []})
            nodes[index]["children"].append((first[:length], child))
            pending.append((child, [(name[length:], address, flags) for name, address, flags in group]))
    offsets = [0] * len(nodes)
    while True:
        encoded = []
        for node in nodes:
            terminal = node["terminal"] or b""
            children = node["children"]
            if len(children) > 255:
                raise BackendUnavailable("Mach-O export node exceeds the dyld child limit")
            record = _uleb(len(terminal)) + terminal + bytes([len(children)])
            for edge, child in children:
                record += edge + b"\0" + _uleb(offsets[child])
            encoded.append(record)
        current = 0
        updated = []
        for record in encoded:
            updated.append(current)
            current += len(record)
        if updated == offsets:
            return b"".join(encoded)
        offsets = updated
