trait Allowed {}
trait Identity { fn identity<U: Allowed>(self, value: U) -> U; }
struct Value { x: u64 }
struct Other { x: u64 }
struct Host { x: u64 }
impl Allowed for Value {}
impl Identity for Host { fn identity<V>(self, value: V) -> V { value } }
impl Host { fn identity<V>(self, value: V) -> V { value } }
fn forward<H: Identity, V: Allowed>(host: H, value: V) -> V { host.identity(value) }
pub fn answer() -> u64 {
    let first = forward(Host { x: 0 }, Value { x: 42 });
    let second = <Host as Identity>::identity(Host { x: 0 }, Value { x: 42 });
    let direct = Host { x: 0 }.identity(Other { x: 900 });
    if first.x == 42 && second.x == 42 && direct.x == 900 { 0xfedcba9876543210 } else { 0 }
}
