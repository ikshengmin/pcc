struct R { x: u64 }
struct Pair<X, Y> { first: X, second: Y }
impl<A, B> Pair<B, A> {
    fn duplicate<U: Copy>(&self, value: U) -> (A, A, U, U) where A: Copy, A: Sized {
        (self.second, self.second, value, value)
    }
    fn make(first: B, second: A) -> Self where A: Copy { Self { first, second } }
    fn relaxed<U: ?Sized>(&self, value: &U) -> u64 where A: Sized { 7 }
    fn unrelated(&self) -> u64 { 7 }
}
fn forwarded<X: Copy, Y>(value: &Pair<Y, X>) -> (X, X, bool, bool) {
    value.duplicate(true)
}
pub fn answer() -> u64 {
    let pair = Pair::<R, u64>::make(R { x: 9 }, 42);
    let first = forwarded(&pair);
    let explicit = pair.duplicate::<u64>(8);
    let unbounded = Pair::<u64, R> { first: 1, second: R { x: 2 } };
    let flag = true;
    if first.0 == 42 && first.1 == 42 && first.2 && first.3
        && explicit.0 == 42 && explicit.1 == 42 && explicit.2 == 8 && explicit.3 == 8
        && pair.first.x == 9 && pair.relaxed(&flag) == 7 && unbounded.unrelated() == 7 {
        0xfedcba9876543210
    } else { 0 }
}
