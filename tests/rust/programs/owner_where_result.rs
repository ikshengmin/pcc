trait Read { fn read(&self) -> u64; }
trait Make { fn make() -> Self; }
struct R { x: u64 }
struct S { x: u64 }
impl Read for R { fn read(&self) -> u64 { self.x } }
impl Make for R { fn make() -> Self { R { x: 42 } } }
impl Make for S { fn make() -> Self { S { x: 43 } } }
struct Factory<T> { value: T }
impl<T> Factory<T> {
    fn make<U>(&self) -> U where T: Read, U: Make { self.value.read(); U::make() }
    fn empty<U>(&self) -> [U; 0] where T: Read { [] }
    fn forward<V: Make>(&self) -> V where T: Read { self.make() }
}
pub fn answer() -> u64 {
    let factory = Factory::<R> { value: R { x: 7 } };
    let first: R = factory.make();
    let second: S = factory.forward();
    let explicit = factory.make::<R>();
    let mut later = factory.empty(); later = [R { x: 0 }; 0];
    if first.x == 42 && second.x == 43 && explicit.x == 42
        && factory.value.x == 7 && later.len() == 0usize { 0xfedcba9876543210 } else { 0 }
}
