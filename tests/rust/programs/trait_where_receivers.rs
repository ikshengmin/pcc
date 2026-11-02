trait Apply {
    fn apply<U>(&mut self, value: &mut U, delta: u64) -> u64 where U: Step;
    fn inspect<U>(&self, value: &U) -> u64 where U: Read;
}
trait Step { fn step(&mut self, delta: u64) -> u64; }
trait Read { fn read(&self) -> u64; }
struct Value { x: u64 }
struct Host { calls: u64 }
impl Step for Value { fn step(&mut self, delta: u64) -> u64 { self.x += delta; self.x } }
impl Read for Value { fn read(&self) -> u64 { self.x } }
impl Apply for Host {
    fn apply<V>(&mut self, value: &mut V, delta: u64) -> u64 where V: Step {
        self.calls += 1; value.step(delta)
    }
    fn inspect<V: Read>(&self, value: &V) -> u64 { value.read() }
}
impl Host { fn apply<V>(&mut self, value: &mut V, delta: u64) -> u64 { 900 } }
fn forward<H, V>(host: &mut H, value: &mut V, delta: u64) -> u64 where H: Apply, V: Step {
    host.apply(value, delta)
}
fn observe<H: Apply, V: Read>(host: &H, value: &V) -> u64 { host.inspect(value) }
pub fn answer() -> u64 {
    let mut host = Host { calls: 0 };
    let mut value = Value { x: 39 };
    let mut trace = 0u64;
    let first = forward({ trace = trace * 10 + 1; &mut host },
        { trace = trace * 10 + 2; &mut value }, { trace = trace * 10 + 3; 2u64 });
    let alias = &mut host;
    let second = forward(alias, &mut value, 1);
    let read = observe({ trace = trace * 10 + 4; &host }, { trace = trace * 10 + 5; &value });
    if first == 41 && second == 42 && read == 42 && trace == 12345 && host.calls == 2
        && value.x == 42 && host.apply(&mut value, 1) == 900 { 0xfedcba9876543210 } else { 0 }
}
