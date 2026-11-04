trait Value {
    fn base(&self) -> u64;
    fn answer(&self) -> u64 { self.base() + 2 }
}
struct R { value: u64 }
struct S { value: u64 }
impl Value for R { fn base(&self) -> u64 { self.value } }
impl Value for S {
    fn base(&self) -> u64 { self.value }
    fn answer(&self) -> u64 { self.base() + 22 }
}
pub fn answer() -> u64 {
    let first = R { value: 40 };
    let second = S { value: 20 };
    if first.answer() == 42 && second.answer() == 42 {
        0xfedcba9876543210u64
    } else { 0 }
}
