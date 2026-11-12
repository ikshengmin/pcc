"""Normal-exit automatic lifetimes, projected to the existing C control AST.

N4659 [stmt.jump]/2, [stmt.return]/3, [class.dtor]. This owner handles only
structured control flow and complete objects. It is not exception unwinding,
setjmp/longjmp handling, temporary lifetime extension or an EH runtime.
"""

from pcc.frontends.c.ast import c_ast
from pcc.frontends.cpp.classes import is_class_type
from pcc.frontends.cpp.diagnostics import CppDiagnostic
from pcc.frontends.cpp.types import unqualified


class AutomaticLifetimes:
    def __init__(self):
        self.frames = []
        self.controls = []
        self.cleanup_destructors = None

    def enter(self):
        self.frames.append([])

    def leave(self):
        self.frames.pop()

    def push_control(self, kind):
        self.controls.append((kind, len(self.frames)))

    def pop_control(self):
        self.controls.pop()

    def register(self, parser, symbol, initializer=None, *, aggregate_initialization=False):
        owner = unqualified(symbol.dtype)
        if not is_class_type(owner):
            return
        from pcc.frontends.cpp.destructors import member_destructors
        # A real aggregate list potentially invokes its elements' D1s from
        # the caller (CWG2227), not from a nonexistent enclosing constructor.
        # Declared C1, DMI and implicit-default paths retain their own owner.
        member_scope = parser.scope if aggregate_initialization else owner.definition.scope
        selected = member_destructors(owner.definition, member_scope)
        destructor = owner.definition.destructor
        if destructor is None:
            if selected:
                raise CppDiagnostic("cpp.unsupported.destructor-subobjects",
                                    "implicit enclosing destruction requires special-member synthesis", symbol.coord)
            return
        if selected and initializer is not None:
            from pcc.frontends.cpp.constructors import call_free_body
            if not call_free_body(initializer):
                raise CppDiagnostic("cpp.unsupported.destructor-subobjects",
                                    "aggregate initializer calls require partial member exception cleanup", symbol.coord)
        from pcc.frontends.cpp.destructors import check_usable, request_body
        check_usable(parser.scope, destructor, symbol.coord)
        request_body(parser, destructor, symbol.coord)
        # All supported declaration output completes initialization before the
        # next statement. No jump may enter after it, and no throwing path is
        # admitted. Register here only after that output has been constructed.
        self.frames[-1].append((symbol, destructor, None))
        symbol.jump_entry = "initialized"

    def register_members(self, this, selected):
        # An enclosing frame outlives the body block and every loop/switch
        # boundary. Return consumes it; break/continue must not consume it.
        self.frames[-1].extend((member, destructor, this) for member, destructor in selected)

    def cleanup(self, boundary, coord):
        calls = []
        for frame in reversed(self.frames[boundary:]):
            for symbol, destructor, this in reversed(frame):
                if this is not None and hasattr(symbol, "subobject_address"):
                    address = symbol.subobject_address(this, coord)  # A base subobject.
                else:
                    object_node = c_ast.ID(symbol.c_name, coord)
                    if this is not None:
                        object_node = c_ast.StructRef(c_ast.ID(this.c_name, coord), "->", object_node, coord)
                    address = c_ast.UnaryOp("&", object_node, coord)
                call = c_ast.FuncCall(c_ast.ID(destructor.c_name, coord),
                                     c_ast.ExprList([address], coord), coord)
                if self.cleanup_destructors is not None:
                    self.cleanup_destructors[call] = destructor
                calls.append(call)
        return calls

    def current_cleanup(self, coord):
        return self.cleanup(len(self.frames) - 1, coord)

    def jump(self, kind, node, coord):
        for control, boundary in reversed(self.controls):
            if kind == "break" or control == "loop":
                calls = self.cleanup(boundary, coord)
                return c_ast.Compound(calls + [node], coord) if calls else node
        raise AssertionError("validated jump has no structured control owner")

    def return_statement(self, parser, expression, coord):
        calls = self.cleanup(0, coord)
        if not calls:
            return c_ast.Return(expression, coord)
        result_type = unqualified(parser.return_type)
        if result_type == "void":
            before = [] if expression is None else [expression]
            return c_ast.Compound(before + calls + [c_ast.Return(None, coord)], coord)
        name = parser._fresh_projection_name("__pcc_cpp_return_")
        value = parser._decl(name, result_type, coord, expression)
        return c_ast.Compound([value] + calls + [c_ast.Return(c_ast.ID(name, coord), coord)], coord)
