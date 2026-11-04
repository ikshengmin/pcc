trait First { type Item; fn take(self) -> Self::Item; }
trait Second { type Item; fn take(self) -> Self::Item; }
struct R { value: u64 }
impl First for R {
    type Item = u64;
    fn take(self) -> Self::Item { self.value }
}
impl Second for R {
    type Item = i64;
    fn take(self) -> Self::Item { -(self.value as i64) }
}
pub fn answer() -> u64 {
    let positive = <R as First>::take(R { value: 50 });
    let negative = <R as Second>::take(R { value: 8 });
    if (positive as i64) + negative == 42 { 0xfedcba9876543210u64 } else { 0 }
}
