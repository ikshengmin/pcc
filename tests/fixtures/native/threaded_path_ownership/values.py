"""Ordinary-str native ownership probe; the host substitutes only oracle data."""
import gc
import os
from pcc.extern import extern, c_int64

threads_enabled = extern("pcc_threads_enabled", (), c_int64)
backend = extern("pcc_gc_backend", (), c_int64)

ABS_CASES = __ABS_CASES__
NORM_CASES = __NORM_CASES__
REL_CASES = __REL_CASES__
EXPECTED_BACKEND = __EXPECTED_BACKEND__
order = []


def fresh(value: str) -> str:
    # A returned allocation, not another reference to the oracle's literal.
    return (value + "#")[:-1]


def churn() -> None:
    garbage = []
    for i in range(128):
        garbage.append(("临时对象" * 40) + str(i))
    gc.collect()
    assert len(garbage) == 128


def first_argument(value: str) -> str:
    order.append(1)
    return fresh(value)


def second_argument(value: str) -> str:
    assert order[-1] == 1
    # The first result has no source-level owner while this code executes.
    churn()
    order.append(2)
    return fresh(value)


def second_error() -> str:
    assert order[-1] == 1
    churn()
    order.append(3)
    raise ValueError("second-operand-error")


def main():
    assert threads_enabled() == 1
    assert backend() == EXPECTED_BACKEND
    retained = []
    expected = []
    for path, answer in ABS_CASES:
        # Exercise the direct native_os abspath boundary with a fresh argument.
        result = os.path.abspath(fresh(path))
        assert result == answer
        retained.append(result)
        expected.append(answer)
        churn()
    for path, answer in NORM_CASES:
        result = os.path.normpath(fresh(path))
        assert result == answer
        retained.append(result)
        expected.append(answer)
        churn()
    for path, start, answer in REL_CASES:
        result = os.path.relpath(first_argument(path), second_argument(start))
        assert order[-2:] == [1, 2]
        assert result == answer
        retained.append(result)
        expected.append(answer)
        churn()
    try:
        os.path.relpath(first_argument("temporary/路径"), second_error())
    except ValueError as error:
        assert str(error) == "second-operand-error"
    else:
        raise AssertionError("second-argument exception was lost")
    assert order[-2:] == [1, 3]
    try:
        os.path.relpath("")
    except ValueError:
        pass
    else:
        raise AssertionError("empty relpath was accepted")
    for start in ["", "plain"]:
        try:
            os.path.relpath("", start)
        except ValueError:
            pass
        else:
            raise AssertionError("explicit empty relpath was accepted")
    # Includes null-result cleanup followed by a normal successful call.
    assert os.path.relpath(fresh("a/b"), fresh("a")) == "b"
    churn()
    for i in range(len(expected)):
        assert retained[i] == expected[i]
    print("path-values-and-lifetimes-ok")


main()
