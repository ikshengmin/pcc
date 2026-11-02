trait Copy {}
trait Sized {}
struct R { value: u64 }
struct Wrap<T> { item: T }
impl<T> Wrap<T> {
    fn pair<U: core::marker::Copy>(&self, value: U) -> (U, U) { (value, value) }
    fn forward<Copy, U: std::marker::Copy + core::marker::Sized>(&self, ignored: Copy, value: U) -> (U, U) {
        self.pair::<U>(value)
    }
    fn keep<R: core::marker::Copy>(self, ignored: R) -> Self { self }
}
pub fn answer() -> u64 {
    let w = Wrap::<R> { item: R { value: 42 } };
    let pair = w.forward(false, 21u64);
    let kept = w.keep::<bool>(true);
    if pair.0 + pair.1 == kept.item.value { 0xfedcba9876543210 } else { 0 }
}
