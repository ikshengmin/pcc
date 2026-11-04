trait Root { type Item: ?Sized; fn root(self) -> Self::Item; }
trait Left: Root {}
trait Right: Root {}
trait Bottom: Left + Right {
    fn bottom(self, item: Self::Item) -> Self::Item;
}
struct R { x: u64 }
impl Bottom for R {
    fn bottom(self, item: Self::Item) -> Self::Item { self.x + item }
}
impl Right for R {}
impl Left for R {}
impl Root for R {
    type Item = u64;
    fn root(self) -> Self::Item { self.x }
}
pub fn answer() -> u64 {
    let root = <R as Root>::root(R { x: 20 });
    let result = <R as Bottom>::bottom(R { x: root }, 22u64);
    if result == 42 { 0xfedcba9876543210u64 } else { 0 }
}
