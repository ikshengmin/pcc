"""Cross-module inlining for the owned runtime archive.

Each runtime module is optimized on its own (phase "ir" of
``build_runtime_archive``), so a call to a small helper that another module
defines keeps its call, frame and argument moves: a list append called the
container lock and unlock of the thread kernel, ``py_err_occurred`` and the
GC's retain, each a few instructions of work behind a full call
(docs/status.md, item 3). LLVM's (Thin)LTO imports such definitions into
their callers' modules before inlining.

Between the phases, ``cross_module_inline_table`` selects small external
definitions that reference no symbol private to their module and use no
construct the copy could not carry (allocas, metadata, exception edges,
indirect branches, variadic or musttail calls). ``import_cross_module_definitions``
gives a module internal copies (``@name.pcc.xinl``) of the selected
functions it calls, directly or through another copy, retargets those
calls, and declares what the copies reference that the module lacks. The
object phase then inlines the copies (``inline-defined-wide``) and cleans
up; a copy the inliner declines stays an internal function. The original
definitions and every address-taken use are unchanged.
"""

from __future__ import annotations

import re

INLINE_SUFFIX = ".pcc.xinl"
# Copies the inliner then sees; its own limit (96) decides the call sites.
MAX_INSTRUCTIONS = 64
# Twice, as the runtime's own list: a copy's calls to other copies are
# inlined in the second round. Slow paths are outlined after this inlining
# (owned_runtime_build defers the module's own outlining to it).
IMPORT_PASSES = (
    "inline-defined-wide,instsimplify,inline-defined-wide,jump-thread,instsimplify,instcombine,"
    "outline-slow-paths,dce"
)

_DEFINE_RE = re.compile(
    r"^define\s+(?P<head>[^@]*?)@(?P<name>[\w.$-]+)\((?P<params>[^)]*)\)(?P<tail>[^{]*)\{\s*$"
)
_DECLARE_RE = re.compile(r"^declare\s+[^@]*@(?P<name>[\w.$-]+)\(")
_GLOBAL_RE = re.compile(r"^@(?P<name>[\w.$-]+)\s*=\s*(?P<rest>.*)$")
_SYMBOL_RE = re.compile(r"@([\w.$-]+)")
_CALL_TARGET_RE = re.compile(r"\bcall\b[^@]*@([\w.$-]+)\(")
_PARAM_NAME_RE = re.compile(r"\s*%[\w.$-]+")
_ATTRIBUTE_GROUP_RE = re.compile(r"\s*#\d+")
# Constructs a copy cannot carry, and intrinsics whose answer depends on
# the frame they run in.
_REJECTED_TOKENS = (
    " alloca ", "landingpad", "invoke ", "musttail", "va_arg", "indirectbr",
    "blockaddress", "callbr", "personality", "...", " !",
    "@llvm.frameaddress", "@llvm.returnaddress", "@llvm.stacksave",
    "@llvm.stackrestore", "@llvm.sponentry",
)
_PRIVATE_LINKAGE = ("internal", "private")


def _split_functions(text: str):
    """(name, define line, body lines) of each definition."""
    lines = text.split("\n")
    index = 0
    while index < len(lines):
        # A prefix test first: pcc1's regular expressions are slow per line.
        match = _DEFINE_RE.match(lines[index]) if lines[index].startswith("define ") else None
        if match is None:
            index += 1
            continue
        end = index + 1
        while end < len(lines) and lines[end] != "}":
            end += 1
        yield match, lines[index + 1:end]
        index = end + 1


def _symbols_defined_privately(text: str) -> set[str]:
    private: set[str] = set()
    for line in text.split("\n"):
        if not line.startswith(("@", "define ")):
            continue
        match = _GLOBAL_RE.match(line)
        if match is not None and match.group("rest").split(" ", 1)[0] in _PRIVATE_LINKAGE:
            private.add(match.group("name"))
            continue
        define = _DEFINE_RE.match(line)
        if define is not None and define.group("head").split(" ", 1)[0] in _PRIVATE_LINKAGE:
            private.add(define.group("name"))
    return private


def _balanced_type(text: str) -> str | None:
    """The type at the start of ``text``: one token, or a bracketed
    aggregate (``[N x T]``, ``{ ... }``, ``<{ ... }>``)."""
    text = text.lstrip()
    if not text:
        return None
    if text[0] not in "[{<":
        return text.split(" ", 1)[0].rstrip(",")
    depth = 0
    for index, character in enumerate(text):
        if character in "[{<":
            depth += 1
        elif character in "]}>":
            depth -= 1
            if depth == 0:
                return text[:index + 1]
    return None


def _global_declaration(name: str, rest: str) -> str | None:
    """An external declaration for ``@name = <rest>`` (a definition or a
    declaration), or None when its form is not understood."""
    tokens = rest.split(" ")
    flags: list[str] = []
    position = 0
    while position < len(tokens) and tokens[position] not in ("global", "constant"):
        token = tokens[position]
        if token in ("external", "dso_local", "local_unnamed_addr", "unnamed_addr", "hidden"):
            pass
        elif token == "thread_local" or token.startswith("thread_local("):
            flags.append(token)
        elif token in ("common", "weak", "linkonce", "linkonce_odr", "weak_odr", "extern_weak"):
            return None
        else:
            return None
        position += 1
    if position >= len(tokens):
        return None
    kind = tokens[position]
    ty = _balanced_type(" ".join(tokens[position + 1:]))
    if ty is None:
        return None
    align = re.search(r",\s*align\s+(\d+)\s*$", rest)
    text = "@" + name + " = external " + " ".join(flags + [kind, ty])
    if align is not None:
        text += ", align " + align.group(1)
    return text


def _function_declaration(match: re.Match) -> str:
    """``declare`` for a parsed ``define`` line: linkage and parameter names
    dropped, types and return type kept."""
    head = match.group("head").split()
    while head and head[0] in ("external", "dso_local", "hidden", "protected",
                               "default", "local_unnamed_addr", "unnamed_addr"):
        head = head[1:]
    params = _PARAM_NAME_RE.sub("", match.group("params"))
    return "declare " + " ".join(head) + " @" + match.group("name") + "(" + params.strip() + ")"


def _module_declarations(text: str) -> dict[str, str]:
    """Declaration text for every symbol ``text`` declares or defines."""
    declarations: dict[str, str] = {}
    for line in text.split("\n"):
        if not line.startswith(("@", "define ", "declare ")):
            continue
        declare = _DECLARE_RE.match(line)
        if declare is not None:
            declarations[declare.group("name")] = _ATTRIBUTE_GROUP_RE.sub("", line.rstrip())
            continue
        define = _DEFINE_RE.match(line)
        if define is not None:
            declarations[define.group("name")] = _function_declaration(define)
            continue
        found = _GLOBAL_RE.match(line)
        if found is not None:
            declared = _global_declaration(found.group("name"), found.group("rest"))
            if declared is not None:
                declarations[found.group("name")] = declared
    return declarations


def _defined_or_declared(text: str) -> set[str]:
    names: set[str] = set()
    for line in text.split("\n"):
        if not line.startswith(("@", "define ", "declare ")):
            continue
        for pattern in (_DECLARE_RE, _DEFINE_RE, _GLOBAL_RE):
            match = pattern.match(line)
            if match is not None:
                names.add(match.group("name"))
                break
    return names


def cross_module_inline_table(modules: dict[str, str]) -> dict[str, dict]:
    """Small external definitions every module may import, by name.

    ``modules`` maps a module name to its optimized IR. An entry holds the
    defining module, the copy's define line and body, and the declarations
    of the symbols the body references.
    """
    table: dict[str, dict] = {}
    for module, text in modules.items():
        private = _symbols_defined_privately(text)
        declarations = None
        for match, body in _split_functions(text):
            name = match.group("name")
            if match.group("head").split(" ", 1)[0] in _PRIVATE_LINKAGE:
                continue
            instructions = [line for line in body if line.strip() and not line.rstrip().endswith(":")]
            if len(instructions) > MAX_INSTRUCTIONS:
                continue
            joined = "\n".join(body)
            if any(token in joined for token in _REJECTED_TOKENS):
                continue
            references = set(_SYMBOL_RE.findall(joined))
            if name in references or references & private:
                continue
            if declarations is None:
                declarations = _module_declarations(text)
            needed: dict[str, str] = {}
            for symbol in sorted(references):
                if symbol not in declarations:
                    needed = None
                    break
                needed[symbol] = declarations[symbol]
            if needed is None:
                continue
            head = match.group("head").split()
            while head and head[0] in ("external", "dso_local", "hidden", "protected",
                                       "default", "local_unnamed_addr", "unnamed_addr"):
                head = head[1:]
            table[name] = {
                "module": module,
                "define": "define internal " + " ".join(head) + " @" + name + INLINE_SUFFIX
                          + "(" + match.group("params") + ") {",
                "body": body,
                "declarations": needed,
            }
    return table


def _retarget_calls(lines: list[str], names: set[str]) -> list[str]:
    out = []
    for line in lines:
        if "call" in line:
            for target in set(_CALL_TARGET_RE.findall(line)) & names:
                line = re.sub(r"@" + re.escape(target) + r"\(",
                              "@" + target + INLINE_SUFFIX + "(", line)
        out.append(line)
    return out


def import_cross_module_definitions(text: str, table: dict[str, dict], module: str) -> tuple[str, int]:
    """``text`` with internal copies of the table's functions it calls.

    Returns the new text and the number of copies (0 leaves ``text``
    unchanged). A module never imports its own definitions.
    """
    own = _defined_or_declared(text)
    defined_here = {match.group("name") for match, _body in _split_functions(text)}
    wanted: list[str] = []
    pending = [target for line in text.split("\n") if "call" in line
               for target in _CALL_TARGET_RE.findall(line)]
    seen: set[str] = set()
    while pending:
        target = pending.pop(0)
        if target in seen:
            continue
        seen.add(target)
        entry = table.get(target)
        if entry is None or entry["module"] == module or target in defined_here:
            continue
        wanted.append(target)
        for line in entry["body"]:
            if "call" in line:
                pending.extend(_CALL_TARGET_RE.findall(line))
    if not wanted:
        return text, 0
    names = set(wanted)
    lines = _retarget_calls(text.split("\n"), names)
    copies: list[str] = []
    declared: set[str] = set(own)
    for name in wanted:
        entry = table[name]
        for symbol, declaration in entry["declarations"].items():
            if symbol not in declared:
                copies.append(declaration)
                declared.add(symbol)
    for name in wanted:
        entry = table[name]
        copies.append("")
        copies.append(entry["define"])
        copies.extend(_retarget_calls(entry["body"], names))
        copies.append("}")
    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines + copies) + "\n", len(wanted)
