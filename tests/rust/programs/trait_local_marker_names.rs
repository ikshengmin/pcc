trait Copy {}
trait Sized {}
trait Ops {
    fn identity<U: Copy + Sized>(self, value: U) -> U;
    fn twice<U: core::marker::Copy>(self, value: U) -> (U, U);
}
struct Value { x: u64 }
struct Host { x: u64 }
impl Copy for Value {}
impl Sized for Value {}
impl Ops for Host {
    fn identity<V>(self, value: V) -> V { value }
    fn twice<V: core::marker::Copy>(self, value: V) -> (V, V) { (value, value) }
}
fn forward<H: Ops, V: Copy + Sized>(host: H, value: V) -> V { host.identity(value) }
pub fn answer() -> u64 {
    let value = forward(Host { x: 0 }, Value { x: 42 });
    let pair = Host { x: 0 }.twice(21u64);
    if value.x == pair.0 + pair.1 { 0xfedcba9876543210 } else { 0 }
}
