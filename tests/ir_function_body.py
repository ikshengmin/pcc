"""Select a complete emitted definition, never a descriptor or a declaration."""
import re


def function_body(ir_text: str, symbol: str) -> str:
    pattern = re.compile(
        r"^define[^\n]* @" + re.escape(symbol) + r"\([^\n]*\)\s*\{\n.*?^\}",
        re.MULTILINE | re.DOTALL,
    )
    matches = list(pattern.finditer(ir_text))
    assert len(matches) == 1, (symbol, "expected exactly one function definition", len(matches))
    return matches[0].group()
