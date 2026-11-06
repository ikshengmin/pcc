"""Execute the production CMS handoff across separately compiled TLS owners.

The controlled C caller supplies collector/backend/stopped-world states. It
does not model an actual concurrent collection or qualify the whole runtime.
"""

import ast
from pathlib import Path
import subprocess

import pytest

from pcc.backend.owned_object_emit import emit_owned_object
from pcc.driver.project import TranslationUnit
from pcc.frontends.c.evaluator.c_evaluator import CEvaluator
from pcc.frontends.python.pipeline import compile_python
from pcc.frontends.python.pipeline_targets import host_target_triple


RUNTIME = Path(__file__).resolve().parents[2] / "pcc/runtime/py"
TLS = "pcc_gc_cms_worker_current"


def _slice(filename, function, externs=(), *, definition=False):
    tree = ast.parse((RUNTIME / filename).read_text())
    selected = []
    for node in tree.body:
        if isinstance(node, ast.ImportFrom):
            selected.append(node)
        elif isinstance(node, ast.FunctionDef) and node.name == function:
            selected.append(node)
        elif isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id in (*externs, "__pcc_freestanding__")
            for target in node.targets
        ):
            selected.append(node)
        elif (definition and isinstance(node, ast.Expr)
              and isinstance(node.value, ast.Call)
              and isinstance(node.value.func, ast.Name)
              and node.value.func.id == "define_thread_local_i32"
              and ast.literal_eval(node.value.args[0]) == TLS):
            selected.append(node)
    assert sum(isinstance(node, ast.FunctionDef) for node in selected) == 1
    return ast.unparse(ast.Module(body=selected, type_ignores=[])) + "\n"


def _compile_slices(tmp_path, target):
    sources = {
        "owner": _slice("freestanding_gc_public_collection.py", "pcc_gc_is_cms_worker", definition=True),
        "consumer": _slice("freestanding_gc_incremental_concurrent_scheduler.py",
                           "pcc_gc_tracing_finish_left_to_mutator",
                           ("pcc_gc_is_cms_worker", "pcc_thread_owns_stopped_world")),
    }
    objects = []
    for name, source in sources.items():
        path = tmp_path / (name + ".py")
        path.write_text(source)
        ir = path.with_suffix(".ll")
        compile_python(str(path), str(ir), emit_llvm_only=True,
                       python_library=True, backend="self", target_triple=target)
        obj = path.with_suffix(".o")
        obj.write_bytes(emit_owned_object(ir.read_text(), target))
        objects.append(obj)
    return objects


@pytest.mark.parametrize("target", ["aarch64-unknown-linux-gnu", "x86_64-unknown-linux-gnu"])
def test_scheduler_consumes_the_tls_owner_abi_in_owned_objects(tmp_path, target):
    from pcc.backend.elf_x86_64 import parse_relocatable, STT_TLS

    owner_path, consumer_path = _compile_slices(tmp_path, target)
    owner = parse_relocatable(owner_path.read_bytes())
    consumer = parse_relocatable(consumer_path.read_bytes())
    assert next(symbol for symbol in owner.symbols if symbol.name == TLS).type == STT_TLS
    assert TLS not in {symbol.name for symbol in consumer.symbols}
    getter = next(i for i, symbol in enumerate(consumer.symbols)
                  if symbol.name == "pcc_gc_is_cms_worker")
    assert consumer.symbols[getter].section_index == 0
    assert any(relocation.symbol_index == getter for section in consumer.sections
               for relocation in section.relocations)


@pytest.mark.integration
def test_nonzero_cms_tls_handoff_executes_with_owned_toolchain(tmp_path):
    target = host_target_triple()
    objects = _compile_slices(tmp_path, target)
    source = r'''
extern _Thread_local int pcc_gc_cms_worker_current;
extern long long pcc_gc_is_cms_worker(void);
extern long long pcc_gc_tracing_finish_left_to_mutator(void);
int pcc_gc_backend_selected;
static int stopped;
long long pcc_thread_owns_stopped_world(void) { return stopped; }
int main(void) {
    for (int backend = 0; backend <= 4; ++backend) {
        pcc_gc_backend_selected = backend;
        for (int worker = 0; worker <= 1; ++worker) {
            pcc_gc_cms_worker_current = worker;
            if (pcc_gc_is_cms_worker() != worker) return 10 + backend;
            for (stopped = 0; stopped <= 1; ++stopped) {
                int expected = worker && (backend == 1 || backend == 2) && !stopped;
                if (pcc_gc_tracing_finish_left_to_mutator() != expected) return 20 + backend;
            }
        }
    }
    return 0;
}
'''
    path = tmp_path / "caller.c"
    path.write_text(source)
    evaluator = CEvaluator(backend="self", target_triple=target)
    units = evaluator.compile_translation_units(
        [TranslationUnit(str(path), str(path), source)],
        use_system_cpp=False, use_compile_cache=False,
    )
    binary = tmp_path / "handoff"
    evaluator.emit_executable(units, str(binary), link_args=[str(path) for path in objects])
    ran = subprocess.run([str(binary)], capture_output=True, timeout=10)
    assert (ran.returncode, ran.stdout, ran.stderr) == (0, b"", b"")
