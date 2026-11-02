struct Factory<T> { item: T, calls: u64 }
impl<T> Factory<T> {
    fn pair<U: Copy>(&self, value: U) -> (U, U) { (value, value) }
    fn forward<V: Copy>(&mut self, value: V) -> (V, V) {
        self.calls += 1;
        self.pair(value)
    }
    fn empty<U: Copy>(&self) -> [U; 0] { [] }
    fn require_sized<U: Sized>(&self) {}
    fn inspect<U: ?Sized>(&self, value: &U) -> u64 { 42 }
    fn relaxed<V: ?Sized>(&self, value: &V) -> u64 { self.inspect(value) }
    fn restored<V: ?Sized + Copy>(&self, value: V) -> V { self.require_sized::<V>(); value }
}
pub fn answer() -> u64 {
    let mut f = Factory::<bool> { item: true, calls: 0 };
    let values = f.forward(21u64);
    let mut late = f.empty(); late = [0u32; 0];
    let result: [bool; 0] = f.empty();
    let observed = 42u64;
    if values.0 + values.1 == f.relaxed(&observed) && f.restored(42u64) == 42
        && f.calls == 1 && f.item && late.len() + result.len() == 0usize {
        0xfedcba9876543210
    } else { 0 }
}
