trait Step { fn step(&mut self, delta: u64) -> u64; }
struct R { x: u64 }
impl Step for R { fn step(&mut self, delta: u64) -> u64 { self.x += delta; self.x } }
impl R { fn step(&mut self, delta: u64) -> u64 { 900 } }
struct Counter<T> { value: T, calls: u64 }
impl<T> Counter<T> {
    fn apply(&mut self, delta: u64) -> u64 where T: Step {
        self.calls += 1;
        self.value.step(delta)
    }
    fn forward(&mut self, delta: u64) -> u64 where T: Step { self.apply(delta) }
}
fn forwarded<U: Step>(counter: &mut Counter<U>) -> u64 { counter.forward(1) }
pub fn answer() -> u64 {
    let mut counter = Counter::<R> { value: R { x: 39 }, calls: 0 };
    let mut trace = 0u64;
    let first = ({ trace = trace * 10 + 1; &mut counter }).apply({ trace = trace * 10 + 2; 2u64 });
    let alias = &mut counter;
    let second = forwarded(alias);
    if first == 41 && second == 42 && trace == 12 && counter.calls == 2
        && counter.value.x == 42 && counter.value.step(1) == 900 {
        0xfedcba9876543210
    } else { 0 }
}
