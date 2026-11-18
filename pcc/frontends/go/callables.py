"""Closed Go function signatures and declaration-owned named callable instances.

A concrete admitted function value is nil or the code address of an owned,
noncapturing original function. These types do not grant closure environments,
pointer retention, slice backing storage, interfaces or Go's external ABI.
"""
from dataclasses import dataclass, field

from .diagnostics import GoDiagnostic, UnsupportedGo
from .records import DefinedType, underlying
from .types import PointerType, Results, SliceType, is_integer, is_float


@dataclass(frozen=True)
class CallableType:
    parameters: tuple
    result: object
    variadic: bool = False

    def __str__(self):
        parts = list(map(str, self.parameters))
        if self.variadic:
            parts[-1] = '...' + str(self.parameters[-1].element)
        return 'func('+', '.join(parts)+')'+(
            '' if self.result == 'void' else ' '+str(self.result))


@dataclass(eq=False)
class CallableTemplate:
    name: str
    package: str
    parameters: tuple
    signature: CallableType
    session: object
    instances: dict = field(default_factory=dict)

    def instantiate(self, arguments, token):
        from .generics import COMPARABLE, comparable, substitute

        arguments = tuple(arguments)
        if len(arguments) != len(self.parameters):
            raise GoDiagnostic(token, 'wrong number of type arguments for '+self.name)
        for parameter, argument in zip(self.parameters, arguments):
            if parameter.constraint is COMPARABLE and not comparable(argument):
                raise GoDiagnostic(token, str(argument)+' does not satisfy comparable')
        if arguments not in self.instances:
            # The cap is graph-wide, including signatures shared by imports,
            # not 128 new objects per declaration. Reserve before substitution
            # since it may instantiate another named signature.
            if self.session.callable_instances >= 128:
                raise UnsupportedGo(token, 'go.unsupported.limit-generic-instances',
                                    'named callable types exceed graph limit (128 identities)')
            self.session.callable_instances += 1
            try:
                sig = substitute(self.signature, dict(zip(self.parameters, arguments)), token, signature=True)
                self.instances[arguments] = DefinedType(self.name, self.package, sig,
                                                        template=self, type_arguments=arguments)
            except Exception:
                self.session.callable_instances -= 1
                raise
        return self.instances[arguments]


def callable_signature(dtype):
    base = underlying(dtype)
    return base if isinstance(base, CallableType) else None


def require_callable_abi(sig, token):
    """Only scalar/code-pointer carriers. No indirect pointer-retention inference."""
    from .generics import TypeParameter

    # Bound expanded signature work/IR spelling, even for a repeated DAG.
    # Recursive function types need a nominal IR signature anchor.
    left = [4096]
    def visit(t, active):
        left[0] -= 1
        if left[0] < 0 or len(active) >= 32:
            raise UnsupportedGo(token, 'go.unsupported.limit-type-expansion',
                                'indirect function type exceeds the bounded signature representation')
        q = underlying(t)
        if isinstance(q, CallableType):
            if q.variadic:
                raise UnsupportedGo(token, 'go.unsupported.slice-storage',
                                    'indirect variadic calls lack an owned non-retention contract')
            if id(q) in active:
                raise UnsupportedGo(token, 'go.unsupported.function-values',
                                    'recursive indirect function signatures require ABI type anchors')
            current = active | {id(q)}
            for value in q.parameters + (q.result,):
                visit(value, current)
        elif isinstance(q, Results):
            # Aggregate result transport through indirect calls has not been qualified.
            raise UnsupportedGo(token, 'go.unsupported.function-values',
                                'multiple-result function values require indirect aggregate-result ABI')
        elif isinstance(q, SliceType):
            raise UnsupportedGo(token, 'go.unsupported.slice-storage',
                                'indirect argument or result requires a backed slice value')
        elif isinstance(q, PointerType):
            raise UnsupportedGo(token, 'go.unsupported.pointer-signatures',
                                'indirect pointer transfer requires a checked non-retention contract')
        elif not (q in ('void', 'bool') or is_integer(q) or is_float(q) or isinstance(q, TypeParameter)):
            raise UnsupportedGo(token, 'go.unsupported.function-values',
                                'indirect calls currently transport scalars and noncapturing function values')
    visit(sig, set())
