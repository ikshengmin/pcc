# Keep a dynamic input and a real lexical owning operand root.
def project(value) -> float:
    return float(value)


def literal() -> float:
    return float(3.5)


def typed(value: float) -> float:
    return float(value)
