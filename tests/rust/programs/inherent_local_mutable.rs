trait Step { fn step(&mut self, delta: u64) -> u64; }
struct R { x: u64 }
impl Step for R { fn step(&mut self, delta: u64) -> u64 { self.x += delta; self.x } }
impl R { fn step(&mut self, delta: u64) -> u64 { 900 } }
struct Counter { calls: u64 }
impl Counter {
    fn apply<U: Step>(&mut self, value: &mut U, delta: u64) -> u64 {
        self.calls += 1;
        value.step(delta)
    }
    fn forward<V>(&mut self, value: &mut V) -> u64 where V: Step { self.apply(value, 1) }
}
pub fn answer() -> u64 {
    let mut counter = Counter { calls: 0 };
    let mut value = R { x: 39 };
    let mut trace = 0u64;
    let first = ({ trace = trace * 10 + 1; &mut counter }).apply(
        { trace = trace * 10 + 2; &mut value }, { trace = trace * 10 + 3; 2u64 });
    let second = counter.forward(&mut value);
    if first == 41 && second == 42 && counter.calls == 2 && trace == 123
        && value.x == 42 && value.step(1) == 900 { 0xfedcba9876543210 } else { 0 }
}
