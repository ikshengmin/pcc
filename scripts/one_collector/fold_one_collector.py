"""Fold the branches that only collectors 1-4 could take.

pcc has one collector, so the words and calls that named the selected
collector are constants. This rewrites runtime sources in place: an ``if``
whose test is constant under those facts is replaced by the arm that runs,
a partly constant test is simplified, and a module-level ``extern`` binding
that nothing uses any more is removed. Everything else, comments and layout
included, is left as it is. It rewrites nothing it cannot prove: a test is
folded only when the operands it drops are free of side effects.

    fold_one_collector.py [--write] FILE...
"""
from __future__ import annotations

import ast
import io
import sys
import tokenize
from pathlib import Path

# Calls that return the selected collector, and words fixed with it.
ZERO_CALLS = {
    "_gc_backend_fast", "_gc_backend_selected_fast", "gc_backend_current",
    "pcc_gc_backend", "pcc_gc_is_cms_worker", "_gc_backend_selected",
}
# Calls that answer the selected collector but also initialise the
# configuration: their value is known, the call itself must stay.
ZERO_VALUE_CALLS = {"pcc_gc_config_ensure", "_init_config", "_selected_backend"}
ZERO_WORDS = {
    "pcc_gc_backend_selected", "pcc_gc_nonmoving_count_free",
    "pcc_gc_backend4_no_refcounts",
}
# Filled from facts.json (sweep_one_collector.py facts): words no live code
# writes, and exported functions whose body is empty, a constant or one of
# their own parameters. BINDINGS maps the current module's names to them.
TRIVIAL_VOID: set = set()
TRIVIAL_CONST: dict = {}
TRIVIAL_IDENTITY: dict = {}
BINDINGS: dict = {}
LOADS = {"load_i32", "load_i64", "atomic_load_i32", "atomic_load_i64"}
PURE_CALLS = LOADS | ZERO_CALLS | {
    "global_addr", "global_load_ptr", "load_ptr", "load_i8", "ptr_is_null",
    "is_tagged_int", "ptr_eq", "ptr_add", "ptr_diff", "null", "abi_constant",
    "logical_shift_right_i64", "int_to_ptr", "ptr_to_int",
    "pcc_threads_enabled", "pcc_thread_owns_stopped_world",
}
UNKNOWN = object()


def call_name(node):
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        return node.func.id
    return None


def is_pure(node) -> bool:
    if isinstance(node, (ast.Constant, ast.Name)):
        return True
    if isinstance(node, ast.Compare):
        return is_pure(node.left) and all(is_pure(c) for c in node.comparators)
    if isinstance(node, ast.BoolOp):
        return all(is_pure(v) for v in node.values)
    if isinstance(node, ast.UnaryOp):
        return is_pure(node.operand)
    if isinstance(node, ast.BinOp):
        return is_pure(node.left) and is_pure(node.right)
    if isinstance(node, ast.Tuple):
        return all(is_pure(e) for e in node.elts)
    if isinstance(node, ast.Call):
        return (call_name(node) in PURE_CALLS and not node.keywords
                and all(is_pure(a) for a in node.args))
    return False


class Folder:
    def __init__(self, constants):
        self.constants = constants  # name -> int, for the current function

    def value(self, node):
        """The node's constant value, or UNKNOWN."""
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, bool)):
            return int(node.value)
        if isinstance(node, ast.Name) and node.id in self.constants:
            return self.constants[node.id]
        name = call_name(node)
        if name in ZERO_CALLS and not node.args and not node.keywords:
            return 0
        if name in ZERO_VALUE_CALLS and not node.args and not node.keywords:
            return 0
        symbol = BINDINGS.get(name) if name is not None else None
        if (symbol in TRIVIAL_CONST and not node.keywords
                and all(is_pure(a) for a in node.args)):
            return TRIVIAL_CONST[symbol]
        if name in LOADS and len(node.args) >= 2:
            address, offset = node.args[0], node.args[1]
            if (call_name(address) == "global_addr" and len(address.args) == 1
                    and isinstance(address.args[0], ast.Constant)
                    and address.args[0].value in ZERO_WORDS
                    and isinstance(offset, ast.Constant) and offset.value == 0):
                return 0
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            inner = self.value(node.operand)
            return UNKNOWN if inner is UNKNOWN else int(not inner)
        if isinstance(node, ast.Compare) and len(node.ops) == 1:
            left = self.value(node.left)
            op = node.ops[0]
            right_node = node.comparators[0]
            if left is not UNKNOWN and isinstance(op, (ast.In, ast.NotIn)):
                if isinstance(right_node, ast.Tuple):
                    items = [self.value(e) for e in right_node.elts]
                    if all(i is not UNKNOWN for i in items):
                        found = left in items
                        return int(found if isinstance(op, ast.In) else not found)
                return UNKNOWN
            right = self.value(right_node)
            if left is UNKNOWN or right is UNKNOWN:
                return UNKNOWN
            if isinstance(op, ast.Eq):
                return int(left == right)
            if isinstance(op, ast.NotEq):
                return int(left != right)
            if isinstance(op, ast.Lt):
                return int(left < right)
            if isinstance(op, ast.LtE):
                return int(left <= right)
            if isinstance(op, ast.Gt):
                return int(left > right)
            if isinstance(op, ast.GtE):
                return int(left >= right)
            return UNKNOWN
        if isinstance(node, ast.BoolOp):
            simplified = self.simplify(node)
            if isinstance(simplified, ast.Constant):
                return int(bool(simplified.value))
        return UNKNOWN

    def simplify(self, node):
        """A test with its constant operands folded: a Constant when the
        whole test is decided, the same node when nothing changed."""
        if isinstance(node, ast.BoolOp):
            is_and = isinstance(node.op, ast.And)
            kept = []
            changed = False
            for operand in node.values:
                operand2 = self.simplify(operand)
                if operand2 is not operand:
                    changed = True
                value = self.value(operand2)
                if value is UNKNOWN:
                    kept.append(operand2)
                    continue
                changed = True
                if bool(value) != is_and:
                    # ``and`` met a false operand, ``or`` a true one: the
                    # test is decided if what ran before it had no effect.
                    if all(is_pure(k) for k in kept):
                        return ast.Constant(value=not is_and)
                    kept.append(ast.Constant(value=not is_and))
                    return ast.BoolOp(op=node.op, values=kept) if len(kept) > 1 else kept[0]
                # A true operand of ``and`` (false of ``or``) drops out.
            if not kept:
                return ast.Constant(value=is_and)
            if not changed:
                return node
            return kept[0] if len(kept) == 1 else ast.BoolOp(op=node.op, values=kept)
        value = self.value(node)
        if value is not UNKNOWN and not isinstance(node, ast.Constant):
            return ast.Constant(value=bool(value))
        return node


def function_constants(function) -> dict:
    """Names bound once in ``function``, to a value constant under the facts."""
    bound = {}
    for arg in ast.walk(function.args):
        if isinstance(arg, ast.arg):
            bound[arg.arg] = 2
    candidates = {}
    for node in ast.walk(function):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)) and node is not function:
            return {}
        targets = []
        if isinstance(node, ast.Assign):
            targets = node.targets
            if len(targets) == 1 and isinstance(targets[0], ast.Name):
                candidates.setdefault(targets[0].id, []).append(node.value)
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
            if isinstance(node.target, ast.Name) and node.value is not None:
                candidates.setdefault(node.target.id, []).append(node.value)
        elif isinstance(node, (ast.AugAssign, ast.For, ast.NamedExpr)):
            targets = [node.target]
            if isinstance(node, ast.AugAssign) and isinstance(node.target, ast.Name):
                bound[node.target.id] = 2
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            for name in node.names:
                bound[name] = 2
        elif isinstance(node, ast.ExceptHandler) and node.name:
            bound[node.name] = 2
        elif isinstance(node, ast.With):
            targets = [item.optional_vars for item in node.items if item.optional_vars is not None]
        for target in targets:
            for name in ast.walk(target):
                if isinstance(name, ast.Name):
                    bound[name.id] = bound.get(name.id, 0) + 1
    constants = {}
    plain = Folder({})
    for name, values in candidates.items():
        # Every binding is a plain assignment, and all of them assign the
        # same constant: the name is that constant wherever it is read.
        if bound.get(name, 0) != len(values):
            continue
        found = {plain.value(value) for value in values}
        if len(found) == 1 and UNKNOWN not in found:
            constants[name] = found.pop()
    return constants


def header_end_line(lines, statement) -> int:
    """Line (1-based) holding the colon that ends an ``if``/``while`` header."""
    test = statement.test
    line = test.end_lineno
    column = test.end_col_offset
    depth = 0
    while line <= len(lines):
        text = lines[line - 1]
        index = column
        in_comment = False
        while index < len(text):
            char = text[index]
            if char == "#":
                in_comment = True
                break
            if char in "([{":
                depth += 1
            elif char in ")]}":
                depth -= 1
            elif char == ":" and depth <= 0:
                return line
            index += 1
        line += 1
        column = 0
    raise ValueError("no header colon")


def has_multiline_string(lines, first, last) -> bool:
    text = "".join(lines[first - 1:last])
    try:
        for token in tokenize.generate_tokens(io.StringIO(text).readline):
            if token.type == tokenize.STRING and token.start[0] != token.end[0]:
                return True
    except (tokenize.TokenError, IndentationError):
        return True
    return False


def indent_of(text: str) -> int:
    return len(text) - len(text.lstrip(" "))


def dedented(block, amount):
    out = []
    for text in block:
        if text.strip() == "":
            out.append(text)
        else:
            out.append(text[amount:] if indent_of(text) >= amount else text.lstrip(" "))
    return out


def arms_of(lines, statement):
    """[(kind, header_first, header_last, body_first, body_last)] of an
    ``if`` chain's first level: the ``if`` arm and, when present, what
    follows it (an ``elif`` chain or an ``else`` block) as one arm."""
    column = statement.col_offset
    head_last = header_end_line(lines, statement)
    if statement.body[0].lineno <= head_last:
        raise ValueError("body on the header line")
    body_last = statement.body[-1].end_lineno
    if not statement.orelse:
        return [("if", statement.lineno, head_last, head_last + 1, statement.end_lineno)]
    line = body_last + 1
    while line <= statement.end_lineno:
        text = lines[line - 1]
        stripped = text.strip()
        if indent_of(text) == column and (stripped.startswith("else:") or stripped.startswith("elif ") or stripped.startswith("elif(")):
            break
        line += 1
    else:
        raise ValueError("no else/elif line")
    kind = "elif" if lines[line - 1].strip().startswith("elif") else "else"
    return [
        ("if", statement.lineno, head_last, head_last + 1, line - 1),
        (kind, line, line, line + (0 if kind == "elif" else 1), statement.end_lineno),
    ]


def one_pass(source: str, report: list) -> str:
    tree = ast.parse(source)
    lines = source.splitlines(keepends=True)
    edits = []  # (first_line, last_line, replacement_lines)
    spans = []  # (first_line, last_line, first_col, last_col, replacement_text) within a line range

    def visit(body, folder, block_indent):
        for statement in body:
            if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
                visit(statement.body, Folder(function_constants(statement)), None)
                continue
            if isinstance(statement, ast.ClassDef):
                visit(statement.body, folder, None)
                continue
            if isinstance(statement, ast.If):
                test = folder.simplify(statement.test)
                if isinstance(test, ast.Constant):
                    try:
                        arms = arms_of(lines, statement)
                    except ValueError as error:
                        report.append(f"skip line {statement.lineno}: {error}")
                        arms = None
                    if arms is not None and not has_multiline_string(lines, statement.lineno, statement.end_lineno):
                        taken = bool(test.value)
                        column = statement.col_offset
                        if taken:
                            _kind, _h0, _h1, first, last = arms[0]
                            block = lines[first - 1:last]
                            amount = indent_of(lines[statement.body[0].lineno - 1]) - column
                            replacement = dedented(block, amount)
                        elif len(arms) == 1:
                            replacement = []
                        else:
                            kind, h0, _h1, first, last = arms[1]
                            if kind == "elif":
                                block = lines[h0 - 1:last]
                                text = block[0]
                                block[0] = text[:column] + "if" + text[column + 4:]
                                replacement = block
                            else:
                                block = lines[first - 1:last]
                                amount = indent_of(lines[statement.orelse[0].lineno - 1]) - column
                                replacement = dedented(block, amount)
                        while replacement and replacement[-1].strip() == "" and len(replacement) > 1 and replacement[-2].strip() == "":
                            replacement.pop()
                        edits.append((statement.lineno, statement.end_lineno, replacement, column))
                        continue
                elif test is not statement.test:
                    node = statement.test
                    spans.append((node.lineno, node.end_lineno, node.col_offset, node.end_col_offset, ast.unparse(test)))
                visit(statement.body, folder, None)
                visit(statement.orelse, folder, None)
                continue
            if isinstance(statement, ast.While):
                test = folder.simplify(statement.test)
                if isinstance(test, ast.Constant) and not test.value and not statement.orelse:
                    edits.append((statement.lineno, statement.end_lineno, [], statement.col_offset))
                    continue
                if test is not statement.test and not isinstance(test, ast.Constant):
                    node = statement.test
                    spans.append((node.lineno, node.end_lineno, node.col_offset, node.end_col_offset, ast.unparse(test)))
                visit(statement.body, folder, None)
                continue
            for field in ("body", "orelse", "finalbody"):
                inner = getattr(statement, field, None)
                if isinstance(inner, list) and inner and isinstance(inner[0], ast.stmt):
                    visit(inner, folder, None)
            for handler in getattr(statement, "handlers", []) or []:
                visit(handler.body, folder, None)

    visit(tree.body, Folder({}), None)
    if not edits and not spans:
        return source
    # Apply from the bottom so earlier line numbers stay valid. Statement
    # edits never overlap each other (only outermost ones were taken); a
    # span edit inside a replaced statement cannot exist either, because a
    # replaced statement is not descended into.
    actions = [("stmt", e[0], e) for e in edits] + [("span", s[0], s) for s in spans]
    actions.sort(key=lambda item: item[1], reverse=True)
    for kind, _line, payload in actions:
        if kind == "stmt":
            first, last, replacement, column = payload
            lines[first - 1:last] = replacement
        else:
            first, last, col0, col1, text = payload
            head = lines[first - 1][:col0]
            tail = lines[last - 1][col1:]
            lines[first - 1:last] = [head + text + tail]
    return "".join(lines)


def fix_empty_blocks(source: str) -> str:
    """A block left without a statement gets ``pass``; loop until it parses."""
    for _ in range(200):
        try:
            ast.parse(source)
            return source
        except IndentationError as error:
            lines = source.splitlines(keepends=True)
            # The message names the header that lost its block.
            import re as _re
            named = _re.search(r"on line (\d+)", error.msg or "")
            if named:
                index = int(named.group(1)) - 1
            else:
                index = error.lineno - 2
                while index >= 0 and (lines[index].strip() == "" or lines[index].lstrip().startswith("#")):
                    index -= 1
            header = lines[index]
            if not header.endswith("\n"):
                lines[index] = header + "\n"
            lines.insert(index + 1, " " * (indent_of(header) + 4) + "pass\n")
            source = "".join(lines)
    raise SystemExit("could not repair empty blocks")


def remove_unused_externs(source: str, report: list, before_unused: set) -> str:
    tree = ast.parse(source)
    used = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)}
    lines = source.splitlines(keepends=True)
    removed = []
    for statement in reversed(tree.body):
        if not (isinstance(statement, ast.Assign) and len(statement.targets) == 1
                and isinstance(statement.targets[0], ast.Name)
                and call_name(statement.value) == "extern"):
            continue
        name = statement.targets[0].id
        if name in used or name in before_unused:
            continue
        del lines[statement.lineno - 1:statement.end_lineno]
        removed.append(name)
    if removed:
        report.append("unused externs removed: " + ", ".join(sorted(removed)))
    return "".join(lines)


def unused_externs(source: str) -> set:
    tree = ast.parse(source)
    used = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)}
    out = set()
    for statement in tree.body:
        if (isinstance(statement, ast.Assign) and len(statement.targets) == 1
                and isinstance(statement.targets[0], ast.Name)
                and call_name(statement.value) == "extern"
                and statement.targets[0].id not in used):
            out.add(statement.targets[0].id)
    return out


def unreachable_tails(source: str) -> set:
    """Source text of the statements that already follow an unconditional
    return or raise: they were unreachable before any folding and are not
    this tool's to remove."""
    out = set()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        for field in ("body", "orelse", "finalbody"):
            block = getattr(node, field, None)
            if not (isinstance(block, list) and block and isinstance(block[0], ast.stmt)):
                continue
            for position, statement in enumerate(block[:-1]):
                if isinstance(statement, (ast.Return, ast.Raise)):
                    out.add(ast.dump(block[position + 1]))
                    break
    return out


def tidy_pass(source: str, keep_tails: set = frozenset()) -> str:
    """Remove what folding left behind: an ``else`` that only passes, an
    ``if`` with a pure test that only passes, and a name bound to the
    selected collector that nothing reads any more."""
    tree = ast.parse(source)
    lines = source.splitlines(keepends=True)
    edits = []
    plain = Folder({})

    def only_pass(body):
        return len(body) == 1 and isinstance(body[0], ast.Pass)

    for function in ast.walk(tree):
        if isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
            loaded = {n.id for n in ast.walk(function) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
            for node in ast.walk(function):
                target = None
                if isinstance(node, ast.AnnAssign) and node.value is not None and isinstance(node.target, ast.Name):
                    target = node.target.id
                elif isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                    target = node.targets[0].id
                if target is None or target in loaded:
                    continue
                if plain.value(node.value) is UNKNOWN or not is_pure(node.value):
                    continue
                if isinstance(node.value, ast.Constant):
                    continue
                edits.append((node.lineno, node.end_lineno, []))
    # Statements after an unconditional return in the same block cannot run.
    for node in ast.walk(tree):
        for field in ("body", "orelse", "finalbody"):
            block = getattr(node, field, None)
            if not (isinstance(block, list) and block and isinstance(block[0], ast.stmt)):
                continue
            for position, statement in enumerate(block[:-1]):
                if isinstance(statement, (ast.Return, ast.Raise)):
                    if ast.dump(block[position + 1]) not in keep_tails:
                        edits.append((block[position + 1].lineno, block[-1].end_lineno, []))
                    break
    # ``if pure: return`` directly before the function's own bare return
    # (or its end) decides nothing.
    def bare_return(statement):
        return isinstance(statement, ast.Return) and statement.value is None

    for function in ast.walk(tree):
        if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        body = function.body
        for position, statement in enumerate(body):
            if not (isinstance(statement, ast.If) and not statement.orelse
                    and len(statement.body) == 1 and bare_return(statement.body[0])
                    and is_pure(statement.test)):
                continue
            rest = body[position + 1:]
            if not rest or (len(rest) == 1 and bare_return(rest[0])):
                edits.append((statement.lineno, statement.end_lineno, []))
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        if node.orelse and only_pass(node.orelse) and not isinstance(node.orelse[0], ast.If):
            # the ``else:`` line sits between the body's end and the pass
            first = node.body[-1].end_lineno + 1
            while first < node.orelse[0].lineno and lines[first - 1].strip() != "else:":
                first += 1
            if lines[first - 1].strip() == "else:":
                edits.append((first, node.orelse[0].end_lineno, []))
        elif not node.orelse and only_pass(node.body) and is_pure(node.test):
            edits.append((node.lineno, node.end_lineno, []))
    if not edits:
        return source
    taken = []
    for first, last, replacement in sorted(edits, key=lambda e: (e[0], -e[1])):
        if taken and first <= taken[-1][1]:
            continue
        taken.append((first, last, replacement))
    for first, last, replacement in reversed(taken):
        lines[first - 1:last] = replacement
    return "".join(lines)


def bindings_of(source: str) -> dict:
    """Module name -> exported symbol, for extern bindings and for the
    module's own exported functions."""
    out = {}
    tree = ast.parse(source)
    for statement in tree.body:
        if (isinstance(statement, ast.Assign) and len(statement.targets) == 1
                and isinstance(statement.targets[0], ast.Name)
                and call_name(statement.value) == "extern" and statement.value.args
                and isinstance(statement.value.args[0], ast.Constant)):
            out[statement.targets[0].id] = statement.value.args[0].value
        elif isinstance(statement, ast.FunctionDef):
            for decorator in statement.decorator_list:
                if call_name(decorator) in ("c_abi_export", "c_abi_typed_export") and decorator.args:
                    first = decorator.args[0]
                    if isinstance(first, ast.Constant) and isinstance(first.value, str):
                        out[statement.name] = first.value
    return out


def trivial_pass(source: str) -> str:
    """Drop statements that only call a function with an empty body, and
    replace calls whose function returns a constant or a parameter."""
    tree = ast.parse(source)
    lines = source.splitlines(keepends=True)
    statements = []
    spans = []
    covered = []

    def inside(node):
        return any(a <= node.lineno and node.end_lineno <= b for a, b in covered)

    for function in tree.body:
        if not isinstance(function, ast.FunctionDef):
            continue
        for node in ast.walk(function):
            if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
                symbol = BINDINGS.get(call_name(node.value))
                if (symbol is not None and not node.value.keywords
                        and (symbol in TRIVIAL_VOID or symbol in TRIVIAL_CONST or symbol in TRIVIAL_IDENTITY)
                        and all(is_pure(a) for a in node.value.args)):
                    statements.append((node.lineno, node.end_lineno))
                    covered.append((node.lineno, node.end_lineno))
        for node in ast.walk(function):
            if not isinstance(node, ast.Call) or inside(node):
                continue
            symbol = BINDINGS.get(call_name(node))
            if symbol is None or node.keywords or not all(is_pure(a) for a in node.args):
                continue
            if symbol in TRIVIAL_CONST:
                text = str(TRIVIAL_CONST[symbol])
            elif symbol in TRIVIAL_IDENTITY and TRIVIAL_IDENTITY[symbol] < len(node.args):
                text = ast.unparse(node.args[TRIVIAL_IDENTITY[symbol]])
            else:
                continue
            spans.append((node.lineno, node.end_lineno, node.col_offset, node.end_col_offset, text))
            covered.append((node.lineno, node.end_lineno))
    if not statements and not spans:
        return source
    actions = [("stmt", a, (a, b)) for a, b in statements] + [("span", sp[0], sp) for sp in spans]
    actions.sort(key=lambda item: item[1], reverse=True)
    for kind, _line, payload in actions:
        if kind == "stmt":
            first, last = payload
            del lines[first - 1:last]
        else:
            first, last, col0, col1, text = payload
            head = lines[first - 1][:col0]
            tail = lines[last - 1][col1:]
            lines[first - 1:last] = [head + text + tail]
    return "".join(lines)


def fold(source: str, report: list) -> str:
    before_unused = unused_externs(source)
    keep_tails = unreachable_tails(source)
    BINDINGS.clear()
    BINDINGS.update(bindings_of(source))
    for _ in range(60):
        folded = fix_empty_blocks(one_pass(source, report))
        folded = fix_empty_blocks(trivial_pass(folded))
        folded = fix_empty_blocks(tidy_pass(folded, keep_tails))
        if folded == source:
            break
        source = folded
    return remove_unused_externs(source, report, before_unused)


def load_facts() -> None:
    import json
    path = Path(__file__).resolve().parents[2] / "build" / "one-collector" / "facts.json"
    if not path.exists():
        return
    facts = json.loads(path.read_text())
    ZERO_WORDS.update(facts.get("zero_words", []))
    TRIVIAL_VOID.update(facts.get("trivial_void", []))
    TRIVIAL_CONST.update(facts.get("trivial_const", {}))
    TRIVIAL_IDENTITY.update(facts.get("trivial_identity", {}))
    forced = Path(__file__).with_name("forced.json")
    if forced.exists():
        facts = json.loads(forced.read_text())
        TRIVIAL_VOID.update(facts.get("trivial_void", []))
        TRIVIAL_CONST.update(facts.get("trivial_const", {}))
        TRIVIAL_IDENTITY.update(facts.get("trivial_identity", {}))


def main(argv) -> int:
    load_facts()
    write = "--write" in argv
    paths = [Path(a) for a in argv if not a.startswith("--")]
    total = 0
    for path in paths:
        source = path.read_text(encoding="utf-8")
        report: list = []
        folded = fold(source, report)
        if folded == source:
            continue
        before = source.count("\n")
        after = folded.count("\n")
        total += before - after
        print(f"{path}: {before} -> {after} lines ({before - after} removed)")
        for line in report:
            print("   ", line)
        if write:
            path.write_text(folded, encoding="utf-8")
    print("total lines removed:", total)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
