struct Counter { calls: u64 }
trait Apply {
    fn shared<U: Copy>(&self, value: U) -> U;
    fn mutable<U: Copy>(&mut self, value: U) -> U;
}
impl Apply for Counter {
    fn shared<V>(&self, value: V) -> V { value }
    fn mutable<V>(&mut self, value: V) -> V { self.calls += 1; value }
}
pub fn answer() -> u64 {
    let mut c = Counter { calls: 0 };
    let mut trace = 0u64;
    let a = ({ trace = trace * 10 + 1; &c }).shared({ trace = trace * 10 + 2; 20u64 });
    let b = ({ trace = trace * 10 + 3; &mut c }).mutable({ trace = trace * 10 + 4; 22u64 });
    let shared = &c;
    let yes = shared.shared(true);
    let mutable = &mut c;
    let d = mutable.mutable::<u64>(42);
    if a + b == 42 && yes && d == 42 && c.calls == 2 && trace == 1234 {
        0xfedcba9876543210
    } else { 0 }
}
