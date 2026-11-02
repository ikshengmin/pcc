trait Copy { fn take(self) -> u64; }
trait Sized {}
trait Read { fn read(self) -> u64; }
trait Child: Read {}
struct R { x: u64 }
impl Copy for R { fn take(self) -> u64 { self.x } }
impl Sized for R {}
impl Read for R { fn read(self) -> u64 { self.x } }
impl Child for R {}
fn consume<T>(value: T) -> u64 where T: Copy + Sized { value.take() }
fn mixed<T, U>(value: T, number: U) -> (u64, U, U) where T: Copy, U: core::marker::Copy {
    (value.take(), number, number)
}
fn shadow<Read, T>(value: T, marker: Read) -> u64 where T: Child { value.read() }
pub fn answer() -> u64 {
    let triple = mixed(R { x: 42 }, 21u64);
    if consume(R { x: 42 }) == 42 && triple.0 == triple.1 + triple.2
        && shadow(R { x: 42 }, true) == 42 { 0xfedcba9876543210 } else { 0 }
}
