// Original PCC specification fixture: core's `matches!` macro, which expands
// to `match value { pattern => true, _ => false }`, with or-patterns,
// payload and tuple patterns, every delimiter and a trailing comma, as uv's
// `const fn needs_initialization(self) -> bool { matches!(self, Self::Initialize) }`.
#[derive(Clone, Copy, PartialEq)]
enum Init { Initialize, Reuse, Skip(u64) }
impl Init {
    const fn needs_initialization(self) -> bool { matches!(self, Self::Initialize) }
}
fn small(x: u64) -> bool { matches!(x, 0 | 1 | 2) }
fn skipped(init: Init) -> bool { matches![init, Init::Skip(7) | Init::Reuse,] }
pub fn answer() -> &'static str {
    let ok = Init::Initialize.needs_initialization() && !Init::Reuse.needs_initialization()
        && small(2) && !small(3) && skipped(Init::Skip(7)) && skipped(Init::Reuse)
        && !skipped(Init::Skip(8)) && matches!{(1u64, true), (1, true)};
    if ok { "matches agrees" } else { "matches differs" }
}
