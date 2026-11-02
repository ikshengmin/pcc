struct R { x: u64 }
struct Pair<X, Y> { first: X, second: Y }
impl<A: Copy, B> Pair<B, A> {
    fn duplicate<Copy>(self, marker: Copy) -> (A, A, Copy) { (self.second, self.second, marker) }
}
impl<A, B> Pair<B, A> where A: Copy, A: Sized {
    fn make(first: B, second: A) -> Self { Self { first, second } }
    fn mirror<U: Copy>(&self, value: U) -> (A, A, U, U) { (self.second, self.second, value, value) }
}
impl<A, B> Pair<B, A> { fn unrelated(&self) -> u64 { 7 } }
fn forward<X: Copy, Y>(pair: &Pair<Y, X>) -> (X, X, bool, bool) { pair.mirror(true) }
pub fn answer() -> u64 {
    let pair = Pair::<R, u64>::make(R { x: 9 }, 42);
    let first = forward(&pair);
    let second = pair.duplicate(R { x: 8 });
    let unbounded = Pair::<u64, R> { first: 1, second: R { x: 2 } };
    if first.0 == 42 && first.1 == 42 && first.2 && first.3
        && second.0 == 42 && second.1 == 42 && second.2.x == 8
        && unbounded.unrelated() == 7 { 0xfedcba9876543210 } else { 0 }
}
