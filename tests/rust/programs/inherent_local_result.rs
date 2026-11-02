trait Make { fn make() -> Self; }
struct R { x: u64 }
struct S { x: u64 }
impl Make for R { fn make() -> Self { R { x: 42 } } }
impl Make for S { fn make() -> Self { S { x: 43 } } }
struct Holder<T> { value: T }
impl<T> Holder<T> {
    fn create<U>(&self) -> U where U: Make { U::make() }
    fn empty<U: Make>(&self) -> [U; 0] { [] }
}
pub fn answer() -> u64 {
    let holder = Holder::<bool> { value: true };
    let first: R = holder.create();
    let second: S = holder.create();
    let explicit = holder.create::<R>();
    let mut later = holder.empty(); later = [R { x: 0 }; 0];
    let other: [S; 0] = holder.empty();
    if first.x == 42 && second.x == 43 && explicit.x == 42 && holder.value
        && later.len() + other.len() == 0usize { 0xfedcba9876543210 } else { 0 }
}
