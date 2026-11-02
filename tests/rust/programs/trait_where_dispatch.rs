trait Ops { fn select<U>(self, value: U) -> u64 where U: Child; }
trait Read { fn read(self) -> u64; }
trait Child: Read {}
struct Value { x: u64 }
struct Host { x: u64 }
impl Read for Value { fn read(self) -> u64 { self.x } }
impl Child for Value {}
impl Value { fn read(self) -> u64 { 901 } }
impl Ops for Host {
    fn select<V>(self, value: V) -> u64 where V: Read { value.read() }
}
impl Host { fn select<V>(self, value: V) -> u64 { 900 } }
fn forward<H, U>(host: H, value: U) -> u64 where H: Ops, U: Child {
    host.select(value)
}
pub fn answer() -> u64 {
    let first = forward(Host { x: 0 }, Value { x: 42 });
    let second = <Host as Ops>::select(Host { x: 0 }, Value { x: 43 });
    let direct = Host { x: 0 }.select(Value { x: 1 });
    if first == 42 && second == 43 && direct == 900 { 0xfedcba9876543210 } else { 0 }
}
