trait T { type Item: ?Sized; fn f(self) -> Self::Item; }
struct R { x: u64 }
impl T for R { type Item = u64; fn f(self) -> Self::Item { self.x } }
fn g() -> u64 { R { x: 42 }.f() }
pub fn answer() -> u64 {
    if g() == 42 { 0xfedcba9876543210u64 } else { 0 }
}
