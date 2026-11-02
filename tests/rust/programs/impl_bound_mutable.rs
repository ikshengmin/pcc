trait Step { fn step(&mut self, delta: u64) -> u64; }
struct Value { x: u64 }
impl Step for Value { fn step(&mut self, delta: u64) -> u64 { self.x += delta; self.x } }
impl Value { fn step(&mut self, delta: u64) -> u64 { 900 } }
struct Counter<T> { value: T, calls: u64 }
impl<T> Counter<T> where T: Step {
    fn apply(&mut self, delta: u64) -> u64 { self.calls += 1; self.value.step(delta) }
    fn forward(&mut self, delta: u64) -> u64 { self.apply(delta) }
}
fn forward<U: Step>(counter: &mut Counter<U>) -> u64 { counter.forward(1) }
pub fn answer() -> u64 {
    let mut counter = Counter::<Value> { value: Value { x: 39 }, calls: 0 };
    let mut trace = 0u64;
    let first = ({ trace = trace * 10 + 1; &mut counter }).apply({ trace = trace * 10 + 2; 2u64 });
    let alias = &mut counter;
    let second = forward(alias);
    if first == 41 && second == 42 && trace == 12 && counter.calls == 2
        && counter.value.x == 42 && counter.value.step(1) == 900 { 0xfedcba9876543210 } else { 0 }
}
