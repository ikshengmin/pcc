// Original PCC specification fixture: static items (Rust Reference
// items/static-items.html): one global place each, a `static mut` updated
// across calls inside `unsafe`, initializers reading consts and another
// static, a module static and a `&str` static.
static BASE: u64 = 0xfedcba9876543200;
static LABEL: &str = "statics agree";
static mut COUNTER: u64 = 0;
const STEP: u64 = 4;
static OFFSET: u64 = STEP * 4;
static ALIAS: u64 = OFFSET;
mod config { pub static VERSION: u32 = 1; }
fn bump() -> u64 { unsafe { COUNTER += STEP; COUNTER } }
pub fn answer() -> &'static str {
    let first = bump();
    let second = bump();
    let total = unsafe { COUNTER };
    if first == 4 && second == 8 && total == 8 && ALIAS == 16
        && BASE + ALIAS == 0xfedcba9876543210 && config::VERSION == 1 {
        LABEL
    } else {
        "statics differ"
    }
}
