"""Source proofs for call-bounded byte growth, without slice lowering."""
from dataclasses import dataclass, replace

from pcc.frontends.go.branches import walk
from pcc.frontends.go.managed_objects import FixedByteExtent
from pcc.frontends.go.syntax import PointerSyntax, SliceSyntax, StructSyntax, TypeName


_U32_SHIFT_COUNTS = frozenset(str(number) for number in range(32))


@dataclass(frozen=True)
class ByteAppendSummary:
    shifts: tuple[int, ...]

    @property
    def bytes_per_word(self):
        return len(self.shifts)


@dataclass(frozen=True)
class ByteSpanCallerProof:
    append_lengths: tuple[int, ...]
    views: int
    string_copies: int


def _name(expr, value):
    return expr.kind == "name" and expr.value == value


def _slice_of(dtype, element):
    return isinstance(dtype, SliceSyntax) and dtype.element == element


def _builtin_call(expr, name):
    return expr.kind == "call" and expr.callee is not None and _name(expr.callee, name) and expr.spread is None


def _package_names(program):
    names = {item.token.text for item in (*program.types, *program.functions)}
    names.update(token.text for stmt in (*program.constants, *program.variables) for token in stmt.names)
    return names


def byte_append_summary(program, function_name):
    """Recognize a closed scalar loop returning only its updated input span.

    This deliberately fails closed on indirect calls, additional statements,
    shadowing, captured state and arbitrary expressions. It establishes the
    length delta and absence of retention outside the returned result, not
    that an arbitrary caller has proven capacity, roots or nonescape.
    """
    if any(imported.alias == "." for imported in program.imports):
        return None
    if _package_names(program) & {"append", "byte", "uint32"}:
        return None
    functions = [item for item in program.functions if item.token.text == function_name and item.receiver is None]
    if len(functions) != 1:
        return None
    fn = functions[0]
    if (not fn.variadic or fn.type_parameters or len(fn.params) != 2 or len(fn.body) != 2
            or not _slice_of(fn.result, "uint8")):
        return None
    (dst, dst_type), (words, words_type) = fn.params
    if not _slice_of(dst_type, "uint8") or not _slice_of(words_type, "uint32"):
        return None
    if {dst.text, words.text} & {"append", "byte", "_"} or dst.text == words.text:
        return None
    loop, returned = fn.body
    if (loop.kind != "range" or not loop.range_declaration or len(loop.targets) != 2
            or not _name(loop.targets[0], "_") or loop.targets[1].kind != "name"
            or len(loop.body) != 1 or len(loop.exprs) != 1 or not _name(loop.exprs[0], words.text)):
        return None
    word = loop.targets[1].value
    if word in {dst.text, words.text, "append", "byte", "_"}:
        return None
    step = loop.body[0]
    if (step.kind != "=" or len(step.targets) != 1 or not _name(step.targets[0], dst.text)
            or len(step.exprs) != 1 or not _builtin_call(step.exprs[0], "append")
            or returned.kind != "return" or len(returned.exprs) != 1 or not _name(returned.exprs[0], dst.text)):
        return None
    arguments = step.exprs[0].args
    if len(arguments) < 2 or not _name(arguments[0], dst.text):
        return None
    shifts = []
    for arg in arguments[1:]:
        if not _builtin_call(arg, "byte") or len(arg.args) != 1:
            return None
        value = arg.args[0]
        if _name(value, word):
            shifts.append(0)
            continue
        if (value.kind != "binary" or value.value != ">>" or not _name(value.args[0], word)
                or value.args[1].kind != "integer"):
            return None
        text = value.args[1].value
        if text not in _U32_SHIFT_COUNTS:
            return None
        shifts.append(int(text))
    return ByteAppendSummary(tuple(shifts))


def append_within_extent(extent, summary, words):
    """Transfer only the same backing; reallocation is never proven here."""
    if (not isinstance(extent, FixedByteExtent) or not isinstance(summary, ByteAppendSummary)
            or type(words) is not int or words < 0 or not 0 <= extent.length <= extent.capacity
            or summary.bytes_per_word == 0
            or words > (extent.capacity - extent.length) // summary.bytes_per_word):
        raise ValueError("byte append growth exceeds the known capacity or shape")
    return replace(extent, length=extent.length + words * summary.bytes_per_word)


def _expression_children(expr):
    return [*expr.args, *(key for key in expr.keys if key is not None),
            *((expr.callee,) if expr.callee is not None else ())]


def _mentions(expr, local):
    return _name(expr, local) or any(_mentions(child, local) for child in _expression_children(expr))


def _scalar_reads_only(expr, local):
    if _name(expr, local) or expr.kind == "func_literal":
        return False
    if expr.kind == "unary" and expr.value == "&" and _mentions(expr, local):
        return False
    if any(_builtin_call(expr, name) for name in ("len", "cap")):
        if len(expr.args) == 1 and _name(expr.args[0], local):
            return True
    if expr.kind == "index" and _name(expr.args[0], local):
        return all(_scalar_reads_only(item, local) for item in expr.args[1:])
    return all(_scalar_reads_only(child, local) for child in _expression_children(expr))


def _literal_dimension(expr):
    if (expr.kind == "integer" and expr.value.isascii() and expr.value.isdecimal() and len(expr.value) <= 19
            and (expr.value == "0" or not expr.value.startswith("0"))):
        value = int(expr.value)
    elif expr.kind == "binary" and expr.value == "*":
        left, right = (_literal_dimension(item) for item in expr.args)
        if left is None or right is None:
            return None
        value = left * right
    else:
        return None
    return value if value < (1 << 63) else None


def byte_span_caller_proof(program, function_name, local, extent):
    """Conservative source-shape proof; an owned string copy is still required.

    Only the straight-line original append prefix carries its exact length.
    View/byte use may follow it, but future growth is not proved after a view,
    inside a loop, or through a stored alias.
    """
    builtins = {"append", "byte", "cap", "len", "make", "string", "uint32"}
    if (not isinstance(extent, FixedByteExtent) or _package_names(program) & builtins
            or any(imported.alias == "." for imported in program.imports)):
        return None
    functions = [fn for fn in program.functions if fn.token.text == function_name and fn.receiver is None]
    if len(functions) != 1 or functions[0].type_parameters:
        return None
    fn = functions[0]
    protected = builtins | {local}
    if any(token is not None and token.text in protected
           for token in [*(name for name, _ in fn.params), *fn.result_names]):
        return None
    allocation = None
    append_steps, lengths = set(), []
    closed = False
    for stmt in fn.body:
        matches_declaration = (stmt.kind == ":=" and len(stmt.names) == 1 and stmt.names[0].text == local
                               and len(stmt.exprs) == 1 and _builtin_call(stmt.exprs[0], "make"))
        if matches_declaration:
            args = stmt.exprs[0].args
            if (allocation is not None or len(args) != 3 or args[0].kind != "type"
                    or not _slice_of(args[0].value, "uint8")
                    or tuple(_literal_dimension(arg) for arg in args[1:]) != (extent.length, extent.capacity)):
                return None
            allocation = stmt
            continue
        to_local = stmt.kind == "=" and len(stmt.targets) == 1 and _name(stmt.targets[0], local)
        if allocation is not None and to_local and len(stmt.exprs) == 1:
            call = stmt.exprs[0]
            if (call.kind == "call" and call.callee is not None and call.callee.kind == "name"
                    and call.spread is None and call.args and _name(call.args[0], local)):
                summary = byte_append_summary(program, call.callee.value)
                if closed or summary is None or not all(_scalar_reads_only(arg, local) for arg in call.args[1:]):
                    return None
                try:
                    extent = append_within_extent(extent, summary, len(call.args) - 1)
                except ValueError:
                    return None
                append_steps.add(id(stmt))
                lengths.append(extent.length)
                continue
        if any(_mentions(expr, local) for item in walk((stmt,)) for expr in item.exprs + item.targets):
            if allocation is None:
                return None
            closed = True
    if allocation is None:
        return None
    views, copies = 0, 0
    for stmt in walk(fn.body):
        if stmt is allocation or id(stmt) in append_steps:
            continue
        if (stmt.kind in ("goto", "label")
                or any(declaration.token.text in protected for declaration in stmt.type_declarations)
                or stmt.kind in (":=", "var", "const", "range") and any(name.text in protected for name in stmt.names)):
            return None
        if stmt.kind == "=" and len(stmt.targets) == len(stmt.exprs) == 1 and _name(stmt.targets[0], local):
            view = stmt.exprs[0]
            if (view.kind != "slice" or not _name(view.args[0], local)
                    or not all(_scalar_reads_only(item, local) for item in view.args[1:])):
                return None
            views += 1
            continue
        if stmt.kind == "return" and len(stmt.exprs) == 1:
            value = stmt.exprs[0]
            if _builtin_call(value, "string") and len(value.args) == 1 and _name(value.args[0], local):
                copies += 1
                continue
        if stmt.kind == "range" and len(stmt.exprs) == 1 and _name(stmt.exprs[0], local):
            if not all(_scalar_reads_only(target, local) for target in stmt.targets):
                return None
            continue
        if not all(_scalar_reads_only(expr, local) for expr in stmt.exprs + stmt.targets):
            return None
    return ByteSpanCallerProof(tuple(lengths), views, copies)


@dataclass(frozen=True)
class ScalarFirstAppendProof:
    """One builtin append, on a newly declared zero-value slice."""
    element: str
    token: object


def _closed_scalar_function(program, function_name, body_count):
    if (program.imports or program.types or program.constants or program.variables
            or program.linknames or len(program.functions) != 1):
        return None
    fn = program.functions[0]
    if (fn.token.text != function_name or function_name in {"_", "init", "append"}
            or program.token.text != "main" or function_name == "main"
            or fn.receiver is not None or fn.declaration_only or fn.type_parameters
            or fn.variadic or len(fn.params) != 1 or len(fn.body) != body_count
            or any(name is not None for name in fn.result_names)):
        return None
    return fn


def closed_scalar_first_append(program, function_name):
    """Prove the complete contents of an isolated producer package.

    No other declarations, imports, shadowing, calls, state or spread can
    hide behind the opt-in entry. The uninitialized local is necessarily nil;
    its only observation is append and its only escape is the returned value.
    """
    fn = _closed_scalar_function(program, function_name, 2)
    if fn is None:
        return None
    parameter, element = fn.params[0]
    if (type(element) is not str or element not in {"uint8", "uintptr"}
            or not _slice_of(fn.result, element) or parameter is None):
        return None
    protected = {"_", "append", "byte", "uint8", "uintptr"}
    if parameter.text in protected or function_name in protected:
        return None
    declaration, returned = fn.body
    if (declaration.kind != "var" or len(declaration.names) != 1 or declaration.exprs
            or not _slice_of(declaration.type, element) or returned.kind != "return"
            or len(returned.exprs) != 1 or not _builtin_call(returned.exprs[0], "append")):
        return None
    local = declaration.names[0].text
    arguments = returned.exprs[0].args
    if (local in protected or local == parameter.text or len(arguments) != 2
            or not _name(arguments[0], local) or not _name(arguments[1], parameter.text)):
        return None
    return ScalarFirstAppendProof(element, fn.token)

@dataclass(frozen=True)
class ScalarAppendLoopProof:
    """Loop with one zero-offset owner and an unsigned scalar counter."""
    token: object


def closed_scalar_append_loop(program, function_name):
    """Prove a zero-origin uint32 loop returning its sole slice owner."""
    fn = _closed_scalar_function(program, function_name, 3)
    if fn is None:
        return None
    parameter, bound_type = fn.params[0]
    if parameter is None or bound_type != "uint32" or not _slice_of(fn.result, "uintptr"):
        return None
    protected = {"_", "append", "uint32", "uintptr"}
    if protected & {parameter.text, fn.token.text}:
        return None
    declaration, loop, returned = fn.body
    if (declaration.kind != "var" or len(declaration.names) != 1 or declaration.exprs
            or not _slice_of(declaration.type, "uintptr") or loop.kind != "for"
            or loop.init is None or loop.post is None or len(loop.exprs) != 1
            or len(loop.body) != 1 or returned.kind != "return" or len(returned.exprs) != 1):
        return None
    local = declaration.names[0].text
    init, condition, step = loop.init, loop.exprs[0], loop.post
    if (init.kind != ":=" or len(init.names) != 1 or len(init.exprs) != 1
            or not _builtin_call(init.exprs[0], "uint32") or len(init.exprs[0].args) != 1
            or _literal_dimension(init.exprs[0].args[0]) != 0
            or condition.kind != "binary" or condition.value != "<" or len(condition.args) != 2
            or step.kind != "++" or len(step.targets) != 1):
        return None
    index = init.names[0].text
    if (protected & {local, index} or len({parameter.text, local, index}) != 3
            or not _name(condition.args[0], index) or not _name(condition.args[1], parameter.text)
            or not _name(step.targets[0], index) or not _name(returned.exprs[0], local)):
        return None
    update = loop.body[0]
    if (update.kind != "=" or len(update.targets) != 1 or not _name(update.targets[0], local)
            or len(update.exprs) != 1 or not _builtin_call(update.exprs[0], "append")):
        return None
    arguments = update.exprs[0].args
    if (len(arguments) != 2 or not _name(arguments[0], local)
            or not _builtin_call(arguments[1], "uintptr") or len(arguments[1].args) != 1
            or not _name(arguments[1].args[0], index)):
        return None
    return ScalarAppendLoopProof(fn.token)


@dataclass(frozen=True)
class ScalarSliceOwnerProof:
    """Source ownership census, not executable or type-checking acceptance."""
    owner: str | None
    appends: int
    length_reads: int
    owned_returns: int
    nil_returns: int
    forwarded_returns: tuple[str, ...]


def scalar_slice_owner_group(program):
    """Classify exclusive []uintptr locals and direct return forwarding.

    Other scalar expressions are not accepted or typed here. A slice owner
    may be created only as a function-scope zero value, replaced only via
    its own single-element append, and observed only by len or return.
    Borrowing, indexing, aliases, nested functions and unknown exits
    fail closed. The ordinary checker still owns scalar/control semantics.
    """
    builtins = {"append", "len", "nil", "uintptr"}
    # The selected packages only need blank imports here. A named import
    # must have its resolved package scope checked before proving builtins.
    if _package_names(program) & builtins or any(i.alias != "_" for i in program.imports):
        return None
    selected = [fn for fn in program.functions if _slice_of(fn.result, "uintptr")]
    if not selected:
        return None
    candidates = {fn.token.text: fn for fn in selected}
    if (len(candidates) != len(selected) or
            any(sum(f.token.text == name for f in program.functions) != 1 for name in candidates)):
        return None
    protected = builtins | set(candidates)
    if any(link.local in protected for link in program.linknames):
        return None
    result = {}
    for name, fn in candidates.items():
        if (name in {"_", "init", "main"} or fn.receiver is not None or fn.variadic
                or fn.declaration_only or fn.type_parameters
                or any(item is not None for item in fn.result_names)
                or any(not isinstance(dtype, str) or (token is not None and token.text in protected)
                       for token, dtype in fn.params)):
            return None
        locals_ = [stmt for stmt in fn.body if stmt.kind == "var" and isinstance(stmt.type, SliceSyntax)]
        if len(locals_) > 1:
            return None
        declaration = locals_[0] if locals_ else None
        owner = None
        if declaration is not None:
            if (not _slice_of(declaration.type, "uintptr") or len(declaration.names) != 1
                    or declaration.exprs):
                return None
            owner = declaration.names[0].text
            if (owner in protected or owner == "_" or
                    any(token is not None and token.text == owner for token, _ in fn.params)):
                return None
        counts = [0, 0, 0, 0]  # append, len, owned return, nil return
        forwards = []

        def scalar(expr, active):
            if expr.kind == "func_literal":
                return False
            if owner is not None and _builtin_call(expr, "len") and len(expr.args) == 1:
                if _name(expr.args[0], owner):
                    if not active:
                        return False
                    counts[1] += 1
                    return True
            if expr.kind == "name" and (expr.value in candidates or expr.value == "append"
                                         or (owner is not None and expr.value == owner)):
                return False
            return all(scalar(child, active) for child in _expression_children(expr))

        def statement(stmt, active):
            if stmt.kind not in {
                "var", "const", ":=", "=", "++", "--", "if", "for", "switch", "case",
                "block", "break", "continue", "return", "expression", "empty",
            } or stmt.label is not None:
                return False
            if stmt.kind in {"var", "const", ":="} and (
                isinstance(stmt.type, SliceSyntax) or
                any(token.text in protected or token.text == owner for token in stmt.names)
            ):
                return False
            if stmt.kind == "return":
                if len(stmt.exprs) != 1:
                    return False
                expr = stmt.exprs[0]
                if _name(expr, "nil"):
                    counts[3] += 1
                    return True
                if owner is not None and _name(expr, owner):
                    counts[2] += 1
                    return active
                if expr.kind == "call" and expr.value in candidates and _builtin_call(expr, expr.value):
                    if len(expr.args) != len(candidates[expr.value].params):
                        return False
                    forwards.append(expr.value)
                    return all(scalar(arg, active) for arg in expr.args)
                return False
            if (owner is not None and stmt.kind == "=" and len(stmt.targets) == 1
                    and _name(stmt.targets[0], owner)):
                if (not active or len(stmt.exprs) != 1 or not _builtin_call(stmt.exprs[0], "append")):
                    return False
                args = stmt.exprs[0].args
                if len(args) != 2 or not _name(args[0], owner) or not scalar(args[1], active):
                    return False
                counts[0] += 1
                return True
            return (all(scalar(expr, active) for expr in (*stmt.targets, *stmt.exprs)) and
                    all(statement(child, active) for child in (
                        *([stmt.init] if stmt.init is not None else []),
                        *stmt.body, *(stmt.otherwise or []), *stmt.cases,
                        *([stmt.post] if stmt.post is not None else []))))

        active = False
        for stmt in fn.body:
            if stmt is declaration:
                active = True
            elif not statement(stmt, active):
                return None
        if not (counts[2] or counts[3] or forwards):
            return None
        result[name] = ScalarSliceOwnerProof(owner, *counts, tuple(forwards))

    def acyclic(name, visiting, complete):
        if name in visiting:
            return False
        if name in complete:
            return True
        visiting.add(name)
        if not all(acyclic(child, visiting, complete) for child in result[name].forwarded_returns):
            return False
        visiting.remove(name)
        complete.add(name)
        return True

    complete = set()
    return result if all(acyclic(name, set(), complete) for name in result) else None


@dataclass(frozen=True)
class StaticOptionCatalogRequest:
    """Names to look up and independently prove in an exact source package."""
    slot: str
    builder: str
    level: str


@dataclass(frozen=True)
class StaticOptionBuilderProof:
    """Bounds and static field sources, not an initialized global descriptor."""
    record: str
    initial_count: int
    thresholds: tuple[tuple[int, int], ...]
    feature_fields: tuple[tuple[str, str], ...]
    unbound_exports: tuple[str, ...]

    @property
    def capacity(self):
        return self.initial_count + sum(count for _, count in self.thresholds)

    def count_at(self, level):
        if type(level) is not int:
            raise ValueError("option level must be an integer")
        return self.initial_count + sum(count for limit, count in self.thresholds if level < limit)


def bounded_static_option_builder(program, builder_name, slot, level_function):
    """Prove a bounded single-owner initializer prefix for a global slice.

    A nonempty literal is followed by conditional straight-line append
    groups. Each literal has a distinct string label and a direct pointer
    to a package boolean field. This does not prove other consumers, target
    initialization order, fixture offsets, or a native record representation.
    """
    from pcc.frontends.go.strings import literal_bytes

    if any(item.alias != "_" for item in program.imports) or "append" in _package_names(program):
        return None
    variables = {token.text: stmt for stmt in program.variables for token in stmt.names}
    global_slot = variables.get(slot)
    if (global_slot is None or len(global_slot.names) != 1 or global_slot.exprs
            or not isinstance(global_slot.type, SliceSyntax)):
        return None
    element = global_slot.type.element
    if not isinstance(element, TypeName) or element.qualifier is not None:
        return None
    record = element.token.text
    definitions = [decl for decl in program.types if decl.token.text == record]
    if len(definitions) != 1 or definitions[0].alias or not isinstance(definitions[0].type, StructSyntax):
        return None
    layout = definitions[0].type
    if any(layout.embedded):
        return None
    labels = [token.text for token, dtype in layout.fields if dtype == "string"]
    features = [token.text for token, dtype in layout.fields
                if isinstance(dtype, PointerSyntax) and dtype.element == "bool"]
    if (len(labels) != 1 or len(features) != 1 or
            any(token.text not in (*labels, *features) and dtype != "bool" for token, dtype in layout.fields)
            or len({token.text for token, _ in layout.fields}) != len(layout.fields)
            or any(token.text == "_" for token, _ in layout.fields)):
        return None
    label_key, feature_key = labels[0], features[0]
    builders = [fn for fn in program.functions if fn.token.text == builder_name]
    producers = [fn for fn in program.functions if fn.token.text == level_function]
    if len(builders) != 1 or len(producers) != 1:
        return None
    builder, producer = builders[0], producers[0]
    if (builder.params or builder.result != "void" or builder.receiver is not None
            or builder.type_parameters or len(builder.body) < 2 or producer.params
            or producer.result != "int32" or producer.receiver is not None or producer.type_parameters):
        return None

    seen_labels, seen_fields, field_order = set(), set(), []

    def typed_element(dtype):
        return isinstance(dtype, TypeName) and dtype.qualifier is None and dtype.token.text == record

    def item(expr, *, elided):
        if (expr.kind != "composite" or (expr.value is not None if elided else not typed_element(expr.value))
                or len(expr.args) != 2 or len(expr.keys) != 2
                or any(key is None or key.kind != "name" for key in expr.keys)):
            return False
        pairs = {key.value: value for key, value in zip(expr.keys, expr.args)}
        if set(pairs) != {label_key, feature_key}:
            return False
        label, address = pairs[label_key], pairs[feature_key]
        if label.kind != "string" or address.kind != "unary" or address.value != "&" or len(address.args) != 1:
            return False
        selector = address.args[0]
        if selector.kind != "selector" or len(selector.args) != 1 or selector.args[0].kind != "name":
            return False
        owner, field = selector.args[0].value, selector.value
        declaration = variables.get(owner)
        if (declaration is None or len(declaration.names) != 1 or not isinstance(declaration.type, StructSyntax)
                or sum(token.text == field and dtype == "bool" for token, dtype in declaration.type.fields) != 1):
            return False
        text = literal_bytes(label.token)
        key = (owner, field)
        if not text or text in seen_labels or key in seen_fields:
            return False
        seen_labels.add(text)
        seen_fields.add(key)
        field_order.append(key)
        return True

    def write(stmt):
        return (stmt.kind == "=" and len(stmt.targets) == len(stmt.exprs) == 1
                and _name(stmt.targets[0], slot))

    setup, level = builder.body[:2]
    if not write(setup):
        return None
    initial = setup.exprs[0]
    if (initial.kind != "composite" or not isinstance(initial.value, SliceSyntax)
            or not typed_element(initial.value.element) or not initial.args
            or any(key is not None for key in initial.keys)
            or not all(item(value, elided=True) for value in initial.args)):
        return None
    if (level.kind != ":=" or len(level.names) != len(level.exprs) or len(level.names) != 1
            or not _builtin_call(level.exprs[0], level_function) or level.exprs[0].args):
        return None
    level_name = level.names[0].text
    thresholds, end = [], 2
    for node in builder.body[2:]:
        if (node.kind != "if" or node.init is not None or node.otherwise
                or len(node.body) != 1 or len(node.exprs) != 1 or not write(node.body[0])):
            break
        control, append = node.exprs[0], node.body[0].exprs[0]
        if (control.kind != "binary" or control.value != "<" or len(control.args) != 2
                or not _name(control.args[0], level_name) or not _builtin_call(append, "append")
                or len(append.args) < 2 or not _name(append.args[0], slot)):
            return None
        limit = _literal_dimension(control.args[1])
        if limit is None or limit > (1 << 31) - 1 or (thresholds and thresholds[-1][0] >= limit):
            return None
        if not all(item(value, elided=False) for value in append.args[1:]):
            return None
        thresholds.append((limit, len(append.args) - 1))
        end += 1
    def slot_use(expr):
        if _name(expr, slot):
            return True
        if expr.kind == "func_literal":
            return any(slot_use(child) for stmt in walk(expr.value.body)
                       for child in (*stmt.exprs, *stmt.targets))
        return any(slot_use(child) for child in _expression_children(expr))

    if any(slot_use(expr) for stmt in walk(builder.body[end:]) for expr in (*stmt.exprs, *stmt.targets)):
        return None
    owners = {owner for owner, _ in field_order}
    protected = {slot, record, level_function, "append"} | owners
    if any(token.text in protected for stmt in walk(builder.body)
           if stmt.kind in {"var", "const", ":="} for token in stmt.names):
        return None
    relevant = [link for link in program.linknames if link.local in protected]
    if any(link.target is not None or link.local not in owners for link in relevant):
        return None
    exports = tuple(sorted({link.local for link in relevant}))
    return StaticOptionBuilderProof(record, len(initial.args), tuple(thresholds), tuple(field_order), exports)


def static_catalog_index_places(statement, slot, fields):
    """Return only direct scalar field places using an unchanged range index."""
    if (statement.kind != "range" or not statement.range_declaration
            or len(statement.targets) != 1 or statement.targets[0].kind != "name"
            or statement.targets[0].value in {"_", slot}):
        return None
    index = statement.targets[0].value
    places = []

    def expression(expr):
        if expr.kind in {"func_literal", "call"} or _name(expr, slot):
            return False
        if expr.kind == "unary" and expr.value == "&" and (_mentions(expr, slot) or _mentions(expr, index)):
            return False
        if expr.kind == "selector" and len(expr.args) == 1:
            base = expr.args[0]
            if base.kind == "index" and len(base.args) == 2 and _name(base.args[0], slot):
                if expr.value not in fields or not _name(base.args[1], index):
                    return False
                places.append(base)
                return True
        return all(expression(child) for child in _expression_children(expr))

    for node in walk(statement.body):
        if node.kind not in {"var", "const", ":=", "=", "++", "--", "if", "block", "break",
                             "continue", "return", "expression", "empty"}:
            return None
        if any(token.text in {slot, index} for token in node.names):
            return None
        if any(_name(target, index) or _name(target, slot) for target in node.targets):
            return None
        if not all(expression(expr) for expr in (*node.exprs, *node.targets)):
            return None
    return tuple(places)
