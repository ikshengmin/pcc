"""Go declaration checking and value-function instances, not type erasure.

Only scheduling/identity reuse the language-neutral InstanceRegistry. Go's
constraints, inference, scopes, source bodies and concrete representations stay
here. Abstract bodies are checked even when no concrete call requests code.
"""
from copy import deepcopy
from dataclasses import dataclass, replace
import json

from pcc.support.instantiation import InstanceRegistry
from pcc.frontends.go.diagnostics import GoDiagnostic, UnsupportedGo
from pcc.frontends.go.records import DefinedType, InterfaceType, StructType, is_named, same_underlying, underlying
from pcc.frontends.go.interface_methods import interface_type
from pcc.frontends.go.types import (
    ArrayType, PointerType, Results, SliceType, TYPE_NAMES, is_integer, is_string,
)

MAX_FUNCTION_INSTANCES = 128


@dataclass(frozen=True)
class ConstraintIdentity:
    name: str


ANY = ConstraintIdentity('any')
COMPARABLE = ConstraintIdentity('comparable')
PREDECLARED_CONSTRAINTS = {'any': ANY, 'comparable': COMPARABLE}


@dataclass(frozen=True)
class TypeParameter:
    owner: str
    index: int
    name: str
    constraint: ConstraintIdentity

    def __str__(self):
        return self.name


def contains_parameter(dtype):
    if isinstance(dtype, TypeParameter):
        return True
    if isinstance(dtype, (ArrayType, PointerType, SliceType)):
        return contains_parameter(dtype.element)
    if isinstance(dtype, Results):
        return any(contains_parameter(item) for item in dtype.elements)
    if isinstance(dtype, StructType):
        return any(contains_parameter(field.type) for field in dtype.fields)
    if isinstance(dtype, InterfaceType):
        return any(contains_parameter(item) for method in dtype.methods
                   for item in method.signature.parameters + (method.signature.result,))
    return False


def substitute(dtype, bindings, token):
    if isinstance(dtype, TypeParameter):
        return bindings.get(dtype, dtype)
    if isinstance(dtype, Results):
        return Results(tuple(substitute(item, bindings, token) for item in dtype.elements))
    if isinstance(dtype, ArrayType):
        from pcc.frontends.go.sema import checked_array_type
        return checked_array_type(token, dtype.count, substitute(dtype.element, bindings, token))
    # Concrete nominal types retain declaration identity; dependent pointer
    # and record layouts still fail during source type resolution.
    return dtype


def infer_argument(formal, actual, token, parameters, explicit, bindings, *, exact=False):
    """Assignment equations are loose at the root, exact inside an array.

    In particular, [2]Named and [2]int cannot agree merely because Named's
    underlying type is int. A named outer array may still match [2]T.
    Untyped constants are collected separately by select, never recursively.
    """
    if isinstance(formal, TypeParameter) and formal in parameters:
        if formal in explicit:
            return  # Source assignability is checked after substitution.
        previous = bindings.get(formal)
        if previous is None:
            bindings[formal] = actual
        elif previous != actual:
            if not exact and same_underlying(previous, actual) and not (is_named(previous) and is_named(actual)):
                if is_named(actual):
                    bindings[formal] = actual
            else:
                raise GoDiagnostic(token, 'conflicting inferred type arguments')
        return
    if isinstance(formal, ArrayType) and contains_parameter(formal):
        actual_array = underlying(actual) if not exact else actual
        if not isinstance(actual_array, ArrayType) or formal.count != actual_array.count:
            raise GoDiagnostic(token, 'cannot infer type arguments from incompatible array argument')
        infer_argument(formal.element, actual_array.element, token, parameters, explicit,
                       bindings, exact=True)


def type_code(dtype):
    if interface_type(dtype) is not None or isinstance(dtype, SliceType):
        raise AssertionError('interface/signature-only type reached Go symbol encoding')
    if isinstance(dtype, DefinedType):
        return ['defined', dtype.package, dtype.name]
    if isinstance(dtype, ArrayType):
        return ['array', dtype.count, type_code(dtype.element)]
    if isinstance(dtype, StructType):
        # Keep existing untagged symbol spellings stable. Tag bytes and
        # embeddedness distinguish otherwise identical anonymous arguments.
        return ['struct', [
            [field.name, field.package, type_code(field.type)] +
            ([field.embedded, field.tag.hex()] if field.embedded or field.tag else [])
            for field in dtype.fields]]
    if isinstance(dtype, (TypeParameter, PointerType)):
        raise AssertionError('nonconcrete or pointer type argument reached Go symbol encoding')
    return ['scalar', dtype]


def comparable(dtype):
    if isinstance(dtype, TypeParameter):
        return dtype.constraint is COMPARABLE
    dtype = underlying(dtype)
    if isinstance(dtype, ArrayType):
        return comparable(dtype.element)
    if isinstance(dtype, StructType):
        return all(comparable(field.type) for field in dtype.fields)
    if isinstance(dtype, InterfaceType):
        # Interface comparison is legal, but dynamic values may panic.
        # Instantiation/value admission retains its separate capability gate.
        return True
    return isinstance(dtype, PointerType) or is_integer(dtype) or is_string(dtype) or dtype == 'bool'


class GenericSession:
    """One explicit package graph's declarations, checks and shared worklist."""
    def __init__(self):
        self.instances = InstanceRegistry()
        self.parameters = {}
        self.owners = {}
        self.raw_bodies = {}
        self.abstract_calls = []

    def prepare(self, owner, function):
        scope = {}
        parameters = []
        declared_names = {name.text for name, _ in function.type_parameters}
        for index, (name, constraint) in enumerate(function.type_parameters):
            resolved_constraint = self.resolve_constraint(owner, constraint, declared_names)
            owner.check_name(name)
            if name.text == '_':
                raise UnsupportedGo(name, 'go.unsupported.generic-boundaries',
                                    'blank type parameter bindings are not implemented')
            if name.text in scope:
                raise GoDiagnostic(name, 'duplicate type parameter ' + name.text)
            parameter = TypeParameter(function.abi_symbol, index, name.text, resolved_constraint)
            parameters.append(parameter)
            scope[name.text] = parameter
        if parameters:
            self.parameters[id(function)] = tuple(parameters)
            self.owners[id(function)] = owner
            self.raw_bodies[id(function)] = deepcopy(function.body)
        function.type_bindings = scope
        return scope

    def resolve_constraint(self, owner, token, declared_names):
        # Type parameters are in scope throughout this signature. Do not
        # interpret a shadowed spelling as a predeclared constraint identity.
        if token.text in declared_names:
            raise GoDiagnostic(token, 'type parameter cannot be used as a constraint')
        imported = owner.dot_imported(token)
        if imported is not None:
            if token.text in imported.types:
                raise UnsupportedGo(token, 'go.unsupported.generic-boundaries',
                                    'named and exact-type constraints require general type-set resolution')
            raise GoDiagnostic(token, 'constraint must denote a type')
        if token.text in owner.globals or token.text in owner.functions:
            raise GoDiagnostic(token, 'constraint must denote a type')
        if token.text in owner.types:
            raise UnsupportedGo(token, 'go.unsupported.generic-boundaries',
                                'named and exact-type constraints require general type-set resolution')
        if token.text in owner.imports.get(token.filename, {}):
            raise GoDiagnostic(token, 'package name is not a type constraint')
        constraint = PREDECLARED_CONSTRAINTS.get(token.text)
        if constraint is None:
            if token.text in TYPE_NAMES:
                raise UnsupportedGo(token, 'go.unsupported.generic-boundaries',
                                    'exact-type constraints require general type-set resolution')
            raise GoDiagnostic(token, 'undefined type constraint ' + token.text)
        # Package/import/type-parameter declarations shadowing predeclared
        # names currently have their own explicit frontend frontier. Neither
        # that frontier nor future admitted shadows may silently bind here.
        return constraint

    def resolve_argument(self, caller, expression):
        if expression.kind == 'name' and expression.value in ('any', 'error', 'comparable'):
            raise UnsupportedGo(expression.token, 'go.unsupported.generic-boundaries',
                                'interface/constraint type arguments require their own value representation')
        if expression.kind == 'name' and expression.value in ('float32', 'float64', 'complex64', 'complex128'):
            code = 'floating-types' if expression.value.startswith('float') else 'complex-types'
            raise UnsupportedGo(expression.token, 'go.unsupported.' + code,
                                'type argument requires unimplemented numeric representation')
        dtype = caller.type_operand(expression)
        if dtype is None:
            raise GoDiagnostic(expression.token, 'generic type argument must denote a type')
        if interface_type(dtype) is not None:
            raise UnsupportedGo(expression.token, 'go.unsupported.generic-boundaries',
                                'interface type arguments require abstract dynamic-value checking')
        return dtype

    def select(self, caller, function, expressions, explicit=None):
        from pcc.frontends.go.sema import default_type
        parameters = self.parameters.get(id(function), ())
        if not parameters:
            if explicit is not None:
                raise GoDiagnostic(function.token, 'type arguments supplied to a nongeneric function')
            return function
        values = caller.values(expressions)
        if any(value.type == 'void' for value in values):
            raise GoDiagnostic(function.token, 'void function call used as a value')
        if len(values) != len(function.params):
            raise GoDiagnostic(function.token, 'argument count mismatch for ' + function.token.text)
        arguments = [] if explicit is None else [self.resolve_argument(caller, arg) for arg in explicit]
        if len(arguments) > len(parameters):
            raise GoDiagnostic(function.token, 'too many generic type arguments')
        bindings = dict(zip(parameters, arguments))
        explicit_parameters = set(bindings)
        constants = {}
        for (_, formal), value in zip(function.params, values):
            if isinstance(value.type, str) and value.type.startswith('untyped '):
                if isinstance(formal, TypeParameter) and formal in parameters:
                    constants.setdefault(formal, []).append(value)
                continue
            infer_argument(formal, value.type, value.token, parameters, explicit_parameters, bindings)
        for parameter in parameters:
            if parameter not in bindings:
                types = {value.type for value in constants.get(parameter, []) if value.type != 'untyped nil'}
                if types and types <= {'untyped int', 'untyped rune'}:
                    kind = 'untyped rune' if 'untyped rune' in types else 'untyped int'
                elif len(types) == 1:
                    kind = next(iter(types))
                else:
                    raise GoDiagnostic(function.token, 'cannot infer type argument ' + parameter.name)
                bindings[parameter] = default_type(kind)
            argument = bindings[parameter]
            if interface_type(argument) is not None:
                raise UnsupportedGo(function.token, 'go.unsupported.generic-boundaries',
                                    'interface type arguments require abstract dynamic-value checking')
            if isinstance(underlying(argument), PointerType):
                raise UnsupportedGo(function.token, 'go.unsupported.generic-boundaries',
                                    'pointer type arguments require an abstract escape/result contract')
            if parameter.constraint is COMPARABLE and not comparable(argument):
                raise GoDiagnostic(function.token, str(argument) + ' does not satisfy comparable')
        arguments = tuple(bindings[parameter] for parameter in parameters)
        params = [(token, substitute(dtype, bindings, token)) for token, dtype in function.params]
        result = substitute(function.result, bindings, function.token)
        if caller.abstract_function:
            # A signature-only selection is sufficient to check this source
            # call. It never requests code or substitutes a favorable concrete
            # body for the independent declaration check.
            stub = replace(function, params=params, result=result, body=[], locals=[], parameters=[],
                           temporaries=[], result_symbols=[], type_parameters=[], type_bindings={},
                           abi_symbol=function.abi_symbol + '_abstract_' + str(len(self.abstract_calls)))
            self.abstract_calls.append(stub)
            return stub
        if any(contains_parameter(argument) for argument in arguments):
            raise AssertionError('abstract Go type escaped declaration checking')
        key = (id(function), arguments)
        existing = self.instances.get(key)
        if existing is not None:
            return existing.value
        if len(self.instances) >= MAX_FUNCTION_INSTANCES:
            raise UnsupportedGo(function.token, 'go.unsupported.limit-generic-instances',
                                'generic function instances exceed implementation limit (128)')
        spelling = json.dumps([type_code(argument) for argument in arguments],
                              ensure_ascii=True, separators=(',', ':')).encode('ascii').hex()
        instance = replace(function, params=params, result=result,
                           body=deepcopy(self.raw_bodies[id(function)]), locals=[], parameters=[],
                           temporaries=[], result_symbols=[], type_parameters=[],
                           type_bindings={parameter.name: bindings[parameter] for parameter in parameters},
                           abi_symbol=function.abi_symbol + '_instance_' + spelling)
        return self.instances.request(key, instance, self.owners[id(function)]).value

    def materialize(self):
        def build(entry):
            owner = entry.context
            owner.analyze_function(entry.value)
            owner.program.functions.append(entry.value)
            return entry.value
        # No package is emitted/finalized before all cross-package requests
        # (including requests made by instances) have drained to completion.
        self.instances.drain(build)

    def validate_abstract(self, programs):
        from pcc.frontends.go.pointer_flow import validate_pointer_flow
        from pcc.frontends.go.syntax import Program
        functions = [function for program in programs for function in program.functions]
        # Abstract calls retain concrete pointer parameters and their source
        # obligations. No pointer type argument is admitted, and generic
        # declarations themselves are independently checked even when unused.
        validate_pointer_flow(Program(programs[-1].token, functions + self.abstract_calls, []))
