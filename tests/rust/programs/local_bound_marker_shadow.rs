trait Copy { fn take(self) -> u64; }
trait Sized {}
struct R { x: u64 }
impl Copy for R { fn take(self) -> u64 { self.x } }
impl Sized for R {}
fn consume<T: Copy + Sized>(value: T) -> u64 { value.take() }
fn mixed<T: Copy, U: core::marker::Copy>(value: T, item: U) -> (u64, U, U) {
    (value.take(), item, item)
}
pub fn answer() -> u64 {
    let one = consume(R { x: 42 });
    let pair = mixed(R { x: 42 }, 21u64);
    if one == 42 && pair.0 == pair.1 + pair.2 { 0xfedcba9876543210 } else { 0 }
}
