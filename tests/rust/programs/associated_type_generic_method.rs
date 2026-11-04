trait Pair {
    type Item;
    fn pair<U>(self, item: Self::Item, other: U) -> (Self::Item, U);
}
struct R { value: u64 }
fn identity<T>(value: T) -> T { value }
impl Pair for R {
    type Item = u64;
    fn pair<V>(self, item: Self::Item, other: V) -> (Self::Item, V) {
        let value: Self::Item = identity::<Self::Item>(item);
        (self.value + value, other)
    }
}
pub fn answer() -> u64 {
    let first = R { value: 20 }.pair(22u64, true);
    let second = <R as Pair>::pair::<i64>(R { value: 40 }, 2, -3);
    if first.0 == 42 && first.1 && second.0 == 42 && second.1 == -3 {
        0xfedcba9876543210u64
    } else { 0 }
}
