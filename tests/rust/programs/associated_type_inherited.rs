trait Base { type Item: Copy; fn first(self) -> Self::Item; }
trait Child: Base { fn second(self) -> Self::Item; }
struct R { value: u64 }
impl Child for R {
    fn second(self) -> Self::Item {
        let value: Self::Item = self.value;
        value + value
    }
}
impl Base for R {
    type Item = u64;
    fn first(self) -> u64 { self.value }
}
pub fn answer() -> u64 {
    let first = R { value: 21 }.second();
    let second = R { value: 42 }.first();
    if first == second {
        0xfedcba9876543210u64
    } else { 0 }
}
