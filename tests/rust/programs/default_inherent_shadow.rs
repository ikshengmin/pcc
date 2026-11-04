trait Read {
    fn value(&self) -> u64;
    fn answer(&self) -> u64 { self.value() + 2 }
}
struct R { value: u64 }
impl Read for R { fn value(&self) -> u64 { self.value } }
impl R { fn value(&self) -> u64 { 400 } }
pub fn answer() -> u64 {
    let value = R { value: 40 };
    if value.answer() == 42 && value.value() == 400 {
        0xfedcba9876543210u64
    } else { 0 }
}
