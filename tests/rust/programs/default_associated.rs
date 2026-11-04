trait Carry {
    type Item;
    fn echo(&self, value: Self::Item) -> Self::Item { value }
}
struct R { value: u64 }
struct S { value: u64 }
impl Carry for R { type Item = u64; }
impl Carry for S { type Item = bool; }
pub fn answer() -> u64 {
    let first = R { value: 0 };
    let second = S { value: 0 };
    if first.echo(42u64) == 42 && second.echo(true) {
        0xfedcba9876543210u64
    } else { 0 }
}
