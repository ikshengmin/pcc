trait A { fn value(self) -> u64; }
trait B { fn value(self) -> u64; }
trait Read { fn read(self) -> u64; }
trait Child: Read {}
struct R { x: u64 }
impl A for R { fn value(self) -> u64 { self.x } }
impl B for R { fn value(self) -> u64 { 900 } }
impl Read for R { fn read(self) -> u64 { self.x } }
impl Child for R {}
fn left<T: A + B>(value: T) -> u64 { <T as A>::value(value) }
fn right<T: A + B>(value: T) -> u64 { <T as B>::value(value) }
fn shadow<Read, T: Child>(value: T, marker: Read) -> u64 { value.read() }
pub fn answer() -> u64 {
    if left(R { x: 42 }) == 42 && right(R { x: 42 }) == 900
        && shadow(R { x: 42 }, true) == 42 { 0xfedcba9876543210 } else { 0 }
}
