trait Root { fn value(self) -> u64; }
trait Left: Root { fn left(self) -> u64; }
trait Right: Root { fn right(self) -> u64; }
trait Leaf: Left + Right + Root + Root { fn leaf(self) -> u64; }
struct R<T> { x: T }
impl Leaf for R<u64> { fn leaf(self) -> u64 { self.x + 2 } }
impl Right for R<u64> { fn right(self) -> u64 { self.x + 1 } }
impl Left for R<u64> { fn left(self) -> u64 { self.x } }
impl Root for R<u64> { fn value(self) -> u64 { self.x } }
impl<T> R<T> { fn value(self) -> u64 { 900 } }
pub fn answer() -> u64 {
    if <R<u64> as Root>::value(R::<u64>{x:40}) == 40
        && R::<u64>{x:40}.value() == 900
        && R::<u64>{x:40}.leaf() == 42
        && R::<u64>{x:40}.right() == 41
        && R::<u64>{x:40}.left() == 40 {
        0xfedcba9876543210u64
    } else { 0 }
}
