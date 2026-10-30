"""Temporary, process-local swap of PCC's real inference Scope implementation.

Run this only in an isolated host process: type_infer._Scope is a module global.
The context manager restores it on exceptions. This is not a threaded compiler
integration or a production optimization switch.
"""

from contextlib import contextmanager

from pcc.frontends.python import type_infer
from pcc.frontends.python.py_lift import parse_and_lift

from .scope_record import RecordScope


REFERENCE_SCOPE = type_infer._Scope


@contextmanager
def scope_implementation(scope_type):
    previous = type_infer._Scope
    type_infer._Scope = scope_type
    try:
        yield
    finally:
        type_infer._Scope = previous


def infer(source, scope_type, *, name="record_scope_fixture"):
    parsed = parse_and_lift(source, name + ".py", name)
    with scope_implementation(scope_type):
        return type_infer.infer_module(parsed)


def narrowing_fixture(functions=16, branches=8, *, writes=False):
    lines = []
    for function in range(functions):
        lines.append(f"def f{function}(x):")
        if writes:
            lines.append("    result: int = 0")
        for branch in range(branches):
            lines.append("    if isinstance(x, int):")
            if writes:
                lines.append(f"        result = x + {branch}")
            else:
                lines.append(f"        return x + {branch}")
        lines.append("    return result" if writes else "    return 0")
    return "\n".join(lines) + "\n"


def wide_fixture(functions=16, locals_per_function=24):
    lines = []
    for function in range(functions):
        lines.append(f"def wide{function}(x: int) -> int:")
        for local in range(locals_per_function):
            lines.append(f"    local{local} = x + {local}")
        lines.append(f"    return local{locals_per_function - 1}")
    return "\n".join(lines) + "\n"


SEMANTIC_FIXTURES = {
    "narrowing": """def choose(x):
    if isinstance(x, int) and isinstance(x, int):
        return x + 1
    return 0
""",
    "class_shadow": """class Base:
    value: int
class Child(Base):
    extra: int
def identify(x):
    if isinstance(x, Child):
        return x.extra
    return 0
def shadow(Base: int) -> int:
    return Base + 1
""",
    "closure_late_binding": """def outer(x: int):
    y = x
    def inner(z: int) -> int:
        return y + z
    y = x + 1
    return inner
""",
    "comprehension": """def collect(xs: list[int]):
    return [x + 1 for x in xs if x > 0]
""",
    "exception": """def recover(x: int) -> int:
    try:
        return x + 1
    except ValueError as error:
        return -1
    finally:
        x = 2
""",
    "generator": """def produce(x: int):
    for i in range(x):
        yield i
""",
}


def inventory(source, scope_type):
    """Retain created scopes for counting only, outside timing/memory samples."""
    scopes = []

    class CountingScope(scope_type):
        __slots__ = ()

        def __init__(self, parent=None):
            super().__init__(parent)
            scopes.append(self)

    typed = infer(source, CountingScope)
    widths = [len(scope.bindings) for scope in scopes]
    if issubclass(scope_type, RecordScope):
        dictionaries = sum(scope._spill is not None for scope in scopes)
        sets = sum(scope._classes is not None for scope in scopes)
        # Transition 1 -> 2 copies the first retained field into the spill.
        spill_field_copies = dictionaries
    else:
        dictionaries = sets = len(scopes)
        spill_field_copies = 0
    return typed, {
        "scopes": len(scopes),
        "empty_scopes": widths.count(0),
        "single_scopes": widths.count(1),
        "multi_scopes": sum(width > 1 for width in widths),
        "binding_dict_allocations": dictionaries,
        "class_set_allocations": sets,
        "retained_local_fields": sum(widths),
        "retained_parent_edges": sum(scope.parent is not None for scope in scopes),
        "full_environment_copies": 0,
        "spill_field_copies": spill_field_copies,
    }
