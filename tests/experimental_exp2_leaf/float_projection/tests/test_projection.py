from pathlib import Path
import re
import unittest
from unittest.mock import patch
from pcc.frontends.python import type_infer
from pcc.frontends.python.py_lift import parse_and_lift
from pcc.frontends.python.codegen.layer1 import L1CodeGen

BASE=Path(__file__).resolve().parents[1]
def ir(text):
    m=type_infer.infer_module(parse_and_lift(text,'float_project.py','float_project'))
    host=L1CodeGen(m,ir_scaffold_mode='on')
    host._strict_no_libpython=True
    return str(host.generate(m))

def function(text, name):
    symbol='user_float_project_'+name
    start=re.search(r'(?m)^define [^\n]*?@'+re.escape(symbol)+r'\([^\n]*\{\n',text)
    assert start, symbol
    return text[start.end():text.index('\n}',start.end())]

class Projection(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text=ir((BASE/'fixture/float_project.py').read_text())
    def test_dynamic_lexical_slot_has_only_local_non_escaping_exact_edge(self):
        b=function(self.text,'project')
        for marker in ('@pcc_gc_backend_selected', '@pcc_exp2_lexical_leaf_slot',
                       '@pcc_diagnostics_runtime_log_fast_state', '@pcc_gc_frame_enter_lifo_local'):
            self.assertTrue(marker in b,marker)
        self.assertIn('@pcc_gc_foreign_lease_acquire',b)
        self.assertIn('@py_float_value_of',b)
        self.assertIn('@pcc_gc_foreign_lease_release',b)
        edge=re.search(r'(?m)^float.gc3.exact[^:\n]*:\n(.*?)(?=\n[^ \n][^\n]*:\n)',b,re.S)[1]
        self.assertIn('load double',edge)
        self.assertNotRegex(edge,r'\b(call|invoke|store)\b')
        self.assertIn('phi double',b)
    def test_default_without_explicit_opt_in_keeps_all_of_generic_converter(self):
        with patch.dict('os.environ',{'PCC_EXPERIMENT_GC3_LEXICAL':'0'}):
            b=function(ir((BASE/'fixture/float_project.py').read_text()),'project')
        self.assertNotIn('float.gc3.',b)
        self.assertIn('@pcc_gc_foreign_lease_acquire',b)
        self.assertIn('@py_float_value_of',b)
    def test_existing_literal_and_native_double_paths_are_not_replaced(self):
        for name in ('literal','typed'):
            with self.subTest(name=name):
                b=function(self.text,name)
                self.assertNotIn('float.gc3.',b)
                self.assertNotIn('@py_float_value_of',b)
    def test_shadowed_builtin_keeps_user_callable(self):
        b=function(ir('def project(arg, float):\n    return float(arg)\n'),'project')
        self.assertNotIn('float.gc3.',b)

if __name__=='__main__':unittest.main(verbosity=2)
