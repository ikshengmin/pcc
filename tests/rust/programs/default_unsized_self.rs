fn read<T: ?Sized>(value: &T) -> u64 { 40 }
trait Value {
    fn answer(&self) -> u64 { read(self) + 2 }
}
struct R { value: u64 }
impl Value for R {}
pub fn answer() -> u64 {
    let value = R { value: 0 };
    if value.answer() == 42 { 0xfedcba9876543210u64 } else { 0 }
}
