trait Duplicator {
    fn first<U: ?Sized>(u: U) -> (U, U) where U: Sized, U: Copy,;
    fn second<U: Copy>(u: U) -> (U, U) where U: ?Sized, U: Sized;
    fn identity<U>(u: U) -> U where U: Copy;
}
struct Host { x: u64 }
impl Duplicator for Host {
    fn first<V: Copy>(v: V) -> (V, V) where V: ?Sized { (v, v) }
    fn second<V: ?Sized>(v: V) -> (V, V) where V: Copy { (v, v) }
    fn identity<V>(v: V) -> V { v }
}
fn forward<H: Duplicator, U>(u: U) -> (U, U) where U: Copy { H::first(u) }
pub fn answer() -> u64 {
    let a = Host::first(42u64);
    let b = Host::second(true);
    let c = forward::<Host, u32>(7u32);
    let d = <Host as Duplicator>::identity(9u64);
    if a.0 == 42 && a.1 == 42 && b.0 && b.1 && c.0 == 7 && c.1 == 7 && d == 9 {
        0xfedcba9876543210
    } else { 0 }
}
