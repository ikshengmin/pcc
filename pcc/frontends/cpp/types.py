"""C++ arithmetic, pointer and reference identity over PCC's target-owned C ABI.

Equal storage widths do not make distinct C++ types equal (long and long long
are a common example). Keep language identity/rank until Sema inserts explicit
conversions, then project the selected type to the ordinary C AST.
"""

from pcc.frontends.c.c_abi_layout import (
    builtin_integer_is_unsigned, builtin_scalar_layout, c_target_triple,
    integer_literal_type_name, integer_literal_value, long_double_width,
)


TYPE_WORDS = frozenset("void bool char wchar_t short int long signed unsigned float double".split())
_CODES = {
    "void": "v",
    "bool": "b", "char": "c", "wchar_t": "w", "signed char": "a", "unsigned char": "h",
    "short": "s", "unsigned short": "t", "int": "i", "unsigned int": "j",
    "long": "l", "unsigned long": "m", "long long": "x",
    "unsigned long long": "y", "float": "f", "double": "d", "long double": "e",
}
_RANKS = {"bool": 0, "char": 1, "signed char": 1, "unsigned char": 1,
          "short": 2, "unsigned short": 2, "int": 3, "unsigned int": 3, "wchar_t": 3,
          "long": 4, "unsigned long": 4, "long long": 5, "unsigned long long": 5}
_FLOAT_RANKS = {"float": 1, "double": 2, "long double": 3}


def canonical_type(words):
    """Validate a complete builtin decl-specifier sequence, not a width guess."""
    key = sorted(words)
    for name in _CODES:
        variants = [name.split()]
        if name in ("short", "long", "long long"):
            variants += [(name + " int").split(), ("signed " + name).split(),
                         ("signed " + name + " int").split()]
        elif name in ("unsigned short", "unsigned long", "unsigned long long"):
            variants.append((name + " int").split())
        elif name == "int":
            variants += [["signed"], ["signed", "int"]]
        elif name == "unsigned int":
            variants.append(["unsigned"])
        for variant in variants:
            if sorted(variant) == key:
                return name
    raise ValueError("invalid or unsupported arithmetic type: " + " ".join(words))


def c_type_names(name):
    if is_enum_type(name):
        name = unqualified(name).underlying
    return ["_Bool"] if name == "bool" else name.split()


class ConstType:
    """Top-level const is distinct from const on a pointer's pointee."""

    def __init__(self, base):
        self.base = base

    def __eq__(self, other):
        return isinstance(other, ConstType) and self.base == other.base

    def __hash__(self):
        return hash(("const", self.base))


class ArrayType:
    """An array is not a pointer alias: retain element type and bound."""

    def __init__(self, element, bound):
        self.element = element
        self.bound = bound

    def __eq__(self, other):
        return isinstance(other, ArrayType) and self.element == other.element and self.bound == other.bound

    def __hash__(self):
        return hash(("array", self.element, self.bound))


def array_type(dtype):
    return isinstance(dtype, ArrayType)


def contains_array(dtype):
    dtype = unqualified(dtype)
    return (array_type(dtype) or isinstance(dtype, PointerType) and contains_array(dtype.pointee)
            or isinstance(dtype, ReferenceType) and contains_array(dtype.referent))


class FunctionType:
    """Function type identity, independent of an entity's name linkage.

    N4659 [dcl.fct], [dcl.link]. Callback declarations retain this structure
    through the C AST projection; it is never replaced by an object pointer.
    """
    def __init__(self, result, parameters, language_linkage="C++"):
        self.result = result
        self.parameters = tuple(unqualified(p) for p in parameters)
        self.language_linkage = language_linkage

    def __eq__(self, other):
        return (isinstance(other, FunctionType) and self.result == other.result
                and self.parameters == other.parameters
                and self.language_linkage == other.language_linkage)

    def __hash__(self):
        return hash((FunctionType, self.result, self.parameters, self.language_linkage))


def function_type(dtype):
    return isinstance(unqualified(dtype), FunctionType)


def contains_function(dtype):
    dtype = unqualified(dtype)
    if isinstance(dtype, ReferenceType):
        return contains_function(dtype.referent)
    if isinstance(dtype, (PointerType, ArrayType)):
        return contains_function(dtype.pointee if isinstance(dtype, PointerType) else dtype.element)
    return isinstance(dtype, FunctionType)


class ReferenceType:
    """An lvalue reference has identity, not nullable/rebindable pointer semantics."""

    def __init__(self, referent):
        self.referent = referent

    def __eq__(self, other):
        return isinstance(other, ReferenceType) and self.referent == other.referent

    def __hash__(self):
        return hash(("lvalue-reference", self.referent))


def reference_type(dtype):
    return isinstance(dtype, ReferenceType)


class PointerType:
    def __init__(self, pointee):
        self.pointee = pointee

    def __eq__(self, other):
        return isinstance(other, PointerType) and self.pointee == other.pointee

    def __hash__(self):
        return hash(("pointer", self.pointee))


def const_qualified(dtype):
    return isinstance(dtype, ConstType) or array_type(dtype) and const_qualified(dtype.element)


def unqualified(dtype):
    if array_type(dtype):
        return ArrayType(unqualified(dtype.element), dtype.bound)
    return dtype.base if isinstance(dtype, ConstType) else dtype


def qualify_const(dtype):
    if reference_type(dtype):
        return dtype  # [dcl.ref]: cv introduced through a typedef is ignored.
    if function_type(dtype):
        return dtype  # [dcl.fct]: cv added through a typedef-name is ignored.
    if array_type(dtype):
        return ArrayType(qualify_const(dtype.element), dtype.bound)
    return dtype if isinstance(dtype, ConstType) else ConstType(dtype)


def pointer_type(dtype):
    return isinstance(unqualified(dtype), PointerType)


def pointer_qualification_signature(dtype):
    """[conv.qual] decomposition for ordinary pointers and const-only cv.

    Arrays remain a leaf here; existing array-of-pointer capability gates stay
    separate. The outermost pointer cv is not part of a value conversion.
    """
    qualifiers = []
    while pointer_type(dtype):
        qualifiers.append(const_qualified(dtype))
        dtype = unqualified(dtype).pointee
    qualifiers.append(const_qualified(dtype))
    return unqualified(dtype), tuple(qualifiers)


def pointer_qualification_conversion(source, destination):
    if not pointer_type(source) or not pointer_type(destination):
        return False
    a, src = pointer_qualification_signature(source)
    b, dst = pointer_qualification_signature(destination)
    if a != b or len(src) != len(dst):
        return False
    for i in range(1, len(src)):
        if src[i] and not dst[i]:
            return False
        if dst[i] and not src[i] and not all(dst[1:i]):
            return False
    return True


def composite_qualified_pointer(first, second):
    """[expr.type] qualification-combined type of two similar pointers."""
    a, left = pointer_qualification_signature(first)
    b, right = pointer_qualification_signature(second)
    if a != b or len(left) != len(right):
        return None
    qualifiers = [x or y for x, y in zip(left, right)]
    for i in range(1, len(left)):
        if left[i] != right[i]:
            for j in range(1, i):
                qualifiers[j] = True
    result = qualify_const(a) if qualifiers[-1] else a
    for i in range(len(qualifiers) - 2, -1, -1):
        result = PointerType(result)
        if i and qualifiers[i]:
            result = qualify_const(result)
    return result


def is_enum_type(dtype):
    return bool(getattr(unqualified(dtype), "cpp_enum_type", False))


def _substitution(index):
    if index == 0:
        return "S_"
    digits, number = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ", ""
    index -= 1
    while True:
        number = digits[index % 36] + number
        index //= 36
        if index == 0:
            return "S" + number + "_"


def _mangle_class(dtype, substitutions):
    parts = dtype.qualified_name
    ordinal = getattr(dtype, "unnamed_ordinal", None)
    # Itanium 5.1.5.5: this is an unnamed-type-name, not a source-name.
    name = ("Ut" + (str(ordinal - 1) if ordinal else "") + "_"
            if ordinal is not None else str(len(parts[-1])) + parts[-1])
    namespaces = parts[:-1]
    enclosing_class = getattr(dtype, "enclosing_class", None)
    if enclosing_class is not None and getattr(enclosing_class, "template_origin", None) is not None:
        # Itanium 5.1.2: a member of a class template specialization is a
        # nested-name whose prefix is that specialization (itself a
        # substitution candidate), not the template's source name.
        outer = _mangle_type(enclosing_class, substitutions)
        prefix = outer[1:-1] if outer.startswith("N") else outer
        return "N" + prefix + name + "E"
    # For a member enum the last prefix is a nominal class type, not a
    # namespace with the same spelling. It shares the substitution entry
    # subsequently used by that enclosing class as a parameter/pointee type.
    def prefix_key(count):
        if enclosing_class is not None and count == len(namespaces):
            return enclosing_class
        return ("namespace-prefix", namespaces[:count])
    template_origin = getattr(dtype, "template_origin", None)
    template_prefix = ("class-template-prefix", template_origin)
    def encode_arguments():
        from pcc.frontends.cpp.templates import IntegralArgument, mangle_argument
        return "".join(mangle_argument(arg) if isinstance(arg, IntegralArgument)
                       else _mangle_type(arg, substitutions)
                       for arg in dtype.template_arguments)
    if template_origin is not None and template_prefix in substitutions:
        encoded = _substitution(substitutions.index(template_prefix)) + "I" + encode_arguments() + "E"
        # Namespace template members keep nested-name framing even when the
        # entire template prefix is substituted (Itanium ABI 5.1.2). std is
        # the ABI's explicit unscoped-name exception, just like global scope.
        return "N" + encoded + "E" if namespaces and namespaces != ("std",) else encoded
    def template_name():
        if template_origin is None:
            return name
        key = ("class-template-prefix", template_origin)
        if key not in substitutions:
            substitutions.append(key)
        return name + "I" + encode_arguments() + "E"
    if not namespaces:
        return template_name()
    if namespaces == ("std",) and enclosing_class is None:
        return "St" + template_name()
    prefix, start = "", 0
    for count in range(len(namespaces), 0, -1):
        key = prefix_key(count)
        if key in substitutions:
            prefix, start = _substitution(substitutions.index(key)), count
            break
    if not prefix and namespaces[0] == "std" and (len(namespaces) > 1 or enclosing_class is None):
        prefix, start = "St", 1
    for index in range(start, len(namespaces)):
        part = namespaces[index]
        prefix += str(len(part)) + part
        key = prefix_key(index + 1)
        if key not in substitutions:
            substitutions.append(key)
    return "N" + prefix + template_name() + "E"


def _mangle_type(dtype, substitutions):
    if dtype == "nullptr":
        return "Dn"
    if dtype in substitutions:
        return _substitution(substitutions.index(dtype))
    if isinstance(dtype, ConstType):
        encoded = "K" + _mangle_type(dtype.base, substitutions)
    elif isinstance(dtype, ArrayType):
        encoded = "A" + ("" if dtype.bound is None else str(dtype.bound)) + "_" + _mangle_type(dtype.element, substitutions)
    elif isinstance(dtype, FunctionType):
        encoded = ("F" + ("Y" if dtype.language_linkage == "C" else "")
                   + _mangle_type(dtype.result, substitutions)
                   + ("".join(_mangle_type(p, substitutions) for p in dtype.parameters) or "v") + "E")
    elif isinstance(dtype, ReferenceType):
        encoded = "R" + _mangle_type(dtype.referent, substitutions)
    elif isinstance(dtype, PointerType):
        encoded = "P" + _mangle_type(dtype.pointee, substitutions)
    elif getattr(dtype, "cpp_class_type", False) or is_enum_type(dtype):
        encoded = _mangle_class(dtype, substitutions)
    else:
        return _CODES[dtype]
    substitutions.append(dtype)
    return encoded


ALLOCATION_OPERATOR_CODES = {"operator new": "nw", "operator new[]": "na",
                             "operator delete": "dl", "operator delete[]": "da"}


def mangle_function(name, parameters, namespace_path=(), variadic=False):
    if name == "main" and not namespace_path:
        return name
    # Namespace prefixes participate in the ABI substitution table before
    # repeated pointer/cv types. Keep semantic path components, not "A::f".
    substitutions = [("namespace-prefix", namespace_path[:i + 1])
                     for i in range(len(namespace_path))]
    encoded_name = ALLOCATION_OPERATOR_CODES.get(name, str(len(name)) + name)
    if namespace_path == ("std",):
        encoded_name = "St" + encoded_name
        substitutions = []
    elif namespace_path:
        prefix = ""
        parts = namespace_path
        if parts[0] == "std":
            prefix, parts = "St", parts[1:]
            substitutions = substitutions[1:]
        encoded_name = "N" + prefix + "".join(str(len(part)) + part for part in parts) + encoded_name + "E"
    return "_Z" + encoded_name + (
        "".join(_mangle_type(unqualified(p), substitutions) for p in parameters)
        + ("z" if variadic else "") or "v")



def mangle_object(name, namespace_path=(), local_name=False):
    """Itanium data encoding; externally linked global variables keep C names.

    Internal names are TU-private. Use the conventional GNU local-source-name
    'L' marker for explicit static/default-const objects, independently of an
    unnamed namespace's internal linkage.
    """
    encoded = ("L" if local_name else "") + str(len(name)) + name
    if not namespace_path:
        return "_Z" + encoded if local_name else name
    if namespace_path == ("std",):
        return "_ZSt" + encoded
    parts = namespace_path
    prefix = ""
    if parts[0] == "std":
        prefix, parts = "St", parts[1:]
    return "_ZN" + prefix + "".join(str(len(p)) + p for p in parts) + encoded + "E"


def scalar_mangling_code(name):
    return _CODES[name]


class ArithmeticTypes:
    def __init__(self, target_triple=None):
        self.target_triple = c_target_triple(target_triple)

    def width(self, name):
        name = unqualified(name)
        return builtin_scalar_layout(c_type_names(name), self.target_triple).size * 8

    def unsigned(self, name):
        name = unqualified(name)
        return name == "bool" or builtin_integer_is_unsigned(
            c_type_names(name), self.target_triple
        )

    def integral(self, name):
        return unqualified(name) in _RANKS

    def integral_operand(self, name):
        name = unqualified(name)
        return self.integral(name) or is_enum_type(name) and not name.scoped

    def arithmetic_operand(self, name):
        name = unqualified(name)
        return (name in _CODES and name != "void"
                or is_enum_type(name) and not name.scoped)

    def value_lowering_supported(self, name):
        # The owned backend has extended-float storage, not value/call ABI
        # lowering. Do not pretend those C++ operations lower to binary64.
        name = unqualified(name)
        return name != "long double" or long_double_width(self.target_triple) == 64

    def promote(self, name):
        name = unqualified(name)
        if is_enum_type(name):
            return name.promotion_type(self)
        if name == "wchar_t":
            # [conv.prom]: the distinct wide-character type promotes to the
            # first standard integer type covering its entire target range.
            width, unsigned = self.width(name), self.unsigned(name)
            low = 0 if unsigned else -(1 << (width - 1))
            high = (1 << (width if unsigned else width - 1)) - 1
            for candidate in ("int", "unsigned int", "long", "unsigned long", "long long", "unsigned long long"):
                bits, signless = self.width(candidate), self.unsigned(candidate)
                minimum = 0 if signless else -(1 << (bits - 1))
                maximum = (1 << (bits if signless else bits - 1)) - 1
                if minimum <= low and high <= maximum:
                    return candidate
            raise ValueError("target wchar_t range has no standard integer promotion")
        if self.integral(name) and _RANKS[name] < _RANKS["int"]:
            if self.width(name) < self.width("int") or not self.unsigned(name):
                return "int"
            return "unsigned int"
        return name

    def conversion_rank(self, source, destination, null_pointer=False):
        if array_type(source):
            source = PointerType(source.element)
        source, destination = unqualified(source), unqualified(destination)
        if array_type(destination):
            return None
        if source == "void" or destination == "void":
            return None
        if source == destination:
            return 0
        if isinstance(destination, PointerType):
            if null_pointer or source == "nullptr":
                return 2
            if isinstance(source, PointerType):
                if pointer_qualification_conversion(source, destination):
                    return 0
                from pcc.frontends.cpp.inheritance import derived_to_base_pointer
                if derived_to_base_pointer(source, destination):
                    return 2  # [over.ics.scs] Table 12: pointer conversion
                if (unqualified(destination.pointee) == "void"
                        and not function_type(source.pointee)
                        and (not const_qualified(source.pointee)
                             or const_qualified(destination.pointee))):
                    return 2
            return None
        if isinstance(source, PointerType):
            return 2 if destination == "bool" else None
        if is_enum_type(destination):
            return None
        if is_enum_type(source):
            if source.scoped or destination not in _CODES:
                return None
            return 1 if destination in source.promotion_types(self) else 2
        if source not in _CODES or destination not in _CODES:
            return None
        if (self.integral(source) and self.promote(source) == destination
                or source == "float" and destination == "double"):
            return 1
        return 2

    def compare_conversions(self, source, first, second, null_pointer=False):
        """-1/0/1 means better/indistinguishable/worse for one argument.

        Conversion rank is not the complete ordering: qualification signatures
        and pointer-to-bool have C++ tie-breaks. Null pointer conversions do not
        acquire a qualification step merely because their target is const.
        """
        a = self.conversion_rank(source, first, null_pointer)
        b = self.conversion_rank(source, second, null_pointer)
        if a != b:
            return -1 if a < b else 1
        source, first, second = map(unqualified, (source, first, second))
        if is_enum_type(source) and source.fixed and not source.scoped:
            promotions = source.promotion_types(self)
            if first in promotions and second in promotions:
                if first == source.underlying and second != source.underlying:
                    return -1
                if second == source.underlying and first != source.underlying:
                    return 1
        if isinstance(source, PointerType):
            if (first == "bool") != (second == "bool"):
                return 1 if first == "bool" else -1
            if isinstance(first, PointerType) and isinstance(second, PointerType):
                a = pointer_qualification_conversion(first, second)
                b = pointer_qualification_conversion(second, first)
                if a != b:
                    return -1 if a else 1
                # [over.ics.rank]/4.4: D* to B* beats D* to void*, and a nearer
                # base beats a farther one.
                from pcc.frontends.cpp.inheritance import derived_to_base_pointer, is_proper_base
                to_first = derived_to_base_pointer(source, first)
                to_second = derived_to_base_pointer(source, second)
                if to_first and unqualified(second.pointee) == "void":
                    return -1
                if to_second and unqualified(first.pointee) == "void":
                    return 1
                if to_first and to_second:
                    a, b = unqualified(first.pointee), unqualified(second.pointee)
                    if is_proper_base(b, a):
                        return -1
                    if is_proper_base(a, b):
                        return 1
        return 0

    def common(self, lhs, rhs):
        """Usual arithmetic conversions, preserving type rank and signedness."""
        lhs, rhs = unqualified(lhs), unqualified(rhs)
        if is_enum_type(lhs):
            lhs = self.promote(lhs)
        if is_enum_type(rhs):
            rhs = self.promote(rhs)
        if lhs in _FLOAT_RANKS or rhs in _FLOAT_RANKS:
            return lhs if _FLOAT_RANKS.get(lhs, 0) >= _FLOAT_RANKS.get(rhs, 0) else rhs
        lhs, rhs = self.promote(lhs), self.promote(rhs)
        if lhs == rhs:
            return lhs
        if self.unsigned(lhs) == self.unsigned(rhs):
            return lhs if _RANKS[lhs] >= _RANKS[rhs] else rhs
        unsigned = lhs if self.unsigned(lhs) else rhs
        signed = rhs if self.unsigned(lhs) else lhs
        if _RANKS[unsigned] >= _RANKS[signed]:
            return unsigned
        if self.width(signed) > self.width(unsigned):
            return signed
        return "unsigned " + signed

    def integer_literal(self, raw):
        name, spelling, _ = self.integer_literal_info(raw)
        return name, spelling

    def integer_literal_info(self, raw):
        """Validate once and retain the target-typed integer's exact value."""
        # The shared C rules match C++17's ordinary integer literal candidates.
        # Validate suffix syntax first: the C utility intentionally only strips
        # suffix characters and must not admit e.g. 1lL as a C++ long-long token.
        if not raw.isascii():
            raise ValueError("non-ASCII integer literal")
        value = raw.rstrip("uUlL")
        suffix = raw[len(value):]
        valid = ("", "u", "U", "l", "L", "ll", "LL", "ul", "uL", "Ul", "UL",
                 "lu", "lU", "Lu", "LU", "ull", "uLL", "Ull", "ULL",
                 "llu", "llU", "LLu", "LLU")
        if suffix not in valid:
            raise ValueError("invalid integer literal suffix")
        name = integer_literal_type_name(raw, self.target_triple)
        number = integer_literal_value(raw)
        # Preserve the resolved literal type through the C AST, even when a
        # nondecimal source spelling chose an unsigned candidate.
        suffix = {"int": "", "unsigned int": "u", "long": "l", "unsigned long": "ul",
                  "long long": "ll", "unsigned long long": "ull"}[name]
        return name, str(number) + suffix, number

    def floating_literal_type(self, raw):
        """Validate syntax the reused C lexer intentionally leaves to Sema."""
        if not raw.isascii():
            raise ValueError("non-ASCII floating literal")
        suffix = raw[-1:] if raw[-1:] in ("f", "F", "l", "L") else ""
        body = raw[:-1] if suffix else raw
        body = body.lower()
        hexadecimal = body.startswith("0x")
        if hexadecimal:
            body = body[2:]
        marker = "p" if hexadecimal else "e"
        parts = body.split(marker)
        if len(parts) > 2 or hexadecimal and len(parts) != 2:
            raise ValueError("invalid floating literal exponent")
        if len(parts) == 2:
            exponent = parts[1]
            if exponent[:1] in ("+", "-"):
                exponent = exponent[1:]
            if not exponent or not all(c in "0123456789" for c in exponent):
                raise ValueError("invalid floating literal exponent")
        mantissa = parts[0]
        if mantissa.count(".") > 1 or (len(parts) == 1 and "." not in mantissa):
            raise ValueError("invalid floating literal mantissa")
        digits = mantissa.replace(".", "")
        alphabet = "0123456789abcdef" if hexadecimal else "0123456789"
        if not digits or not all(c in alphabet for c in digits):
            raise ValueError("invalid floating literal mantissa")
        return "float" if suffix in ("f", "F") else (
            "long double" if suffix in ("l", "L") else "double")
