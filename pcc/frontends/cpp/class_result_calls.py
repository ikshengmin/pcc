"""Selected ordinary calls returning bounded nontrivial-for-calls classes.

A plan is a source class prvalue, never a disguised pointer expression. Calls
are emitted only when a destination owner consumes the plan. No overload or
access selection is performed here; the caller supplies the checked winner.
"""
from pcc.frontends.c.ast import c_ast
from pcc.frontends.cpp.class_result_abi import IndirectResult
from pcc.frontends.cpp.diagnostics import CppDiagnostic
from pcc.frontends.cpp.types import PointerType, unqualified


def _reject(coord, message):
    raise CppDiagnostic('cpp.unsupported.class-result-lifetime', message, coord)


from pcc.frontends.cpp.class_result_guards import PendingClassResult


class CallResultPlan(PendingClassResult):

    def __init__(self, parser, selected, converted_arguments, coord,
                 object_expression=None, discarded_object=None):
        self.abi = IndirectResult(parser, selected, coord)
        self.dtype = self.abi.result_type
        self.source_type = selected.dtype
        self.selected = selected
        self.preparation = []
        self.arguments = list(converted_arguments)
        parser.has_class_result_plans = True
        self.coord = coord
        self.owner = parser
        if (parser.template_function is not None or parser.instantiating_template
                or parser.unevaluated_depth):
            _reject(coord, 'class result calls in template/unevaluated contexts are not owned')
        if len(self.arguments) != len(selected.parameters):
            raise ValueError('selected result call requires expanded converted explicit arguments')
        nonstatic = (getattr(selected, 'class_owner', None) is not None
                     and not getattr(selected, 'method_static', False))
        if nonstatic:
            if object_expression is None or discarded_object is not None:
                raise ValueError('nonstatic result call requires one typed object address')
            from pcc.frontends.cpp.members import object_pointer_type
            this_type = object_pointer_type(selected)
            name = parser._fresh_projection_name('__pcc_cpp_result_this_')
            self.preparation.append(parser._decl(
                name, this_type, coord, parser._convert(object_expression, this_type, coord)))
            self.arguments.insert(0, c_ast.ID(name, coord))
        elif object_expression is not None:
            raise ValueError('static/free call cannot consume implicit this')
        if discarded_object is not None:
            # x.static_f(args): object is still sequenced before arguments.
            self.preparation.append(discarded_object)

    def require_closed(self, parser, coord):
        if parser is not self.owner:
            _reject(coord, 'a result plan cannot escape its semantic parser context')
        # This does NOT claim an external function has a closed constructor
        # body. Result destruction is trivial by the ABI classifier. With no
        # outstanding automatic cleanup, an ordinary possibly-throwing call
        # has the same obligations as existing scalar external calls.
        if not any(parser.lifetimes.frames):
            return
        # Ordinary result-call expansion has no full-body EH proof. A source
        # noexcept/noreturn declaration is not an invocation capability, as
        # in the existing free-function contract. Never invent a proof here.
        _reject(coord, 'class result call with live cleanup requires a proven callee and argument effects')

    def emit_into(self, parser, destination, coord):
        self.require_closed(parser, coord)
        if unqualified(destination.dtype) != PointerType(self.dtype):
            raise CppDiagnostic('cpp.class-result-destination', 'result destination has wrong pointer type', coord)
        call = self.abi.call(destination.node, self.arguments, coord)
        return c_ast.Compound(self.preparation + [call], coord) if self.preparation else call


def selected_call(parser, selected, converted_arguments, coord,
                  object_expression=None, discarded_object=None):
    """After selection/access/default expansion/conversion, before call_result.

    Receiver must be the original typed pointer expression, NOT separately
    evaluated or already packed into converted_arguments. Builder sequences it
    exactly once before the explicit arguments when materialized.
    """
    from pcc.frontends.cpp.parser import Expression
    plan = CallResultPlan(parser, selected, converted_arguments, coord,
                          object_expression, discarded_object)
    return Expression(plan, plan.source_type)
