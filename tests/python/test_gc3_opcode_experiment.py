"""Opcode/identifier distinction from the concrete closed GET route. No native compile here."""
import unittest
from pcc.frontends.python.codegen.gc3_ceiling_experiment import specialize


class CeilingOpcodeTest(unittest.TestCase):
    def test_closed_method_ssa_names_are_not_exception_instructions(self):
        # Shape of the *ordinary* slot-based call in the author's real handler.
        src='''define external ptr @user_foo_index(ptr %obj, ptr %slot) {
entry:
  %import.method.text.invoke.164.52 = call i64 (ptr, ptr, ptr, ptr) @py_obj_call_slots(ptr %obj, ptr %slot, ptr %slot, ptr %slot)
  %.53 = icmp slt i64 %import.method.text.invoke.164.52, 0
  call void (ptr, ptr) @pcc_gc_store_root(ptr %slot, ptr %obj)
  ret ptr %obj
}
'''
        actual=specialize(src,{'user_foo_index'},'foo')
        self.assertIn('exp.gc3.entry:',actual)
        self.assertIn('%exp.gc3.fast.import.method.text.invoke.164.52 = call i64',actual)
        self.assertIn('call void (ptr, ptr) @pcc_exp_gc3_root_foo(ptr %slot, ptr %obj)',actual)
        self.assertEqual(actual.count('call i64 (ptr, ptr, ptr, ptr) @py_obj_call_slots'),2)

    def test_real_control_or_eh_opcodes_fail_the_whole_body_closed(self):
        for instruction in ('%x = invoke ptr @f(ptr %obj) to label %ok unwind label %error',
                            '%x = landingpad { ptr, i32 } cleanup','resume {ptr, i32} %ex',
                            'catchret from %pad to label %catch','cleanupret from %pad unwind to caller',
                            '%x = catchswitch within none [label %h] unwind to caller',
                            'indirectbr ptr %p, [label %ok]','callbr void @f() to label %ok [label %h]',
                            '%x = musttail call ptr @f(ptr %obj)', 'call void asm sideeffect "", ""()'):
            src='define external ptr @fn(ptr %obj, ptr %slot) {\nentry:\n  '+instruction+'\n  call void (ptr, ptr) @pcc_gc_store_root(ptr %slot, ptr %obj)\n  ret ptr %obj\n}\n'
            with self.subTest(instruction=instruction): self.assertEqual(specialize(src,{'fn'},'app'),src)
