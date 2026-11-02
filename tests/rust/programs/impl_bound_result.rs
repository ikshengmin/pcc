trait Read { fn read(&self) -> u64; }
trait Make { fn make() -> Self; }
struct R { x: u64 }
struct S { x: u64 }
impl Read for R { fn read(&self) -> u64 { self.x } }
impl Make for R { fn make() -> Self { R { x: 42 } } }
impl Make for S { fn make() -> Self { S { x: 43 } } }
struct Factory<T> { value: T }
impl<T: Read> Factory<T> {
    fn make<U: Make>(&self) -> U { self.value.read(); U::make() }
    fn forward<Read, U: Make>(&self, marker: Read) -> U { self.value.read(); self.make() }
    fn empty<U: Make>(&self) -> [U; 0] { [] }
}
pub fn answer() -> u64 {
    let factory = Factory::<R> { value: R { x: 7 } };
    let first: R = factory.make();
    let second: S = factory.forward(true);
    let explicit = factory.make::<R>();
    let mut late = factory.empty(); late = [R { x: 0 }; 0];
    if first.x == 42 && second.x == 43 && explicit.x == 42 && factory.value.x == 7
        && late.len() == 0usize { 0xfedcba9876543210 } else { 0 }
}
