// Original PCC specification fixture: free const items (Rust Reference
// items/constant-items.html, const_eval.html) with forward references, folded
// operators, casts, bool consts, array lengths, a module const reached through
// `super::`, `use` and paths, as uv-lock declares `const VERSION: u32 = 1;`.
const VERSION: u32 = 1;
const BASE: u64 = HIGH | LOW;
const HIGH: u64 = 0xfedcba9876543200;
const LOW: u64 = 0x10 - 6 + 6;
const READY: bool = VERSION == 1 && !(LOW > 0x10);
const COUNT: usize = 3;
mod lock {
    pub const SHIFTED: u64 = super::LOW * 2;
}
use lock::SHIFTED;
pub fn answer()->u64 {
    let table = [VERSION as u64; COUNT];
    if !READY || table[2] != 1 || SHIFTED != 0x20 || lock::SHIFTED != crate::lock::SHIFTED { return 0; }
    BASE
}
