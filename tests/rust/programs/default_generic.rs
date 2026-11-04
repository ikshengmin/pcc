trait Helpers {
    fn supplied<U: Copy>(&self, value: U) -> U;
    fn identity<U>(&self, value: U) -> U { value }
    fn pair<U: Copy>(&self, value: U) -> (U, U) { (value, value) }
    fn compose<V: Copy>(&self, value: V) -> V {
        let pair = self.pair(value);
        self.supplied(pair.0)
    }
}
struct R { value: u64 }
impl Helpers for R { fn supplied<U>(&self, value: U) -> U { value } }
pub fn answer() -> u64 {
    let helper = R { value: 0 };
    let first = helper.identity(R { value: 40 });
    let second = helper.compose(2u64);
    let truth = helper.pair(true);
    if first.value + second == 42 && truth.0 && truth.1 && helper.compose(true) {
        0xfedcba9876543210u64
    } else { 0 }
}
