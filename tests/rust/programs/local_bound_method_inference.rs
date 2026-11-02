trait Ops {
    fn pair<U: Copy>(&self, value: U) -> (U, U);
    fn empty<U: Copy>(&self) -> [U; 0];
    fn make() -> Self;
}
struct R { x: u64 }
impl Ops for R {
    fn pair<V: Copy>(&self, value: V) -> (V, V) { (value, value) }
    fn empty<V>(&self) -> [V; 0] { [] }
    fn make() -> Self { R { x: 42 } }
}
fn use_ops<T: Ops>(value: &T) -> u64 {
    let pair = value.pair(21u64);
    let mut late = value.empty(); late = [0u32; 0];
    let result: [bool; 0] = value.empty();
    if late.len() + result.len() == 0usize { pair.0 + pair.1 } else { 0 }
}
fn construct<T: Ops>() -> T { T::make() }
pub fn answer() -> u64 {
    let r: R = construct();
    if use_ops(&r) == r.x { 0xfedcba9876543210 } else { 0 }
}
