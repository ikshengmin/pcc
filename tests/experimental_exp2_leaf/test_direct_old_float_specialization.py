"""M11's private generated clones never insert a call into ordinary hot entries."""
from __future__ import annotations

import argparse
import ast
import hashlib
from pathlib import Path
import unittest

RUNTIME = Path(__file__).resolve().parents[2] / "pcc/runtime/py"
FILES = ("py_obj.py", "py_obj_stubs.py", "py_binary_repeat_runtime.py")
ORDINARY = {
    "py_obj.py": ("pcc_gc_alloc",),
    "py_obj_stubs.py": ("py_float_from_f64", "py_float_mul"),
    "py_binary_repeat_runtime.py": ("_mul_body", "_mul_adopt", "py_obj_mul_slots"),
}
M10_NORMAL_AST_SHA256 = {'py_obj.py': {'pcc_gc_alloc': '902941e55266cffa400cb1be20e227706b222a34b3e49e48444dacc075472e29'}, 'py_obj_stubs.py': {'py_float_from_f64': 'c7091c5e27071eb403d9122d2b1cca3d88e6781ea4de54c1202658ac949b956d', 'py_float_mul': '1360b7fb8869f6b1d8db163cbbc1ac8564d0075fab723f31a98e7257385bb015'}, 'py_binary_repeat_runtime.py': {'_mul_body': '79864d26fde70f74eb78914dae9ac78e9993c19d6f73f58165b020d3baa3ba7d', '_mul_adopt': 'c248d9c6636f16e606ba5e6c66701bb289afefbb4cd03d01367f7c7152a2b531', 'py_obj_mul_slots': 'c7609cd686eab0c3da29b8755ba28039f8cdad6c2d5eba2558030846ccfc722c'}}
BEGIN = "# BEGIN EXP2 M11 generated old-only entries (test_direct_old_float_specialization.py)"
END = "# END EXP2 M11 generated old-only entries"


def functions(text):
    return {node.name:node for node in ast.parse(text).body if isinstance(node, ast.FunctionDef)}


def segment(text, name):
    node=functions(text)[name]
    first=min([node.lineno]+[d.lineno for d in node.decorator_list])
    return "\n".join(text.splitlines()[first-1:node.end_lineno])+"\n"


def exactly(text, old, new):
    if text.count(old)!=1:
        raise ValueError("expected a single source anchor: "+old)
    return text.replace(old,new,1)


def exported(text, old, new):
    return exactly(text,f'@c_abi_export("{old}")\ndef {old}(',f'@c_abi_export("{new}")\ndef {new}(')


def clone_alloc(text):
    original=segment(text,"pcc_gc_alloc")
    old=exported(original,"pcc_gc_alloc","_pcc_exp2_alloc_direct_old_float")
    old=exactly(old,'size: int, type_tag: int, flags: int):\n',
                'site: i64):\n    size: int = 24\n    type_tag: int = PY_TYPE_FLOAT\n    flags: int = 0\n')
    old=exactly(old,'    backend: int = _gc_backend_fast()\n',
                '    backend: int = _gc_backend_fast()\n'
                '    direct_counter = null()\n'
                '    if backend == 3:\n'
                '        direct_counter = _exp2_old_float_counter(site)\n')
    old=exactly(old,'    if (backend == 3 and flags == 0 and\n',
                '    if (ptr_is_null(direct_counter) and backend == 3 and flags == 0 and\n')
    suffix='    return obj\n'
    if not old.endswith(suffix):
        raise ValueError('stock allocator result/registration shape changed')
    old=old[:-len(suffix)]+'    if not ptr_is_null(direct_counter):\n' \
        '        atomic_store_i64(direct_counter, 0, atomic_load_i64(direct_counter, 0, "relaxed") + 1, "relaxed")\n'+suffix
    return old


def clone_box(text, name, site):
    old=exported(segment(text,"py_float_from_f64"),"py_float_from_f64",name)
    return exactly(old,'    p = pcc_gc_alloc(24, PY_TYPE_FLOAT, 0)\n',f'    p = _exp2_alloc_old_float({site})\n')


def clone_float(text):
    old=exported(segment(text,"py_float_mul"),"py_float_mul","_pcc_exp2_exact_float_mul_old")
    old=exactly(old,'        return py_float_from_f64(py_float_to_f64(a) * py_float_to_f64(b))\n',
                '        if at == PY_TYPE_FLOAT and bt == PY_TYPE_FLOAT:\n'
                '            return _pcc_exp2_float_box_old_mul(py_float_to_f64(a) * py_float_to_f64(b))\n'
                '        return py_float_from_f64(py_float_to_f64(a) * py_float_to_f64(b))\n')
    return '\n\n'.join((clone_box(text,"pcc_exp2_float_from_f64_old",1).rstrip(),
                       clone_box(text,"_pcc_exp2_float_box_old_mul",4).rstrip(),old.rstrip()))+"\n"


def clone_mul(text):
    body=segment(text,"_mul_body")
    old=exactly(body,'def _mul_body(', 'def _exp2_mul_body_old(')
    anchor='    source_index: int = _MUL_LEFT\n'
    old=exactly(old,anchor,
                '    if (left_tag == PY_TYPE_FLOAT and right_tag == PY_TYPE_FLOAT\n'
                '            and atomic_load_i64(global_addr("pcc_exp2_window_state"), 0, "acquire") == 2):\n'
                '        store_ptr(result, 0, _exp2_exact_float_mul_old(load_ptr(left, 0), load_ptr(right, 0)))\n'
                '        return _mul_adopt(slots, tokens, _MUL_RESULT)\n'+anchor)
    slots=exported(segment(text,"py_obj_mul_slots"),"py_obj_mul_slots","pcc_exp2_obj_mul_slots_old")
    slots=exactly(slots,'        status = _mul_body(slots, tokens)\n',
                  '        status = _exp2_mul_body_old(slots, tokens)\n')
    return old.rstrip()+'\n\n\n'+slots


CLONE = dict(zip(FILES,(clone_alloc,clone_float,clone_mul),strict=True))


def generated(name,text):
    return BEGIN+'\n'+CLONE[name](text)+END+'\n'


def normal_ast(text, name):
    return hashlib.sha256(ast.dump(functions(text)[name],include_attributes=False).encode()).hexdigest()


class DirectOldSpecialization(unittest.TestCase):
    def test_original_hot_bodies_stay_the_m10_ast(self):
        self.assertEqual(set(M10_NORMAL_AST_SHA256),set(FILES))
        for f in FILES:
            src=(RUNTIME/f).read_text()
            for name in ORDINARY[f]:
                with self.subTest(f=f,name=name):
                    self.assertEqual(normal_ast(src,name),M10_NORMAL_AST_SHA256[f][name])

    def test_full_specializations_follow_current_shared_stock_ast(self):
        for f in FILES:
            with self.subTest(f=f):
                src=(RUNTIME/f).read_text()
                self.assertEqual(src.count(BEGIN),1)
                self.assertEqual(src.count(END),1)
                auto=src[src.index(BEGIN):src.index(END)+len(END)]+'\n'
                self.assertEqual(auto,generated(f,src))


def refresh(path):
    for f in FILES:
        file=path/f
        s=file.read_text()
        if BEGIN not in s:
            if END in s:
                raise ValueError('unpaired generated marker')
            s=s.rstrip()+'\n\n\n'+generated(f,s)
        else:
            if s.count(BEGIN)!=1 or s.count(END)!=1:
                raise ValueError('unpaired generated markers')
            lo,hi=s.index(BEGIN),s.index(END)+len(END)+1
            s=s[:lo]+generated(f,s)+s[hi:]
        file.write_text(s)


if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--regenerate',type=Path)
    args=p.parse_args()
    if args.regenerate is None:
        unittest.main(argv=[__file__],verbosity=2)
    else:
        refresh(args.regenerate)
