trait Value {
    fn base() -> u64 { 40 }
    fn answer() -> u64 { Self::base() + 2 }
    fn adjust(mut value: u64) -> u64 { value += 2; value }
}
struct R { value: u64 }
impl Value for R {}
impl R { fn base() -> u64 { 400 } }
pub fn answer() -> u64 {
    if R::answer() == 42 && R::adjust(40) == 42 && R::base() == 400 {
        0xfedcba9876543210u64
    } else { 0 }
}
