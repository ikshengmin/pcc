// Original PCC specification fixture: restricted visibility (Rust Reference
// visibility-and-privacy.html), as uv-lock declares `pub(crate) mod export;`.
mod lock {
    pub(crate) mod export {
        pub(in crate::lock) fn bits()->u64 { 0x10 }
        pub(super) fn base()->u64 { 0xfedcba9876543200u64 }
        pub(crate) fn both()->u64 { base() + bits() }
    }
    pub struct Pair { pub(crate) x: u64, pub(self) y: u64 }
    pub(crate) fn pair()->Pair { Pair { x: export::bits(), y: 0 } }
    pub(crate) fn sum(p: Pair)->u64 { export::base() + p.x + p.y }
    pub(crate) use self::export::both as combined;
}
pub fn answer()->u64 {
    let p = lock::pair();
    if p.x != 0x10 || lock::combined() != lock::sum(p) { return 0; }
    lock::export::both()
}
