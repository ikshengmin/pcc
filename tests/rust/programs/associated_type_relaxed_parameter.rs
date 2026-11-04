trait T { type Item: ?Sized; fn f(self, item: Self::Item) -> u64; }
struct R { x: u64 }
impl T for R {
    type Item = u64;
    fn f(self, item: Self::Item) -> u64 { self.x + item }
}
fn g() -> u64 { R { x: 20 }.f(22u64) }
pub fn answer() -> u64 {
    if g() == 42 { 0xfedcba9876543210u64 } else { 0 }
}
