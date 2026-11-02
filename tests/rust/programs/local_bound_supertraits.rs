trait Read { fn value(&self) -> u64; }
trait Left: Read { fn left(&self) -> u64; }
trait Right: Read { fn right(&self) -> u64; }
trait Child: Left + Right { fn extra(&self) -> u64; }
struct R { x: u64 }
impl Read for R { fn value(&self) -> u64 { self.x } }
impl Left for R { fn left(&self) -> u64 { 1 } }
impl Right for R { fn right(&self) -> u64 { 1 } }
impl Child for R { fn extra(&self) -> u64 { 2 } }
impl R { fn value(&self) -> u64 { 900 } }
fn base<T: Read>(value: &T) -> u64 { value.value() }
fn combined<T: Child>(value: &T) -> u64 { value.value() + value.left() + value.right() }
fn forwarded<U: Child>(value: &U) -> u64 { base(value) + value.extra() }
pub fn answer() -> u64 {
    let r = R { x: 40 };
    if combined(&r) == 42 && forwarded(&r) == 42 && r.value() == 900 {
        0xfedcba9876543210
    } else { 0 }
}
