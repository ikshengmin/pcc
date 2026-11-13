// Original PCC specification fixture: `&'static str` values (Rust Reference
// tokens.html#string-literals, types/textual.html): escapes and UTF-8, an
// empty string, `&str` const items, tuple and struct fields, and returns
// through calls, as uv-version returns env!("CARGO_PKG_VERSION").
const NAME: &str = "uv";
const EMPTY: &'static str = "";
struct Named { name: &'static str, code: u64 }
fn pick(first: bool) -> &'static str { if first { "tab\tquote\"\u{e9}\u{1F600}\\é" } else { NAME } }
fn named(code: u64) -> Named { Named { name: "named-field", code } }
pub fn answer() -> &'static str {
    let pair = (pick(false), pick(true));
    let n = named(7);
    let blank = EMPTY;
    match n.code { 7 => pair.1, _ => blank }
}
pub fn short() -> &'static str { let n = named(1); if n.code == 1 { pick(false) } else { n.name } }
pub fn empty() -> &'static str { EMPTY }
