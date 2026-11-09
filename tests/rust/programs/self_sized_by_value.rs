trait Count {
    fn step(&self) -> u64;
    fn total(self) -> u64 where Self: Sized { self.step() + 40 }
}
struct R { v: u64 }
impl Count for R {
    fn step(&self) -> u64 { self.v }
}
pub fn answer() -> u64 {
    let r = R { v: 2 };
    if r.total() == 42 { 0xfedcba9876543210u64 } else { 0 }
}
