// Original PCC specification fixture: const functions and methods (Rust
// Reference items/functions.html#const-functions) called at run time, as
// uv's `GlobalInitialization::needs_initialization` is a `const fn` method
// taking `self` by value.
#[derive(Clone, Copy, PartialEq)]
enum Initialization { Initialize, Reuse }
impl Initialization {
    const fn needs_initialization(self) -> bool {
        match self { Self::Initialize => true, Self::Reuse => false }
    }
    pub const fn code(self) -> u64 { if self.needs_initialization() { 7 } else { 3 } }
}
const fn double(x: u64) -> u64 { x * 2 }
const unsafe fn bump(x: u64) -> u64 { x + 1 }
pub fn answer() -> &'static str {
    let first = Initialization::Initialize;
    let other = Initialization::Reuse;
    let total = double(first.code()) + unsafe { bump(other.code()) };
    if total == 18 && first.needs_initialization() && !other.needs_initialization() {
        "const fn agrees"
    } else {
        "const fn differs"
    }
}
