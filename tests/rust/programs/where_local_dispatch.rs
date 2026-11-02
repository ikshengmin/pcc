trait Read { fn read(&self) -> u64; }
trait Child: Read { fn extra(&self) -> u64; }
struct R { x: u64 }
impl Read for R { fn read(&self) -> u64 { self.x } }
impl Child for R { fn extra(&self) -> u64 { 2 } }
impl R { fn read(&self) -> u64 { 900 } }
fn bound<T>(value: &T) -> u64 where T: Child, T: Read { value.read() + value.extra() }
fn forward<U: Child>(value: &U) -> u64 where U: Read { bound(value) }
fn explicit<T>(value: &T) -> u64 where T: Read { <T as Read>::read(value) }
pub fn answer() -> u64 {
    let r = R { x: 40 };
    if forward(&r) == 42 && explicit(&r) == 40 && r.read() == 900 {
        0xfedcba9876543210
    } else { 0 }
}
