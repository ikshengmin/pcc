trait Accumulate {
    fn put(&mut self, value: u64);
    fn accumulate(&mut self) { self.put(40); self.put(2); }
}
struct R { value: u64 }
impl Accumulate for R { fn put(&mut self, value: u64) { self.value += value; } }
pub fn answer() -> u64 {
    let mut value = R { value: 0 };
    value.accumulate();
    if value.value == 42 { 0xfedcba9876543210u64 } else { 0 }
}
