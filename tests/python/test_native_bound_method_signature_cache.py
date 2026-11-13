from __future__ import annotations

import subprocess

import pytest
import textwrap
import re

from pcc.frontends.python.pipeline import compile_python


def _bound_method_source() -> str:
    # An instance method value binds through the runtime's descriptor path;
    # the generated adapter with its cached signature serves a bound
    # classmethod value.
    return textwrap.dedent(
        """
        class C:
            @classmethod
            def add(cls, x: int) -> int:
                return x + 1

            def run(self) -> int:
                total = 0
                for i in range(5):
                    f = self.add
                    total += f(i)
                return total

        def main():
            f = C.add
            print(C().run(), f(9))

        main()
        """
    ).lstrip()


def test_bound_method_signature_cache_preserves_call_semantics(tmp_path):
    src = tmp_path / "bound_method_signature_cache.py"
    src.write_text(_bound_method_source(), encoding="utf-8")
    exe = tmp_path / "bound_method_signature_cache.out"

    compile_python(
        str(src),
        str(exe),
        backend="self",
        ir_scaffold_mode="on",
    )
    run = subprocess.run([str(exe)], capture_output=True, text=True, timeout=20)
    assert run.returncode == 0, run.stderr
    assert run.stdout == "15 10\n"


def test_bound_method_signature_cache_is_emitted_for_default_free_method(tmp_path):
    src = tmp_path / "bound_method_signature_cache.py"
    src.write_text(_bound_method_source(), encoding="utf-8")
    ll = tmp_path / "bound_method_signature_cache.ll"

    compile_python(
        str(src),
        str(ll),
        backend="self",
        ir_scaffold_mode="on",
        emit_llvm_only=True,
    )
    ir_text = ll.read_text(encoding="utf-8")
    assert (
        "@__pcc_native_func_sig_cache_bound_method_signature_cache_C_add_classmethod"
        in ir_text
    )


def test_valueclass_method_value_is_an_explicit_compile_error(tmp_path):
    from pcc.frontends.python.codegen.errors import L1CodegenError

    src = tmp_path / "valueclass_method_value.py"
    src.write_text(
        textwrap.dedent(
            """
            from pcc import valueclass

            @valueclass
            class C:
                base: int

                def add(self, x: int) -> int:
                    return x + self.base

                def bound(self):
                    return self.add

            print(C(1).bound()(2))
            """
        ).lstrip(),
        encoding="utf-8",
    )
    with pytest.raises(Exception, match="valueclass receiver"):
        compile_python(str(src), str(tmp_path / "out.ll"), backend="self",
                       ir_scaffold_mode="on", emit_llvm_only=True)


def test_native_function_adapter_uses_known_tuple_arg_getter(tmp_path):
    src = tmp_path / "native_adapter_tuple_get.py"
    src.write_text(
        textwrap.dedent(
            """
            def add(x: int) -> int:
                return x + 1

            def call_it(fn, value: int) -> int:
                return fn(value)

            print(call_it(add, 4))
            """
        ).lstrip(),
        encoding="utf-8",
    )
    ll = tmp_path / "native_adapter_tuple_get.ll"

    compile_python(
        str(src),
        str(ll),
        backend="self",
        ir_scaffold_mode="on",
        emit_llvm_only=True,
    )
    ir_text = ll.read_text(encoding="utf-8")
    assert "define ptr @user_native_adapter_tuple_get_add_native_adapter" in ir_text
    adapter = ir_text.split("define ptr @user_native_adapter_tuple_get_add_native_adapter(", 1)[1].split("\n}", 1)[0]
    assert re.search(
        r"call ptr(?: \(ptr, i64\))? @py_tuple_get_known\(ptr [^,]+, i64 0\)",
        adapter,
    ), adapter
    assert "@py_tuple_get(" not in adapter


def test_bound_method_varargs_no_kwargs_fast_call_preserves_semantics(tmp_path):
    src = tmp_path / "bound_method_varargs.py"
    src.write_text(
        textwrap.dedent(
            """
            class C:
                def collect(self, head: int, *rest):
                    return head + rest[0] + rest[1]

            def main():
                fn = C().collect
                xs = (2, 3)
                print(fn(1, *xs))

            main()
            """
        ).lstrip(),
        encoding="utf-8",
    )
    exe = tmp_path / "bound_method_varargs.out"

    compile_python(
        str(src),
        str(exe),
        backend="self",
        ir_scaffold_mode="on",
    )
    run = subprocess.run([str(exe)], capture_output=True, text=True, timeout=20)

    assert run.returncode == 0, run.stderr
    assert run.stdout == "6\n"
