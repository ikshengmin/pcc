trait Copy {}
trait Sized {}
struct R { x: u64 }
trait Select {
    fn pair<Copy: core::marker::Copy>(&self, value: Copy) -> (Copy, Copy);
    fn keep<R: core::marker::Copy>(self, value: R) -> Self;
}
impl Select for R {
    fn pair<R: std::marker::Copy>(&self, value: R) -> (R, R) { (value, value) }
    fn keep<R: std::marker::Copy>(self, value: R) -> Self { self }
}
pub fn answer() -> u64 {
    let r = R { x: 42 };
    let p = r.pair(21u64);
    let r = r.keep::<bool>(true);
    if p.0 + p.1 == r.x { 0xfedcba9876543210 } else { 0 }
}
