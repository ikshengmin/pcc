"""Run production runtime function bodies inside host model tests.

One loading discipline for GC model tests, so a production change does not
need a stub copied into every model:

- A model names the production functions it exercises. Their same-module
  helpers come along by call, transitively: a refactor that adds or splits a
  private helper keeps the model running on the real helper. A double the
  model already bound keeps precedence over a pulled-in helper; a function
  the model names is always the production body.
- Cross-object entries a model does not exercise get one documented default
  in ``EXTERN_DEFAULTS`` instead of a stub in each model. A model's own
  double always wins.
- GC state globals start at the values ``freestanding_gc_state.py``
  declares (``gc_state_initial_values``); a model seeds its memory with them
  instead of failing on a global the production code began to read.
- A module's own ``define_*`` declarations run through the model's providers
  (``declare_module_globals``) instead of a copied list of names.
"""
from __future__ import annotations

import ast
import copy
from functools import lru_cache
from pathlib import Path


RUNTIME = Path(__file__).resolve().parents[2] / "pcc" / "runtime" / "py"
GC_STATE_SOURCE = RUNTIME / "freestanding_gc_state.py"


# Defaults for cross-object entries that model tests do not exercise.
EXTERN_DEFAULTS = {
    # GC1/GC2 automatic sweeps can leave plain dead objects to the collector
    # thread. Models run stopped collections without that thread: nothing is
    # deferred, so finishing or slicing the deferred sweep frees nothing.
    "pcc_gc_concurrent_sweep_finish_stopped": lambda: 0,
    "pcc_gc_concurrent_sweep_slice": lambda budget: 0,
    # Count-free collectors root a slot in the running thread's stack by
    # their conservative scan; models of registered roots and leases model
    # slots outside it, which keep the full protocol.
    "pcc_gc_stack_slot_root_is_conservative": lambda slot: 0,
    # Count-free GC1/GC2 stage allocation registrations in per-thread rings
    # (freestanding_gc_alloc_stage). Models without the rings run as a
    # collector that never stages: nothing is drained or released.
    "pcc_gc_alloc_stage_enable": lambda enabled: None,
    "pcc_gc_alloc_stage_note": lambda obj, size: 0,
    "pcc_gc_alloc_stage_miss_drain": lambda: 0,
    "pcc_gc_alloc_stage_drain_stopped": lambda: 0,
    "pcc_gc_alloc_stage_thread_exit": lambda: None,
}


@lru_cache(maxsize=None)
def _module_functions(path: str) -> dict[str, ast.FunctionDef]:
    tree = ast.parse(Path(path).read_text(encoding="utf-8"))
    return {
        node.name: node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
    }


@lru_cache(maxsize=None)
def _module_constants(path: str) -> dict[str, ast.stmt]:
    """Module-level ``NAME: type = <literal>`` / ``NAME = <literal>``."""
    tree = ast.parse(Path(path).read_text(encoding="utf-8"))
    constants = {}
    for node in tree.body:
        if (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.value is not None
        ):
            target, value = node.target.id, node.value
        elif (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
        ):
            target, value = node.targets[0].id, node.value
        else:
            continue
        try:
            ast.literal_eval(value)
        except ValueError:
            continue
        constants[target] = node
    return constants


def with_local_helpers(path, names) -> set[str]:
    """``names`` plus every same-module function they use, transitively:
    called, or passed by name (a visitor callback)."""
    functions = _module_functions(str(path))
    missing = set(names) - set(functions)
    assert not missing, f"{Path(path).name} defines no {sorted(missing)}"
    selected: set[str] = set()
    pending = list(names)
    while pending:
        name = pending.pop()
        if name in selected:
            continue
        selected.add(name)
        for node in ast.walk(functions[name]):
            if (
                isinstance(node, ast.Name)
                and isinstance(node.ctx, ast.Load)
                and node.id in functions
            ):
                pending.append(node.id)
    return selected


def _exported_name(node: ast.FunctionDef):
    for decorator in node.decorator_list:
        if (
            isinstance(decorator, ast.Call)
            and isinstance(decorator.func, ast.Name)
            and decorator.func.id == "c_abi_export"
            and decorator.args
            and isinstance(decorator.args[0], ast.Constant)
        ):
            return decorator.args[0].value
    return None


# Namespace key recording which module bound each loaded constant.
_CONSTANT_OWNERS = "__runtime_body_model_constant_owners__"


class _RenameNames(ast.NodeTransformer):
    def __init__(self, renames):
        self.renames = renames

    def visit_Name(self, node):
        if node.id in self.renames:
            return ast.copy_location(
                ast.Name(id=self.renames[node.id], ctx=node.ctx), node
            )
        return node


def _bind_constants(path, names, namespace) -> dict[str, str]:
    """Bind ``path``'s literal constants ``names``; return body renames.

    Production constants are module-scoped, while a model runs bodies of
    several modules in one namespace. A name no one bound takes this
    module's value. A model's own binding, or this module's earlier one
    (possibly overridden by the model since), stays. When another module
    already bound the name to a different value, this module's bodies read
    their own value under a module-qualified name instead.
    """
    constants = _module_constants(str(path))
    owners = namespace.setdefault(_CONSTANT_OWNERS, {})
    module = str(path)
    renames = {}
    for name in sorted(names):
        value = ast.literal_eval(constants[name].value)
        owner = owners.get(name)
        if name not in namespace:
            namespace[name] = value
            owners[name] = module
        elif owner is not None and owner != module and namespace[name] != value:
            qualified = f"{name}__{Path(path).stem}"
            namespace[qualified] = value
            renames[name] = qualified
    return renames


def load_functions(path, names, namespace, *, local_helpers=True):
    """Execute production bodies from ``path`` into ``namespace``.

    ``names`` None loads every function of the module. The named functions
    always replace any binding. With ``local_helpers`` their same-module
    callees are added unless the model already bound that name. A function
    exported under another symbol is also bound by that symbol, as callers
    in other objects name it. Literal module constants the bodies read are
    bound per ``_bind_constants``. ``EXTERN_DEFAULTS`` fill only names the
    model left unbound.
    """
    functions = _module_functions(str(path))
    names = set(functions) if names is None else set(names)
    selected = with_local_helpers(path, names) if local_helpers else names
    constants = _module_constants(str(path))
    used = {
        node.id
        for name in selected
        for node in ast.walk(functions[name])
        if isinstance(node, ast.Name) and node.id in constants
    }
    renames = _bind_constants(path, used, namespace)
    nodes = []
    for name in sorted(selected):
        if name not in names and name in namespace:
            continue
        # A copy keeps the source line numbers for tracebacks and leaves the
        # cached tree untouched.
        node = copy.deepcopy(functions[name])
        node.decorator_list = []
        if renames:
            node = _RenameNames(renames).visit(node)
        nodes.append(node)
    module = ast.fix_missing_locations(ast.Module(body=nodes, type_ignores=[]))
    exec(compile(module, str(path), "exec"), namespace)
    for name in sorted(selected):
        exported = _exported_name(functions[name])
        if exported is None or exported == name or name not in namespace:
            continue
        if name in names:
            namespace[exported] = namespace[name]
        else:
            namespace.setdefault(exported, namespace[name])
    install_extern_defaults(namespace)
    return namespace


def install_extern_defaults(namespace) -> None:
    for name, value in EXTERN_DEFAULTS.items():
        namespace.setdefault(name, value)


@lru_cache(maxsize=None)
def _module_definitions(path: str) -> tuple[tuple[str, tuple], ...]:
    """Module-level ``define_*(<literal>, ...)`` declarations, in order."""
    tree = ast.parse(Path(path).read_text(encoding="utf-8"))
    definitions = []
    for node in tree.body:
        if not (
            isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Name)
            and node.value.func.id.startswith("define_")
        ):
            continue
        call = node.value
        arguments = tuple(ast.literal_eval(argument) for argument in call.args)
        definitions.append((call.func.id, arguments))
    return tuple(definitions)


def declare_module_globals(path, namespace) -> None:
    """Run ``path``'s ``define_*`` declarations through the model's own
    providers (``namespace["define_global_i64"]`` and so on)."""
    for kind, arguments in _module_definitions(str(path)):
        namespace[kind](*arguments)


def gc_state_cells() -> dict[str, tuple[int, int]]:
    """Every scalar GC state global as ``name -> (byte width, initial)``
    (a null pointer is an 8-byte 0)."""
    cells = {}
    for kind, arguments in _module_definitions(str(GC_STATE_SOURCE)):
        if kind == "define_global_i32":
            cells[arguments[0]] = (4, arguments[1])
        elif kind == "define_global_i64":
            cells[arguments[0]] = (8, arguments[1])
        elif kind == "define_global_ptr_null":
            cells[arguments[0]] = (8, 0)
    return cells


def gc_state_initial_values() -> dict[str, int]:
    """Every scalar GC state global with its declared initial value."""
    return {name: value for name, (_, value) in gc_state_cells().items()}
