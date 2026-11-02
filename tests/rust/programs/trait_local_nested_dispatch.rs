trait Ops { fn read<U: Child>(&self, value: &U) -> u64; }
trait Read { fn read(&self) -> u64; }
trait Child: Read {}
struct Value { x: u64 }
impl Read for Value { fn read(&self) -> u64 { self.x } }
impl Child for Value {}
impl Value { fn read(&self) -> u64 { 901 } }
struct Host { x: u64 }
impl Ops for Host {
    fn read<V: Read>(&self, value: &V) -> u64 { value.read() }
}
impl Host { fn read<V>(&self, value: &V) -> u64 { 900 } }
fn invoke<H: Ops, V: Child>(host: &H, value: &V) -> u64 { host.read(value) }
fn qualified<H: Ops, V: Child>(host: &H, value: &V) -> u64 { <H as Ops>::read(host, value) }
pub fn answer() -> u64 {
    let host = Host { x: 7 };
    let value = Value { x: 42 };
    let mut trace = 0u64;
    let result = invoke({ trace = trace * 10 + 1; &host }, { trace = trace * 10 + 2; &value });
    if result == 42 && trace == 12 && qualified(&host, &value) == 42
        && <Host as Ops>::read::<Value>(&host, &value) == 42
        && host.read(&value) == 900 && value.read() == 901 { 0xfedcba9876543210 } else { 0 }
}
