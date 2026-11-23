"""Find the runtime functions nothing reaches once the collector is fixed.

Nodes are the top-level functions of pcc/runtime/py. A function reaches the
module-local functions it names, the exported functions it calls through an
``extern`` binding, and the targets of ``function_addr``. Roots are the
functions whose exported symbol is named outside the runtime directory
(code generation, the backends, the driver, C headers, scripts, benchmarks),
plus whatever a module's own top-level code names. The registry tables of
runtime_abi.py are declarations, not uses.

    sweep_one_collector.py report            what is unreachable, per module
    sweep_one_collector.py apply             delete it (functions, externs)
"""
from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
(REPO / "build" / "one-collector").mkdir(parents=True, exist_ok=True)
RUNTIME = REPO / "pcc" / "runtime" / "py"
ABI = REPO / "pcc" / "frontends" / "python" / "codegen" / "runtime_abi.py"
IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def call_name(node):
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        return node.func.id
    return None


def head_source(path: Path):
    """The committed text of ``path``, or None when HEAD has no such file."""
    import subprocess
    relative = str(path.relative_to(REPO))
    result = subprocess.run(["git", "-C", str(REPO), "show", "HEAD:" + relative],
                            capture_output=True, text=True)
    return result.stdout if result.returncode == 0 else None


class Module:
    def __init__(self, path: Path, source=None):
        self.path = path
        self.source = path.read_text(encoding="utf-8") if source is None else source
        self.tree = ast.parse(self.source)
        self.functions = {}      # name -> FunctionDef
        self.exports = {}        # symbol -> function name
        self.externs = {}        # binding name -> symbol
        self.extern_nodes = {}   # binding name -> Assign
        self.top_names = set()   # names loaded by other top-level code
        self.top_addresses = set()
        for statement in self.tree.body:
            if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self.functions[statement.name] = statement
                for decorator in statement.decorator_list:
                    if call_name(decorator) in ("c_abi_export", "c_abi_typed_export") and decorator.args:
                        first = decorator.args[0]
                        if isinstance(first, ast.Constant) and isinstance(first.value, str):
                            self.exports[first.value] = statement.name
                continue
            if (isinstance(statement, ast.Assign) and len(statement.targets) == 1
                    and isinstance(statement.targets[0], ast.Name)
                    and call_name(statement.value) == "extern" and statement.value.args
                    and isinstance(statement.value.args[0], ast.Constant)):
                self.externs[statement.targets[0].id] = statement.value.args[0].value
                self.extern_nodes[statement.targets[0].id] = statement
                continue
            for node in ast.walk(statement):
                if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                    self.top_names.add(node.id)
                if call_name(node) == "function_addr" and node.args and isinstance(node.args[0], ast.Constant):
                    self.top_addresses.add(node.args[0].value)

    def references(self, function):
        names = set()
        addresses = set()
        for node in ast.walk(function):
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                names.add(node.id)
            if call_name(node) == "function_addr" and node.args and isinstance(node.args[0], ast.Constant):
                addresses.add(node.args[0].value)
        return names, addresses


def registry_only_names() -> set:
    """Identifiers of runtime_abi.py that appear only as keys of its
    signature tables."""
    source = ABI.read_text(encoding="utf-8")
    tree = ast.parse(source)
    key_positions = set()
    keys = set()
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and (
                node.name.startswith("_runtime_signatures_part_")
                or node.name.startswith("_cross_object_signatures_part_")):
            for inner in ast.walk(node):
                if isinstance(inner, ast.Dict):
                    for key in inner.keys:
                        if isinstance(key, ast.Constant) and isinstance(key.value, str):
                            keys.add(key.value)
                            key_positions.add((key.lineno, key.col_offset))
    elsewhere = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if (node.lineno, node.col_offset) not in key_positions:
                elsewhere.update(IDENT.findall(node.value))
        elif isinstance(node, ast.Name):
            elsewhere.add(node.id)
    return keys - elsewhere


# Modules of collectors 1-4 and their hub. A C header, the ABI tables, a
# benchmark, a script or the tracing-pause diagnostic naming one of their
# symbols declares or measures it; only compiler sources use it.
TARGETS = {"py_gc_backend.py"} | {
    "freestanding_gc_" + name + ".py" for name in """alloc_stage barrier_dispatcher
    callback_layers common_mark_cycle conservative_roots forwarding_identity
    forwarding_retirement generational_oldification generational_promotion
    generational_scheduler gray_worklist incremental_concurrent_scheduler
    mapped_roots object_nodes object_root_seeding page_marks relocation_copy
    relocation_drain relocation_payload relocation_remap relocation_selector
    scalar_sweep sweep_slots tracing_sweep_collector zpage_allocation
    zpage_lifecycle zpage_mechanics""".split()
}


# A symbol with one of these in its name is a concept of collectors 1-4.
# A header or an ABI table naming it declares it; only a compiler source or
# a live caller keeps it.
FORMER = re.compile(
    r"backend[1-4]|gc[1-4]_|tracing|_cms_|zpage|reloc|forward|evacuat|remember|"
    r"store_buffer|gray|page_mark|mark_color|mark_active|scalar_sweep|sweep_owed|"
    r"concurrent_sweep|young_cut|closed_young|oldif|promot|nonmoving|count_free|"
    r"alloc_stage|object_node|object_index|object_walk|conservative|debt|stepmul|"
    r"pcc_gc_step|set_backend|callback_layers|trace_stack|read_barrier|genzgc|"
    r"minor_heap|minor_alloc|black|collect_tracing|seed_roots|claimed|"
    r"extension_root|remap|_heal_|drain_all|closed_young"
)


# Exported for callers outside these sources (the C library's name for an
# allocation's usable size): never swept.
KEEP = {"malloc_usable_size", "pcc_io_waitset_backend_label"}


def former_symbol(symbol: str) -> bool:
    # Only the collector's own symbols: the same words mean other things
    # elsewhere (an ELF relocation, a socket's backend).
    if not symbol.startswith(("pcc_gc_", "py_gc_")):
        return False
    return "backend0" not in symbol and FORMER.search(symbol) is not None


def compiler_identifiers() -> set:
    """Identifiers of the sources that emit or link runtime calls."""
    found = set()
    for path in (REPO / "pcc").rglob("*.py"):
        parts = path.relative_to(REPO).parts
        if "build_py" in parts or "build_owned" in parts or "__pycache__" in parts:
            continue
        if path.parent == RUNTIME or path == ABI:
            continue
        if parts[:2] == ("pcc", "diagnostics") and path.name == "gc_pause_probe.py":
            continue
        found.update(IDENT.findall(path.read_text(encoding="utf-8", errors="replace")))
    return found


def outside_identifiers() -> set:
    """Every identifier in the sources that may name a runtime symbol from
    outside the runtime directory."""
    found = set()
    roots = [REPO / "pcc", REPO / "scripts", REPO / "benchmarks", REPO / "utils"]
    for root in roots:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if not path.is_file() or path.suffix not in (".py", ".h", ".c", ".S", ".s", ".in", ".txt", ".json", ".toml"):
                continue
            parts = path.relative_to(REPO).parts
            if "build_py" in parts or "build_owned" in parts or "__pycache__" in parts:
                continue
            if path.parent == RUNTIME:
                continue
            if path.suffix == ".json" and "provenance" in path.name:
                continue
            if path == ABI:
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            found.update(IDENT.findall(text))
    registry_only = registry_only_names()
    abi_text = ABI.read_text(encoding="utf-8")
    for name in set(IDENT.findall(abi_text)):
        if name not in registry_only:
            found.add(name)
    return found


def analyse(committed: bool = False, refined: bool = True):
    modules = {}
    for path in sorted(RUNTIME.glob("*.py")):
        source = head_source(path) if committed else None
        if committed and source is None:
            continue
        modules[path.name] = Module(path, source)
    export_home = {}
    for name, module in modules.items():
        for symbol, function in module.exports.items():
            export_home[symbol] = (name, function)
    outside = outside_identifiers()
    compiler = compiler_identifiers() if refined else outside
    live = set()
    work = []

    def mark(node):
        if node not in live:
            live.add(node)
            work.append(node)

    for name, module in modules.items():
        named = compiler if name in TARGETS else outside
        for symbol, function in module.exports.items():
            if symbol in KEEP or symbol in (compiler if refined and former_symbol(symbol) else named):
                mark((name, function))
        for local in module.top_names:
            if local in module.functions:
                mark((name, local))
            if local in module.externs and module.externs[local] in export_home:
                mark(export_home[module.externs[local]])
        for symbol in module.top_addresses:
            if symbol in export_home:
                mark(export_home[symbol])
    while work:
        name, function = work.pop()
        module = modules[name]
        names, addresses = module.references(module.functions[function])
        for local in names:
            if local in module.functions:
                mark((name, local))
            elif local in module.externs:
                target = export_home.get(module.externs[local])
                if target is not None:
                    mark(target)
        for symbol in addresses:
            target = export_home.get(symbol)
            if target is not None:
                mark(target)
    return modules, export_home, live


def newly_dead():
    """Functions the committed tree reached and the working tree does not."""
    _m, _e, live_before = analyse(committed=True, refined=False)
    modules, export_home, live = analyse()
    result = {}
    for name, module in modules.items():
        former = {function for symbol, function in module.exports.items() if former_symbol(symbol)}
        result[name] = [f for f in module.functions
                        if (name, f) not in live
                        and (name in TARGETS or f in former or (name, f) in live_before)]
    return modules, result


def report():
    modules, dead_by_module = newly_dead()
    live = None
    rows = []
    dead_symbols = {}
    for name, module in modules.items():
        dead = dead_by_module[name]
        lines = sum(module.functions[f].end_lineno - module.functions[f].lineno + 1 for f in dead)
        if dead:
            rows.append((lines, name, len(dead), len(module.functions)))
        for symbol, function in module.exports.items():
            if function in dead:
                dead_symbols[symbol] = name
    total = sum(r[0] for r in rows)
    print(f"unreachable functions: {sum(r[2] for r in rows)} in {len(rows)} modules, {total} lines")
    for lines, name, count, of in sorted(rows, reverse=True)[:60]:
        print(f"{lines:6d} lines  {count:4d}/{of:<4d} {name}")
    json.dump(dead_symbols, open(REPO / "build/one-collector/dead_symbols.json", "w"), indent=1, sort_keys=True)
    return 0


def apply():
    modules, dead_by_module = newly_dead()
    removed_functions = 0
    for name, module in modules.items():
        dead = [module.functions[f] for f in dead_by_module[name]]
        if not dead:
            continue
        lines = module.source.splitlines(keepends=True)
        for function in sorted(dead, key=lambda f: f.lineno, reverse=True):
            first = min([function.lineno] + [d.lineno for d in function.decorator_list])
            last = function.end_lineno
            # the comment block directly above belongs to the function
            while first > 1 and lines[first - 2].lstrip().startswith("#") and not lines[first - 2].startswith("# ----"):
                first -= 1
            del lines[first - 1:last]
            # at most two blank lines where the function was
            index = first - 1
            while (index < len(lines) and index >= 2 and lines[index].strip() == ""
                   and lines[index - 1].strip() == "" and lines[index - 2].strip() == ""):
                del lines[index]
            removed_functions += 1
        source = "".join(lines)
        # extern bindings nothing names any more
        tree = ast.parse(source)
        used = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
        lines = source.splitlines(keepends=True)
        for statement in reversed(tree.body):
            if (isinstance(statement, ast.Assign) and len(statement.targets) == 1
                    and isinstance(statement.targets[0], ast.Name)
                    and call_name(statement.value) == "extern"
                    and statement.targets[0].id not in used
                    and statement.targets[0].id in module.externs):
                # only bindings that were in use before this sweep
                before_used = {n.id for n in ast.walk(module.tree) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
                if statement.targets[0].id in before_used:
                    del lines[statement.lineno - 1:statement.end_lineno]
        module.path.write_text("".join(lines), encoding="utf-8")
    print("functions removed:", removed_functions)
    return 0




def entries():
    """Which live functions outside the target modules still call into them."""
    modules, export_home, live = analyse()
    compiler = compiler_identifiers()
    edges = {}
    roots = []
    for name, module in modules.items():
        if name in TARGETS:
            for symbol, function in module.exports.items():
                if symbol in compiler and (name, function) in live:
                    roots.append((name, symbol))
            continue
        for function_name, function in module.functions.items():
            if (name, function_name) not in live:
                continue
            names, addresses = module.references(function)
            for local in names:
                if local in module.externs:
                    target = export_home.get(module.externs[local])
                    if target is not None and target[0] in TARGETS:
                        edges.setdefault(target, []).append(f"{name}:{function_name}")
            for symbol in addresses:
                target = export_home.get(symbol)
                if target is not None and target[0] in TARGETS:
                    edges.setdefault(target, []).append(f"{name}:{function_name}")
    live_target = sum(1 for (m, f) in live if m in TARGETS)
    print("live functions in target modules:", live_target)
    print("named by compiler sources:", len(roots))
    for name, symbol in sorted(roots):
        print("   root", name, symbol)
    print("called from live functions of other modules:", len(edges))
    for (module, function), callers in sorted(edges.items()):
        shown = ", ".join(sorted(set(callers))[:4])
        more = len(set(callers)) - 4
        print(f"   {module}:{function}  <-  {shown}" + (f" (+{more})" if more > 0 else ""))
    return 0


if __name__ == "__main__" and len(sys.argv) > 1 and sys.argv[1] == "entries":
    raise SystemExit(entries())


LOAD_INTRINSICS = {"load_i32", "load_i64", "atomic_load_i32", "atomic_load_i64"}
STORE_INTRINSICS = {"store_i32": 2, "store_i64": 2, "atomic_store_i32": 2, "atomic_store_i64": 2}


def body_without_docstring(function):
    body = list(function.body)
    if (body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)):
        body = body[1:]
    return body


def facts():
    """Write facts.json for fold_one_collector.py: the zero-initialised
    words that no live function can change, and the exported functions
    whose whole body is nothing, a constant, or one of their parameters."""
    modules, export_home, live = analyse()
    compiler = compiler_identifiers()
    zero_defined = set()
    other_defined = set()
    for module in modules.values():
        for node in ast.walk(module.tree):
            name = call_name(node)
            if name in ("define_global_i32", "define_global_i64") and len(node.args) == 2:
                symbol, value = node.args
                if isinstance(symbol, ast.Constant) and isinstance(value, ast.Constant):
                    (zero_defined if value.value == 0 else other_defined).add(symbol.value)
            elif name is not None and name.startswith("define_") and node.args and isinstance(node.args[0], ast.Constant):
                other_defined.add(node.args[0].value)
    variable = set()

    def scan(tree_node, parents_ok):
        """Mark every word whose address is used for anything but a load
        or a store of the constant zero."""
        safe = set()
        for node in ast.walk(tree_node):
            name = call_name(node)
            if name in LOAD_INTRINSICS and node.args:
                safe.add(id(node.args[0]))
            elif name in STORE_INTRINSICS and len(node.args) > 2:
                value = node.args[STORE_INTRINSICS[name]]
                if isinstance(value, ast.Constant) and value.value == 0:
                    safe.add(id(node.args[0]))
        for node in ast.walk(tree_node):
            name = call_name(node)
            if name in ("global_addr", "global_load_ptr", "global_store_ptr") and node.args:
                first = node.args[0]
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    if name != "global_addr" or id(node) not in safe:
                        variable.add(first.value)

    # Every function still in the sources counts, reached or not: one that
    # only a test or a C header names can still be called and write.
    for name, module in modules.items():
        for function_name, function in module.functions.items():
            scan(function, None)
        for statement in module.tree.body:
            if not isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
                scan(statement, None)
    zero_words = sorted(w for w in zero_defined - other_defined - variable
                        if w not in compiler and w.startswith(("pcc_gc_", "py_gc_")))
    def trivial_of(module_set):
        """symbol -> ("void",) | ("const", n) | ("identity", i); a symbol
        with more than one definition (platform variants) is left out."""
        found = {}
        seen = {}
        for name, module in module_set.items():
            for symbol, function_name in module.exports.items():
                seen[symbol] = seen.get(symbol, 0) + 1
                function = module.functions[function_name]
                if any(call_name(d) not in ("c_abi_export", "c_abi_typed_export") for d in function.decorator_list):
                    continue
                body = body_without_docstring(function)
                parameters = [a.arg for a in function.args.args]
                if not body or all(isinstance(st, ast.Pass) or (isinstance(st, ast.Return) and st.value is None) for st in body):
                    found[symbol] = ("void",)
                elif len(body) == 1 and isinstance(body[0], ast.Return):
                    value = body[0].value
                    if isinstance(value, ast.Constant) and isinstance(value.value, int) and not isinstance(value.value, bool):
                        found[symbol] = ("const", value.value)
                    elif isinstance(value, ast.Name) and value.id in parameters:
                        found[symbol] = ("identity", parameters.index(value.id))
        return {symbol: kind for symbol, kind in found.items() if seen[symbol] == 1}

    # Only what this work made trivial: a function that was already empty
    # or constant in the committed tree is not this tool's to inline.
    committed_modules, _e, _l = analyse(committed=True, refined=False)
    before = trivial_of(committed_modules)
    now = trivial_of(modules)
    trivial_void, trivial_const, trivial_identity = [], {}, {}
    for symbol, kind in now.items():
        if before.get(symbol) == kind:
            continue
        if kind[0] == "void":
            trivial_void.append(symbol)
        elif kind[0] == "const":
            trivial_const[symbol] = kind[1]
        else:
            trivial_identity[symbol] = kind[1]
    out = {"zero_words": zero_words, "trivial_void": sorted(trivial_void),
           "trivial_const": trivial_const, "trivial_identity": trivial_identity}
    json.dump(out, open(REPO / "build/one-collector/facts.json", "w"), indent=1, sort_keys=True)
    print("zero words:", len(zero_words), " empty functions:", len(trivial_void),
          " constant functions:", len(trivial_const), " identity functions:", len(trivial_identity))
    return 0


if __name__ == "__main__" and len(sys.argv) > 1 and sys.argv[1] == "facts":
    raise SystemExit(facts())


if __name__ == "__main__" and (len(sys.argv) < 2 or sys.argv[1] in ("report", "apply")):
    command = sys.argv[1] if len(sys.argv) > 1 else "report"
    raise SystemExit(report() if command == "report" else apply())


def callers(symbols):
    """Which live functions call each of ``symbols``."""
    modules, export_home, live = analyse()
    compiler = compiler_identifiers()
    for symbol in symbols:
        home = export_home.get(symbol)
        found = []
        for name, module in modules.items():
            for function_name, function in module.functions.items():
                if (name, function_name) not in live:
                    continue
                names, addresses = module.references(function)
                hit = symbol in addresses
                for local in names:
                    if module.externs.get(local) == symbol:
                        hit = True
                    if home is not None and name == home[0] and local == home[1]:
                        hit = True
                if hit:
                    found.append(f"{name}:{function_name}")
        state = "live" if home in live else "dead"
        rooted = " compiler-root" if symbol in compiler else ""
        print(f"{symbol} [{state}{rooted}] <- {', '.join(found[:12])}" + (f" (+{len(found) - 12})" if len(found) > 12 else ""))
    return 0


if __name__ == "__main__" and len(sys.argv) > 1 and sys.argv[1] == "callers":
    raise SystemExit(callers(sys.argv[2:]))
