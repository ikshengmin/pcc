#![no_std]
struct R { x: u64 }
trait Read {
    fn inspect<U: Sized>(&self, value: &U) -> u64;
    fn relaxed<U: ?Sized>(&self, value: &U) -> u64;
    fn forward<U: Sized>(&self, value: &U) -> u64;
}
impl Read for R {
    fn inspect<V: ?Sized>(&self, value: &V) -> u64 { self.relaxed(value) }
    fn relaxed<V: ?Sized>(&self, value: &V) -> u64 { self.x }
    fn forward<V: Sized>(&self, value: &V) -> u64 { self.inspect(value) }
}
pub fn answer() -> u64 {
    let r = R { x: 42 };
    let value = 1u64;
    if r.inspect(&value) == 42 && r.forward(&value) == 42
        && <R as Read>::inspect::<u64>(&r, &value) == 42 {
        0xfedcba9876543210
    } else { 0 }
}
