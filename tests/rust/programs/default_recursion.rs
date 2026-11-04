trait Count {
    fn count(&self, value: u64) -> u64 {
        if value == 0 { 40 } else { self.count(value - 1) + 1 }
    }
}
struct R { value: u64 }
impl Count for R {}
impl R { fn count(&self, value: u64) -> u64 { 100 } }
pub fn answer() -> u64 {
    let value = R { value: 0 };
    if <R as Count>::count(&value, 2) == 42 && value.count(2) == 100 {
        0xfedcba9876543210u64
    } else { 0 }
}
