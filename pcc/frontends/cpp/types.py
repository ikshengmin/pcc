"""C++ arithmetic/builtin-pointer identity over PCC's target-owned C ABI.

Equal storage widths do not make distinct C++ types equal (long and long long
are a common example). Keep language identity/rank until Sema inserts explicit
conversions, then project the selected type to the ordinary C AST.
"""

from pcc.frontends.c.c_abi_layout import (
    builtin_integer_is_unsigned, builtin_scalar_layout, c_target_triple,
    integer_literal_type_name, integer_literal_value, long_double_width,
)


TYPE_WORDS = frozenset("void bool char short int long signed unsigned float double".split())
_CODES = {
    "void": "v",
    "bool": "b", "char": "c", "signed char": "a", "unsigned char": "h",
    "short": "s", "unsigned short": "t", "int": "i", "unsigned int": "j",
    "long": "l", "unsigned long": "m", "long long": "x",
    "unsigned long long": "y", "float": "f", "double": "d", "long double": "e",
}
_RANKS = {"bool": 0, "char": 1, "signed char": 1, "unsigned char": 1,
          "short": 2, "unsigned short": 2, "int": 3, "unsigned int": 3,
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
    return ["_Bool"] if name == "bool" else name.split()


class ConstType:
    """Top-level const is distinct from const on a pointer's pointee."""

    def __init__(self, base):
        self.base = base

    def __eq__(self, other):
        return isinstance(other, ConstType) and self.base == other.base

    def __hash__(self):
        return hash(("const", self.base))


class PointerType:
    def __init__(self, pointee):
        self.pointee = pointee

    def __eq__(self, other):
        return isinstance(other, PointerType) and self.pointee == other.pointee

    def __hash__(self):
        return hash(("pointer", self.pointee))


def unqualified(dtype):
    return dtype.base if isinstance(dtype, ConstType) else dtype


def qualify_const(dtype):
    return dtype if isinstance(dtype, ConstType) else ConstType(dtype)


def pointer_type(dtype):
    return isinstance(unqualified(dtype), PointerType)


def _mangle_type(dtype, substitutions):
    if dtype in substitutions:
        index = substitutions.index(dtype)
        if index == 0:
            return "S_"
        digits, number = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ", ""
        index -= 1
        while True:
            number = digits[index % 36] + number
            index //= 36
            if index == 0:
                return "S" + number + "_"
    if isinstance(dtype, ConstType):
        encoded = "K" + _mangle_type(dtype.base, substitutions)
    elif isinstance(dtype, PointerType):
        encoded = "P" + _mangle_type(dtype.pointee, substitutions)
    else:
        return _CODES[dtype]
    substitutions.append(dtype)
    return encoded


def mangle_function(name, parameters, namespace_path=()):
    if name == "main" and not namespace_path:
        return name
    # Namespace prefixes participate in the ABI substitution table before
    # repeated pointer/cv types. Keep semantic path components, not "A::f".
    substitutions = [("namespace-prefix", namespace_path[:i + 1])
                     for i in range(len(namespace_path))]
    encoded_name = str(len(name)) + name
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
        "".join(_mangle_type(unqualified(p), substitutions) for p in parameters) or "v")


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

    def value_lowering_supported(self, name):
        # The owned backend has extended-float storage, not value/call ABI
        # lowering. Do not pretend those C++ operations lower to binary64.
        name = unqualified(name)
        return name != "long double" or long_double_width(self.target_triple) == 64

    def promote(self, name):
        name = unqualified(name)
        if self.integral(name) and _RANKS[name] < _RANKS["int"]:
            if self.width(name) < self.width("int") or not self.unsigned(name):
                return "int"
            return "unsigned int"
        return name

    def conversion_rank(self, source, destination, null_pointer=False):
        source, destination = unqualified(source), unqualified(destination)
        if source == "void" or destination == "void":
            return None
        if source == destination:
            return 0
        if isinstance(destination, PointerType):
            if null_pointer or source == "nullptr":
                return 2
            if isinstance(source, PointerType):
                # This boundary admits only one pointer level and const. A
                # qualification conversion never removes pointee const.
                if (isinstance(source.pointee, ConstType)
                        and not isinstance(destination.pointee, ConstType)):
                    return None
                if unqualified(source.pointee) == unqualified(destination.pointee):
                    return 0
                if unqualified(destination.pointee) == "void":
                    return 2
            return None
        if isinstance(source, PointerType):
            return 2 if destination == "bool" else None
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
        if isinstance(source, PointerType):
            if (first == "bool") != (second == "bool"):
                return 1 if first == "bool" else -1
            if (isinstance(first, PointerType) and isinstance(second, PointerType)
                    and unqualified(first.pointee) == unqualified(second.pointee)):
                a, b = isinstance(first.pointee, ConstType), isinstance(second.pointee, ConstType)
                if a != b:
                    return 1 if a else -1
        return 0

    def common(self, lhs, rhs):
        """Usual arithmetic conversions, preserving type rank and signedness."""
        lhs, rhs = unqualified(lhs), unqualified(rhs)
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
        return name, str(number) + suffix

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
