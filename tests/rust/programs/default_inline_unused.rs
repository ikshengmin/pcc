trait Value {
    #[inline(always)]
    fn answer(&self) -> u64 { 42 }
}
trait Unused { fn identity<T>(&self, value: T) -> T { value } }
struct R { value: u64 }
impl Value for R {}
pub fn answer() -> u64 {
    let value = R { value: 0 };
    if value.answer() == 42 { 0xfedcba9876543210u64 } else { 0 }
}
