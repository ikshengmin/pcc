trait Parent { type Item; fn parent(self) -> Self::Item; }
trait Child: Parent { type Item; fn child(self) -> i64; }
trait Left { type Item; fn left(self) -> Self::Item; }
trait Right { type Item; fn right(self) -> Self::Item; }
trait Both: Left + Right { fn both(self) -> u64; }
struct R { x: u64 }
impl Child for R {
    type Item = i64;
    fn child(self) -> i64 { -(self.x as i64) }
}
impl Both for R { fn both(self) -> u64 { self.x } }
impl Right for R {
    type Item = i64;
    fn right(self) -> Self::Item { -(self.x as i64) }
}
impl Parent for R {
    type Item = u64;
    fn parent(self) -> Self::Item { self.x }
}
impl Left for R {
    type Item = u64;
    fn left(self) -> Self::Item { self.x }
}
pub fn answer() -> u64 {
    let parent = <R as Parent>::parent(R { x: 50 });
    let child = <R as Child>::child(R { x: 8 });
    let left = <R as Left>::left(R { x: 60 });
    let right = <R as Right>::right(R { x: 18 });
    let both = <R as Both>::both(R { x: 42 });
    if (parent as i64) + child == 42 && (left as i64) + right == 42 && both == 42 {
        0xfedcba9876543210u64
    } else { 0 }
}
