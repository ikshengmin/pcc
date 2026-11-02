struct Factory { calls: u64 }
trait Build {
    fn pair<U: Copy>(&self, value: U) -> (U, U);
    fn forward<U: Copy>(&mut self, value: U) -> (U, U);
    fn empty<U: Copy>(&self) -> [U; 0];
    fn inspect<U: ?Sized>(&self, value: &U) -> u64;
    fn relaxed<U: ?Sized>(&self, value: &U) -> u64;
    fn restored<U: ?Sized + Copy>(&self, value: U) -> U;
}
impl Build for Factory {
    fn pair<V: Copy>(&self, value: V) -> (V, V) { (value, value) }
    fn forward<V: Copy>(&mut self, value: V) -> (V, V) {
        self.calls += 1;
        self.pair(value)
    }
    fn empty<V: Copy>(&self) -> [V; 0] { [] }
    fn inspect<V: ?Sized>(&self, value: &V) -> u64 { 42 }
    fn relaxed<V: ?Sized>(&self, value: &V) -> u64 { self.inspect(value) }
    fn restored<V: Copy>(&self, value: V) -> V { value }
}
pub fn answer() -> u64 {
    let mut f = Factory { calls: 0 };
    let values = f.forward(21u64);
    let mut late = f.empty(); late = [0u32; 0];
    let result: [bool; 0] = f.empty();
    let observed = 42u64;
    if values.0 + values.1 == f.relaxed(&observed) && f.restored(42u64) == 42
        && f.calls == 1 && late.len() + result.len() == 0usize {
        0xfedcba9876543210
    } else { 0 }
}
