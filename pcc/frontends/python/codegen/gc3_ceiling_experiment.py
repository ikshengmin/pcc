"""Isolated post-render GC3 application-body specialization (not a production pass).

Change only direct calls emitted in ordinary Python bodies. Each body is versioned
in place, so a single guarded entry dominates its entire fast copy, including the
loop backedges. No new call frame contains an unregistered pointer. The original
CFG and ABI remain the fallback. Keep snippets in this file specific to the
owned frontend's current canonical, opaque-pointer, 64-bit rendering. Fail
closed on a shape this experiment does not recognize. No object cache, archive,
ownership ABI, backend selection or code emitter is replaced.
"""

import re

_NAME = r"[-.$A-Za-z_0-9]+"
_VALUE = r"(?:[%@]" + _NAME + r"|null)"
_LOCAL = re.compile(r"%(" + _NAME + r")")
_LABEL = re.compile(r"^(" + _NAME + r"):$")
_DEF = re.compile(r"^  %(" + _NAME + r") = ")
_SIG = re.compile(r"^define (?:external )?(?:ptr|void|i(?:1|8|16|32|64)|double) @(" +
                  _NAME + r")\([^\n]*\) \{$")
# LLVM identifiers can contain dots/dashes: an SSA result named
# %import.method.text.invoke.164.52 is an ordinary name, not an EH edge.
# Genuine terminators, EH nodes and asm must still reject the complete body.
_BANNED = re.compile(r'blockaddress\s*\(|(?<![-.$%@A-Za-z0-9_])(?:invoke|callbr|'
                     r'indirectbr|landingpad|resume|catchswitch|catchret|cleanupret|'
                     r'musttail|asm|personality|catchpad|cleanuppad)(?![-.$A-Za-z0-9_])|[%@]"|'
                     r'\bcall\b[^\n@]*@(?:Py[A-Z_]|py_cpy_|pcc_capi_)')
_ROOT = re.compile(r"  call void \(ptr, ptr\) @pcc_gc_store_root(?:_take)?(?:_known)?"
                   r"\(ptr (" + _VALUE + r"), ptr (" + _VALUE + r")\)")
_RELEASE = re.compile(r"  call void \(ptr\) @pcc_gc_release(?:_known)?\(ptr ("+_VALUE+r")\)")
_RETAIN = re.compile(r"  (%"+_NAME+r") = call ptr \(ptr\) @pcc_gc_retain(?:_known)?\(ptr ("+_VALUE+r")\)")
_PIN = re.compile(r"  call void \(ptr\) @pcc_gc_(?:pin|unpin)\(ptr ("+_VALUE+r")\)")
_ZERO_LEASE = re.compile(r"  (%"+_NAME+r") = call i64 \(ptr, i64\) @pcc_gc_foreign_lease_release"
                         r"\(ptr "+_VALUE+r", i64 0\)")
_TAGGED = re.compile(r"  (%"+_NAME+r") = inttoptr i64 (-?\d+) to ptr")
_IMAGE = frozenset(("@py_none_storage", "@py_true_storage", "@py_false_storage",
                    "@py_notimplemented_storage", "@py_ellipsis_storage"))
_GUARD = (
    ("pcc_gc_backend_selected", 3),
    ("pcc_gc_backend4_no_refcounts", 1),
    ("pcc_gc_refcount_provenance_probe", 0),
    ("pcc_diagnostics_runtime_log_fast_state", 0),
)


def rewrite_instruction(line, root_symbol, tags, statistics):
    def changed(key, replacement):
        if isinstance(statistics, dict):
            statistics[key] = statistics.get(key, 0) + 1
        return replacement

    match = _ROOT.fullmatch(line)
    if match:
        # Keep a real rooted address, the write, the marking barrier and the
        # sharded store count. This isn't a generic heap-field store.
        return changed("root", "  call void (ptr, ptr) @"+root_symbol+"(ptr "+
                       match[1]+", ptr "+match[2]+")")
    if _RELEASE.fullmatch(line):
        return changed("release", "")
    match = _RETAIN.fullmatch(line)
    if match:
        return changed("retain", "  "+match[1]+" = select i1 true, ptr "+match[2]+", ptr null")
    match = _PIN.fullmatch(line)
    if match and (match[1] == "null" or match[1] in _IMAGE or match[1] in tags):
        return changed("immediate-pin", "")
    match = _ZERO_LEASE.fullmatch(line)
    if match:
        # A literal zero is not ownership of any outstanding lease.
        return changed("zero-lease", "  "+match[1]+" = add i64 0, 0")
    return line


def _version(lines, root_symbol):
    if not lines or not _LABEL.fullmatch(lines[0]):
        return lines, {}
    body = "\n".join(lines)
    if "exp.gc3." in body or _BANNED.search(body) or " !dbg " in body:
        return lines, {}
    labels = {m[1] for line in lines if (m := _LABEL.fullmatch(line))}
    definitions = {m[1] for line in lines if (m := _DEF.match(line))}
    # Emit neither a new predecessor of a phi entry nor a body where part of
    # the input grammar is unknown.
    if any(line.strip() and not line.startswith("  ") and not _LABEL.fullmatch(line)
           for line in lines):
        return lines, {}
    if any(" = phi " in s for s in lines[1:next((i for i, s in
           enumerate(lines[1:], 1) if _LABEL.fullmatch(s)), len(lines))]):
        return lines, {}
    if any(x.startswith("exp.gc3.") for x in labels | definitions):
        return lines, {}
    tags = {m[1] for s in lines if (m := _TAGGED.fullmatch(s)) and int(m[2]) & 1}
    statistics = {}
    changed = [rewrite_instruction(s, root_symbol, tags, statistics) for s in lines]
    if not statistics:
        return lines, {}
    original_entry = lines[0][:-1]
    names = labels | definitions
    def rename(match):
        return "%exp.gc3.fast."+match[1] if match[1] in names else match[0]
    clone = [
        "exp.gc3.fast."+s if _LABEL.fullmatch(s) else _LOCAL.sub(rename, s)
        for s in changed if s
    ]
    prefix = [
        "exp.gc3.entry:",
        "  %exp.gc3.freeze = load atomic i32, ptr @pcc_gc_backend_frozen acquire, align 4",
        "  %exp.gc3.frozen = icmp ne i32 %exp.gc3.freeze, 0",
        "  br i1 %exp.gc3.frozen, label %exp.gc3.config, label %"+original_entry,
        "exp.gc3.config:",
    ]
    last = None
    for i, (field, expects) in enumerate(_GUARD):
        val, ok = "%exp.gc3.v"+str(i), "%exp.gc3.ok"+str(i)
        prefix += ["  "+val+" = load atomic i32, ptr @"+field+" acquire, align 4",
                   "  "+ok+" = icmp eq i32 "+val+", "+str(expects)]
        if last:
            combined = "%exp.gc3.and"+str(i)
            prefix.append("  "+combined+" = and i1 "+last+", "+ok)
            last = combined
        else:
            last = ok
    prefix.append("  br i1 "+last+", label %exp.gc3.fast."+original_entry+", label %"+original_entry)
    return prefix+lines+clone, statistics


def _root_helper(symbol):
    # py_obj._store_root_nonmoving_tracing(slot,value,3): the mark phase can
    # transition only at a stop. Precisely zero calls intervene between the
    # store, the phase read and the branch. The freestanding public shade
    # owner itself enters a nonparking scope before any polled operation.
    return """define internal void @SYMBOL(ptr %slot, ptr %value) {
entry:
  %empty = icmp eq ptr %slot, null
  br i1 %empty, label %done, label %publish
publish:
  store ptr %value, ptr %slot
  %null = icmp eq ptr %value, null
  %word = ptrtoint ptr %value to i64
  %tagged = trunc i64 %word to i1
  %imm = or i1 %null, %tagged
  br i1 %imm, label %note, label %phase
phase:
  %active = load atomic i32, ptr @pcc_gc_mark_active acquire, align 4
  %mark = icmp ne i32 %active, 0
  br i1 %mark, label %shade, label %note
shade:
  call void (ptr) @pcc_gc_count_free_barrier_shade(ptr %value)
  br label %note
note:
  call void () @pcc_gc_note_store()
  br label %done
done:
  ret void
}""".replace("SYMBOL", symbol)


def specialize(text, owned, module_name):
    if not re.fullmatch(_NAME, module_name) or not owned:
        return text
    # Complete defs only; a direct-only/native IR representation is not
    # silently routed through a different backend for this experiment.
    symbol = "pcc_exp_gc3_root_"+module_name
    if "@"+symbol in text or "exp.gc3.entry:" in text:
        return text
    extra = {
        ("@"+g): "@"+g+" = external global i32"
        for g in ["pcc_gc_backend_frozen", "pcc_gc_mark_active"]+[p[0] for p in _GUARD]
    }
    extra["@pcc_gc_count_free_barrier_shade"] = "declare external void @pcc_gc_count_free_barrier_shade(ptr)"
    extra["@pcc_gc_note_store"] = "declare external void @pcc_gc_note_store()"
    lines = text.splitlines()
    out = []
    i, any_change = 0, False
    while i < len(lines):
        line = lines[i]
        out.append(line)
        sig = _SIG.fullmatch(line)
        i += 1
        if not sig or sig[1] not in owned:
            continue
        j = i
        while j < len(lines) and lines[j] != "}":
            j += 1
        if j == len(lines):
            return text
        version, changes = _version(lines[i:j], symbol)
        out.extend(version)
        any_change |= bool(changes)
        i = j
    if not any_change:
        return text
    for name, decl in extra.items():
        # A differently typed existing declaration cannot be borrowed.
        if re.search(r"(?m)^(?:(?:declare|define)[^\n@]* )?"+re.escape(name)+r"(?:\s*=|\()", text):
            if decl not in lines:
                return text
        else:
            out.append(decl)
    out.append(_root_helper(symbol))
    return "\n".join(out)+"\n"
