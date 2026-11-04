trait Root {
    type Item;
    fn echo(&self, value: Self::Item) -> Self::Item { value }
}
trait Child: Root {
    fn relay(&self, value: Self::Item) -> Self::Item { self.echo(value) }
}
struct R { value: u64 }
struct S { value: u64 }
impl Root for R { type Item = u64; }
impl Root for S { type Item = bool; }
impl Child for R {}
impl Child for S {}
impl R { fn echo(&self, value: u64) -> u64 { 400 } }
impl S { fn echo(&self, value: bool) -> bool { false } }
pub fn answer() -> u64 {
    let first = R { value: 0 };
    let second = S { value: 0 };
    if first.relay(42u64) == 42 && second.relay(true)
        && first.echo(42u64) == 400 && !second.echo(true) {
        0xfedcba9876543210u64
    } else { 0 }
}
