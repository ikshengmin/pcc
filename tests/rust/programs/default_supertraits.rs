trait Base { fn base(&self) -> u64; }
trait Left: Base { fn middle(&self) -> u64 { self.base() + 1 } }
trait Right: Base {}
trait Top: Left + Right { fn answer(&self) -> u64 { self.middle() + 1 } }
struct R { value: u64 }
impl Base for R { fn base(&self) -> u64 { self.value } }
impl Left for R {}
impl Right for R {}
impl Top for R {}
impl R { fn middle(&self) -> u64 { 400 } }
pub fn answer() -> u64 {
    let value = R { value: 40 };
    if value.answer() == 42 && value.middle() == 400 {
        0xfedcba9876543210u64
    } else { 0 }
}
