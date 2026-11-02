trait Apply { fn apply<U: Step>(&mut self, value: &mut U, delta: u64) -> u64; }
trait Step { fn step(&mut self, delta: u64) -> u64; }
struct Value { x: u64 }
struct Host { calls: u64 }
impl Step for Value { fn step(&mut self, delta: u64) -> u64 { self.x += delta; self.x } }
impl Apply for Host {
    fn apply<V: Step>(&mut self, value: &mut V, delta: u64) -> u64 {
        self.calls += 1;
        value.step(delta)
    }
}
impl Host { fn apply<V>(&mut self, value: &mut V, delta: u64) -> u64 { 900 } }
fn forward<H: Apply, V: Step>(host: &mut H, value: &mut V, delta: u64) -> u64 {
    host.apply(value, delta)
}
pub fn answer() -> u64 {
    let mut host = Host { calls: 0 };
    let mut value = Value { x: 39 };
    let mut trace = 0u64;
    let first = forward({ trace = trace * 10 + 1; &mut host },
        { trace = trace * 10 + 2; &mut value }, { trace = trace * 10 + 3; 2u64 });
    let alias = &mut host;
    let second = forward(alias, &mut value, 1);
    if first == 41 && second == 42 && trace == 123 && host.calls == 2
        && value.x == 42 && host.apply(&mut value, 1) == 900 { 0xfedcba9876543210 } else { 0 }
}
