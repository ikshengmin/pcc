"""C++17 #if integer semantics over the shared preprocessing-expression parser.

PCC targets have 64-bit intmax_t/uintmax_t. Preserve their usual arithmetic
conversions without changing the legacy C evaluator. Unsupported/undefined live
operations diagnose rather than selecting an invented branch.
"""
import re

from pcc.frontends.c.preprocessor import _CppExprError, _CppExprParser

_MASK = (1 << 64) - 1
_MAX = (1 << 63) - 1
_MIN = -(1 << 63)


class _Parser(_CppExprParser):
    def parse_primary(self):
        self.skip_ws()
        if self._eat("("):
            result = self.parse_ternary()
            self._expect(")")
            return result
        if (self.src[self.pos:self.pos + 1] == "'"
                or self.src[self.pos:self.pos + 2] in ("L'", "u'", "U'")):
            from pcc.frontends.c.c_character_literals import parse_c_character_constant
            from pcc.frontends.c.c_abi_layout import builtin_integer_is_unsigned

            try:
                value, dtype, self.pos = parse_c_character_constant(
                    self.src, self.pos, self.target_triple, cpp=True)
            except ValueError as exc:
                raise _CppExprError(str(exc)) from exc
            return ("lit", value,
                    builtin_integer_is_unsigned(dtype.split(), self.target_triple))
        start = self.pos
        value = self.parse_number()
        spelling = self.src[start:self.pos]
        suffix = re.search(r"[uUlL]+$", spelling)
        text = suffix.group() if suffix is not None else ""
        if text and re.fullmatch(r"(?:[uU](?:[lL]|ll|LL)?|(?:[lL]|ll|LL)[uU]?)", text) is None:
            raise _CppExprError("invalid integer suffix: " + text)
        unsigned = "u" in text.lower()
        nondecimal = spelling.startswith("0") and len(spelling) > len(text) + 1
        if value > _MASK or (value > _MAX and not unsigned and not nondecimal):
            raise _CppExprError("integer literal exceeds supported intmax_t range")
        return ("lit", value, unsigned or value > _MAX)


def _unsigned(node):
    op = node[0]
    if op == "lit":
        return node[2]
    if op in ("!", "&&", "||", "<", "<=", ">", ">=", "==", "!="):
        return False
    if op in ("u+", "u-", "~", "<<", ">>"):
        return _unsigned(node[1])
    if op == "?:":
        return _unsigned(node[2]) or _unsigned(node[3])
    return _unsigned(node[1]) or _unsigned(node[2])


def _value(value, unsigned):
    if unsigned:
        return value & _MASK
    if value < _MIN or value > _MAX:
        raise _CppExprError("signed intmax_t overflow")
    return value


def _evaluate(node):
    op = node[0]
    unsigned = _unsigned(node)
    if op == "lit":
        return node[1]
    left = _evaluate(node[1])
    if op == "u+":
        return left
    if op == "u-":
        return _value(-left, unsigned)
    if op == "~":
        return _value(~left, unsigned)
    if op == "!":
        return int(not left)
    if op == "&&":
        return int(bool(left) and bool(_evaluate(node[2])))
    if op == "||":
        return int(bool(left) or bool(_evaluate(node[2])))
    if op == "?:":
        return _value(_evaluate(node[2] if left else node[3]), unsigned)
    right = _evaluate(node[2])
    if op in ("<<", ">>"):
        if right < 0 or right >= 64:
            raise _CppExprError("shift count outside intmax_t width")
        if op == ">>":
            return left >> right
        if not unsigned and left < 0:
            raise _CppExprError("left shift of a negative signed value")
        # C++17 permits a nonnegative signed lhs when the product fits the
        # corresponding unsigned type, then converts it back to signed.
        shifted = left << right
        if not unsigned:
            if shifted > _MASK:
                raise _CppExprError("signed left shift exceeds uintmax_t range")
            return shifted if shifted <= _MAX else shifted - (1 << 64)
        return shifted & _MASK
    common_unsigned = _unsigned(node[1]) or _unsigned(node[2])
    if common_unsigned:
        left &= _MASK
        right &= _MASK
    if op == "+": result = left + right
    elif op == "-": result = left - right
    elif op == "*": result = left * right
    elif op in ("/", "%"):
        if right == 0:
            raise _CppExprError("division by zero")
        quotient = abs(left) // abs(right)
        if (left < 0) != (right < 0):
            quotient = -quotient
        _value(quotient, common_unsigned)
        result = quotient if op == "/" else left - quotient * right
    elif op == "<": return int(left < right)
    elif op == "<=": return int(left <= right)
    elif op == ">": return int(left > right)
    elif op == ">=": return int(left >= right)
    elif op == "==": return int(left == right)
    elif op == "!=": return int(left != right)
    elif op == "&": result = left & right
    elif op == "|": result = left | right
    elif op == "^": result = left ^ right
    else: raise _CppExprError("unsupported C++ preprocessor operator: " + op)
    return _value(result, unsigned)


def evaluate_cpp17_condition(source, target_triple=None):
    parser = _Parser(source, target_triple)
    tree = parser.parse_ternary()
    parser.skip_ws()
    if parser.pos != len(source):
        raise _CppExprError("trailing C++ preprocessor expression input")
    return _evaluate(tree)
