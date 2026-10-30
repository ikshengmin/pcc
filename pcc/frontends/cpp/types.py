"""C++ arithmetic type identity over PCC's existing target-owned C ABI.

Equal storage widths do not make distinct C++ types equal (long and long long
are a common example). Keep language identity/rank until Sema inserts explicit
conversions, then project the selected type to the ordinary C AST.
"""

from pcc.frontends.c.c_abi_layout import (
    builtin_integer_is_unsigned, builtin_scalar_layout, c_target_triple,
    integer_literal_type_name, integer_literal_value, long_double_width,
)


TYPE_WORDS = frozenset("bool char short int long signed unsigned float double".split())
_CODES = {
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


def mangle_function(name, parameters):
    if name == "main":
        return name
    return "_Z" + str(len(name)) + name + ("".join(_CODES[p] for p in parameters) or "v")


class ArithmeticTypes:
    def __init__(self, target_triple=None):
        self.target_triple = c_target_triple(target_triple)

    def width(self, name):
        return builtin_scalar_layout(c_type_names(name), self.target_triple).size * 8

    def unsigned(self, name):
        return name == "bool" or builtin_integer_is_unsigned(
            c_type_names(name), self.target_triple
        )

    def integral(self, name):
        return name in _RANKS

    def value_lowering_supported(self, name):
        # The owned backend has extended-float storage, not value/call ABI
        # lowering. Do not pretend those C++ operations lower to binary64.
        return name != "long double" or long_double_width(self.target_triple) == 64

    def promote(self, name):
        if self.integral(name) and _RANKS[name] < _RANKS["int"]:
            if self.width(name) < self.width("int") or not self.unsigned(name):
                return "int"
            return "unsigned int"
        return name

    def conversion_rank(self, source, destination):
        # This is the arithmetic, by-value standard-conversion slice only.
        # No pointer/reference/user-defined conversion is silently ranked here.
        if source == destination:
            return 0
        if source not in _CODES or destination not in _CODES:
            return None
        if (self.integral(source) and self.promote(source) == destination
                or source == "float" and destination == "double"):
            return 1
        return 2

    def common(self, lhs, rhs):
        """Usual arithmetic conversions, preserving type rank and signedness."""
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
