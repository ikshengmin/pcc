"""Nominal namespace enums and checked enumerator constant semantics.

N4659 [dcl.enum], [conv.prom] and [over.ics.rank] distinguish enum identity,
underlying storage, promotion range and enumerator types during a definition.
The default unfixed ABI policy uses at least int-sized storage, unsigned when
all enumerators are nonnegative. It follows the supported GNU/Itanium targets,
not a host compiler query; short-enum and MSVC policies are not inferred.

This module shares Scope/Symbol and the typed constant-fact owner. It neither
rewrites enum declarations into source text nor invokes a C parser/evaluator.
Local, anonymous and dependent enum declarations remain explicit capability
boundaries. Named namespace/class enums need no C storage declaration.
"""

from pcc.backend.self_backend_target_match import (
    is_aarch64_darwin_triple,
    is_aarch64_linux_triple,
    is_x86_64_linux_triple,
    is_x86_64_windows_triple,
)
from pcc.frontends.cpp.constants import KNOWN, NOT_CONSTANT, known
from pcc.frontends.cpp.diagnostics import CppDiagnostic
from pcc.frontends.cpp.sema import Scope, Symbol
from pcc.frontends.cpp.types import unqualified


_PROMOTION_TYPES = ("int", "unsigned int", "long", "unsigned long",
                    "long long", "unsigned long long")


def integer_range(dtype, types):
    dtype = unqualified(dtype)
    if dtype == "bool":
        return 0, 1
    width = types.width(dtype)
    if types.unsigned(dtype):
        return 0, (1 << width) - 1
    return -(1 << (width - 1)), (1 << (width - 1)) - 1


def _target_policy(types, coord, unfixed=False):
    triple = types.target_triple
    # All of these owned scalar ABIs use two's-complement signed integers.
    # This is an explicit target policy, not an observation about Python ints.
    if not any(match(triple) for match in (
            is_aarch64_darwin_triple, is_aarch64_linux_triple,
            is_x86_64_linux_triple, is_x86_64_windows_triple)):
        raise CppDiagnostic("cpp.unsupported.enum-abi-policy", "unknown target enum representation policy", coord)
    if unfixed and is_x86_64_windows_triple(triple) and not triple.lower().endswith("-gnu"):
        raise CppDiagnostic("cpp.unsupported.enum-abi-policy", "unfixed enum ABI policy for non-GNU Windows is not implemented", coord)


def select_unfixed_underlying(minimum, maximum, types, coord=None):
    """Default GNU/Itanium storage policy, with no short-enum option."""
    _target_policy(types, coord, unfixed=True)
    candidates = ("int", "long", "long long")
    if minimum >= 0:
        candidates = tuple("unsigned " + name for name in candidates)
    for dtype in candidates:
        low, high = integer_range(dtype, types)
        if low <= minimum and maximum <= high:
            return dtype
    raise CppDiagnostic("cpp.enum-value-range", "no supported integer type contains every enumerator value", coord)


def unfixed_value_range(minimum, maximum, types, coord=None):
    """C++17 [dcl.enum]/8 range, separate from ABI storage width.

    K is explicitly 1 for the supported two's-complement targets. Empty/all-zero
    definitions have range (0, 0) under this C++17 wording; CWG2932 remains an
    open-policy distinction from later wording and implementation practice.
    """
    _target_policy(types, coord)
    magnitude = max(abs(minimum) - 1, abs(maximum))
    upper = (1 << magnitude.bit_length()) - 1
    return (0 if minimum >= 0 else -(upper + 1)), upper


class EnumType:
    """One nominal identity, including a complete-but-not-defined opaque enum."""

    cpp_enum_type = True

    def __init__(self, name, qualified_name, coord, scoped=False, underlying=None,
                 declaration_scope=None, internal_linkage=False, types=None):
        self.name = name
        self.qualified_name = tuple(qualified_name)
        self.coord = coord
        self.scoped = scoped
        self.underlying = unqualified(underlying) if underlying is not None else ("int" if scoped else None)
        self.fixed = self.underlying is not None
        self.declaration_scope = declaration_scope
        self.enclosing_class = getattr(declaration_scope, "class_type", None)
        self.internal_linkage = internal_linkage
        self.scope = Scope(declaration_scope, kind="enum", name=name)
        self.enumerators = []
        self.defined = False
        self.defining = False
        self.value_range = None
        if self.fixed and types is not None:
            self.complete_base(types)

    @property
    def complete(self):
        return self.fixed or self.defined

    def complete_base(self, types):
        if not self.fixed or not types.integral(self.underlying):
            raise CppDiagnostic("cpp.enum-underlying-type", "enum base must name an integral type", self.coord)
        _target_policy(types, self.coord)
        self.value_range = integer_range(self.underlying, types)

    def begin_definition(self):
        if self.defined or self.defining:
            raise CppDiagnostic("cpp.enum-redefinition", "redefinition of enum '" + self.name + "'", self.coord)
        self.defining = True

    def _initializer(self, fact, types, coord):
        if fact is None:
            raise CppDiagnostic("cpp.unsupported.enum-constant-expression", "enumerator requires unimplemented constant-expression semantics", coord)
        source = unqualified(fact.dtype)
        if getattr(source, "cpp_enum_type", False):
            if source.scoped:
                raise CppDiagnostic("cpp.enum-initializer-type", "scoped enum is not an implicit integral initializer", coord)
            if not source.complete:
                raise CppDiagnostic("cpp.incomplete-enum", "enumerator source enum is incomplete", coord)
            # [dcl.enum]/5.1 uses this underlying type, not its promotion type.
            source = source.underlying
        if not types.integral(source):
            raise CppDiagnostic("cpp.enum-initializer-type", "enumerator initializer must have integral or unscoped enum type", coord)
        if fact.status not in (KNOWN, NOT_CONSTANT):
            raise CppDiagnostic("cpp.unsupported.enum-constant-expression", "enumerator requires unimplemented constant-expression semantics", coord)
        if fact.status == NOT_CONSTANT:
            raise CppDiagnostic("cpp.enum-constant-expression", "enumerator is not a constant expression: " + fact.reason, coord)
        low, high = integer_range(source, types)
        if not isinstance(fact.value, int) or not low <= fact.value <= high:
            raise CppDiagnostic("cpp.unsupported.enum-constant-expression", "enumerator proof does not fit its source target type", coord)
        return source, fact.value

    def add_enumerator(self, name, coord, fact, types):
        """Install a body-phase Symbol; None means an implicit successor."""
        if not self.defining:
            raise ValueError("enumerators require an active enum definition")
        if name in self.scope.names:
            raise CppDiagnostic("cpp.redeclaration", "duplicate enumerator '" + name + "'", coord)
        if not self.scoped and self.enclosing_class is not None and name == self.enclosing_class.name:
            raise CppDiagnostic("cpp.class-name-member",
                                "an enumerator cannot have its enclosing class name", coord)
        if fact is not None:
            source, value = self._initializer(fact, types, coord)
        elif self.enumerators:
            previous = self.enumerators[-1]
            source, value = previous.dtype, previous.enum_value + 1
            low, high = integer_range(source, types)
            if not low <= value <= high and not self.fixed:
                source = None
                for candidate in _PROMOTION_TYPES:
                    low, high = integer_range(candidate, types)
                    if low <= value <= high:
                        source = candidate
                        break
                if source is None:
                    raise CppDiagnostic("cpp.enum-value-range", "implicit enumerator successor exceeds supported integer types", coord)
        else:
            source, value = "int", 0
        if self.fixed:
            low, high = integer_range(self.underlying, types)
            if not low <= value <= high:
                raise CppDiagnostic("cpp.enum-value-range", "enumerator is outside its fixed underlying type", coord)
            if fact is not None and self.underlying == "bool" and source != "bool":
                # [expr.const]/4 permits integral promotions/conversions but
                # not a boolean conversion in a converted constant expression
                # (CWG1407, NAD). Values 0/1 alone cannot prove this sequence.
                # An unscoped fixed-bool enum has source underlying bool here:
                # its conversion is an allowed enum promotion, not conv.bool.
                raise CppDiagnostic("cpp.enum-converted-constant",
                                    "enum initializer requires a converted constant expression without boolean conversion",
                                    coord)
            source = self.underlying
        symbol = Symbol(name, "enumerator", coord, dtype=source)
        symbol.enum_type = self
        symbol.enum_value = value
        symbol.constant_fact = known(source, value, coord)
        symbol.initialization_complete = True
        if self.scoped:
            self.scope.declare(symbol)
        else:
            parent = self.declaration_scope
            if parent is None:
                raise ValueError("unscoped enumerator requires its declaration scope")
            if self.enclosing_class is not None:
                symbol.class_owner = self.enclosing_class
                symbol.access = self.enclosing_class.definition.access
            previous = parent.names.get(name)
            if previous is not None and previous.kind == "type" and (
                    getattr(previous, "class_declaration", False)
                    or getattr(previous, "enum_declaration", False)):
                raise CppDiagnostic("cpp.unsupported.enum-tag-hiding", "enumerator hiding a tag requires elaborated-type lookup", coord)
            parent.declare(symbol)
            self.scope.names[name] = symbol
        self.enumerators.append(symbol)
        return symbol

    def finish(self, types):
        if not self.defining:
            raise ValueError("no active enum definition to finish")
        values = [item.enum_value for item in self.enumerators] or [0]
        if self.fixed:
            self.complete_base(types)
        else:
            self.underlying = select_unfixed_underlying(min(values), max(values), types, self.coord)
            self.value_range = unfixed_value_range(min(values), max(values), types, self.coord)
            low, high = integer_range(self.underlying, types)
            if self.value_range[0] < low or self.value_range[1] > high:
                # Keep the C++17 range formula separate from later hypothetical-
                # integer wording and compiler practice. Do not silently choose
                # an incompatible promotion for this unresolved edge policy.
                raise CppDiagnostic("cpp.unsupported.enum-range-policy", "C++17 enum value range exceeds selected ABI storage; edge policy is not implemented", self.coord)
        for symbol in self.enumerators:
            symbol.dtype = self
            symbol.constant_fact = known(self, symbol.enum_value, symbol.coord)
        self.defining = False
        self.defined = True
        return self

    def promotion_targets(self, types):
        return enum_promotion_types(self, types)

    def promotion_types(self, types):
        return enum_promotion_types(self, types)

    def promotion_type(self, types):
        return enum_promotion_type(self, types)


def enum_promotion_types(dtype, types):
    dtype = unqualified(dtype)
    if dtype.scoped:
        return ()
    if not dtype.complete:
        raise CppDiagnostic("cpp.incomplete-enum", "cannot promote an incomplete enum", dtype.coord)
    if dtype.fixed:
        promoted = types.promote(dtype.underlying)
        return ((dtype.underlying,) if promoted == dtype.underlying
                else (dtype.underlying, promoted))
    minimum, maximum = dtype.value_range
    for candidate in _PROMOTION_TYPES:
        low, high = integer_range(candidate, types)
        if low <= minimum and maximum <= high:
            return (candidate,)
    raise CppDiagnostic("cpp.unsupported.enum-promotion", "enum range requires an extended integral promotion", dtype.coord)


def enum_promotion_type(dtype, types):
    targets = enum_promotion_types(dtype, types)
    return targets[-1] if targets else None


def parse_enum(parser):
    """Consume a named namespace/class declaration without runtime storage."""
    keyword = parser._expect("enum")
    if parser.parsing_template or parser.template_function is not None:
        raise CppDiagnostic("cpp.unsupported.enum-templates", "dependent enum declarations are not implemented", keyword.coord)
    if parser.scope.kind not in ("namespace", "class"):
        raise CppDiagnostic("cpp.unsupported.enum-scope", "local enum declarations are not implemented", keyword.coord)
    if parser.scope.kind == "class":
        owner = getattr(parser.scope, "class_type", None)
        definition = getattr(owner, "definition", None)
        if definition is None or definition.scope is not parser.scope:
            raise CppDiagnostic("cpp.unsupported.enum-scope",
                                "class enum declaration requires its enclosing nominal class scope", keyword.coord)
    scoped = False
    if parser._peek().value in ("class", "struct"):
        parser.cursor.take()
        scoped = True
    if parser._peek().value in ("{", ":"):
        raise CppDiagnostic("cpp.unsupported.anonymous-enums", "anonymous enum identity/linkage is not implemented", parser._peek().coord)
    name = parser._identifier()
    if parser._peek().value == "::" or (parser._peek().value == ":"
            and parser.cursor.tokens[parser.cursor.position + 1].value == ":"):
        raise CppDiagnostic("cpp.unsupported.enum-qualified-definition", "qualified enum definitions are not implemented", name.coord)
    underlying = None
    if parser._accept(":"):
        underlying = unqualified(parser._type(check_lowering=False))
        if not parser.types.integral(underlying):
            raise CppDiagnostic("cpp.enum-underlying-type", "enum base must name an integral type", name.coord)
    elif scoped:
        underlying = "int"
    if not scoped and underlying is None and parser._peek().value == ";":
        raise CppDiagnostic("cpp.unsupported.enum-elaborated-declaration", "unfixed forward or elaborated enum declarations are not implemented", name.coord)
    parent = parser.scope
    enclosing_class = getattr(parent, "class_type", None)
    if enclosing_class is not None and name.value == enclosing_class.name:
        raise CppDiagnostic("cpp.class-name-member",
                            "a member enum cannot have its enclosing class name", name.coord)
    previous = parent.names.get(name.value)
    if getattr(previous, "namespace_object", False):
        raise CppDiagnostic("cpp.unsupported.tag-hiding",
                            "enum/object dual name lookup is not implemented", name.coord)
    if previous is not None:
        if not getattr(previous, "enum_declaration", False):
            raise CppDiagnostic("cpp.redeclaration", "enum name conflicts with another declaration", name.coord)
        if previous.owner is not parent:
            raise CppDiagnostic("cpp.unsupported.enum-redeclaration", "an imported enum cannot be completed in another scope", name.coord)
        dtype = previous.dtype
        if dtype.scoped != scoped or dtype.fixed != (underlying is not None):
            raise CppDiagnostic("cpp.enum-redeclaration", "enum scopedness or fixed-underlying status differs", name.coord)
        if dtype.fixed and dtype.underlying != underlying:
            raise CppDiagnostic("cpp.enum-underlying-mismatch", "enum underlying type differs from its prior declaration", name.coord)
        if enclosing_class is not None:
            if previous.access != enclosing_class.definition.access:
                raise CppDiagnostic("cpp.enum-access-redeclaration",
                                    "member enum redeclared with different access", name.coord)
            if parser._peek().value == ";":
                raise CppDiagnostic("cpp.redeclaration",
                                    "a member enum cannot be declared twice without a definition", name.coord)
    else:
        owner_name = enclosing_class.qualified_name if enclosing_class is not None else parent.namespace_path()
        dtype = EnumType(name.value, owner_name + (name.value,), name.coord,
                         scoped=scoped, underlying=underlying, declaration_scope=parent,
                         internal_linkage=parent.namespace().internal_linkage, types=parser.types)
        previous = parent.declare(Symbol(name.value, "type", name.coord, dtype=dtype))
        previous.enum_declaration = True
        if enclosing_class is not None:
            previous.class_owner = enclosing_class
            previous.access = enclosing_class.definition.access
    if parser._accept(";"):
        if not dtype.fixed:
            raise CppDiagnostic("cpp.unsupported.enum-elaborated-declaration", "unfixed enum requires a definition; elaborated enum declarations are not implemented", name.coord)
        return None
    if parser._peek().value != "{":
        raise CppDiagnostic("cpp.unsupported.enum-declarators", "enum definition followed by object declarators is not implemented", parser._peek().coord)
    dtype.begin_definition()
    parser._expect("{")
    parser.scope = dtype.scope
    try:
        if not parser._accept("}"):
            while True:
                enumerator = parser._identifier()
                fact = None
                if parser._accept("="):
                    expression = parser._expression()
                    parser._require_value(expression)
                    fact = parser._constant_fact(expression)
                dtype.add_enumerator(enumerator.value, enumerator.coord, fact, parser.types)
                if parser._accept("}"):
                    break
                parser._expect(",")
                if parser._accept("}"):
                    break
    finally:
        parser.scope = parent
    if parser._peek().value != ";":
        raise CppDiagnostic("cpp.unsupported.enum-declarators", "enum definitions with object declarators are not implemented", parser._peek().coord)
    parser._expect(";")
    dtype.finish(parser.types)
    return None
