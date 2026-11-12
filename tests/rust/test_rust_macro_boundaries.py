"""`$` lexes as Rust punctuation; macros by example remain an explicit gap.

Rust Reference: https://doc.rust-lang.org/reference/tokens.html#punctuation
(`$` is a token) and https://doc.rust-lang.org/reference/macros-by-example.html.
No macro_rules! definition is recorded or expanded and no invocation is
evaluated: each reports a capability boundary at its own token, so the
parser's real first boundary in a source is no longer hidden by the lexer.
"""
import pytest

from pcc.frontends.rust import RustError
from pcc.frontends.rust.syntax import Parser, lex
from scripts.ci_source_frontier_support import diagnostic_status


def test_dollar_is_a_punctuation_token():
    tokens = lex("($x:expr) => {$x}")
    dollars = [token for token in tokens if token.text == "$"]
    assert len(dollars) == 2 and all(token.kind == "punct" for token in dollars)


@pytest.mark.parametrize("text,location,message", [
    ("macro_rules! m { ($x:expr) => {$x} } fn main(){}", "1:1",
     "macro_rules! definitions and item macro invocations require macro-by-example expansion"),
    ("m!{} fn main(){}", "1:1", "item macro invocations require macro-by-example expansion"),
    ("fn main(){ m!(); }", "1:12", "macro invocations require macro-by-example expansion"),
    ("fn f()->u64{ let v = m![1]; v }", "1:22", "macro invocations require macro-by-example expansion"),
    ("fn f(){let x=$;}", "1:14", "unsupported expression '\\$'"),
])
def test_macro_syntax_reports_a_located_capability_boundary(text, location, message):
    with pytest.raises(RustError, match="^" + location + ": .*" + message) as caught:
        Parser(text).parse()
    assert diagnostic_status(caught.value) == "UNSUPPORTED"


def test_inequality_is_not_a_macro_invocation():
    function, = Parser("fn f(a:bool,b:bool)->bool{a!=b}").parse()
    assert function.token.text == "f"


def test_orjson_root_reaches_its_first_parser_boundary_past_the_lexer():
    from scripts import ci_orjson_source_frontier as project
    _, files = project.load_inputs()
    source = files["src/lib.rs"].decode()
    assert "macro_rules! add" in source and "$mptr:expr" in source
    lex(source)  # Whole original lexes; previously 73:6 stopped at `$`.
    with pytest.raises(RustError, match="^src/lib.rs: 4:4: cfg_attr requires an actual source/options-bound "
                                        "CheckedRustSource configuration$") as caught:
        Parser(source, source_path="src/lib.rs", edition=2024).parse()
    assert diagnostic_status(caught.value) == "UNSUPPORTED"
