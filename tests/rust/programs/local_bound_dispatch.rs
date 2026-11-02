trait Read { fn read(&self) -> u64; }
struct R { x: u64 }
struct S { x: u64 }
impl Read for R { fn read(&self) -> u64 { self.x } }
impl Read for S { fn read(&self) -> u64 { self.x } }
impl R { fn read(&self) -> u64 { 900 } }
fn bound<T: Read>(value: &T) -> u64 { value.read() }
fn qualified<T: Read>(value: &T) -> u64 { <T as Read>::read(value) }
fn associated<T: Read>(value: &T) -> u64 { T::read(value) }
pub fn answer() -> u64 {
    let r = R { x: 42 };
    let s = S { x: 43 };
    if bound(&r) == 42 && bound(&s) == 43 && qualified(&r) == 42
        && associated(&r) == 42 && r.read() == 900 { 0xfedcba9876543210 } else { 0 }
}
