struct Host { x: u64 }
trait Pairs {
    fn pair<U>(host: Host, value: U) -> (Host, U) where U: Copy;
    fn swap<A, B>(a: A, b: B) -> (B, A) where A: Copy, B: Copy;
}
impl Pairs for Host {
    fn pair<Host>(host: Self, value: Host) -> (Self, Host) where Host: Copy { (host, value) }
    fn swap<B, A>(a: B, b: A) -> (A, B) where A: Copy, B: Copy { (b, a) }
}
fn forward<H: Pairs, V: Copy>(host: Host, value: V) -> (Host, V) { H::pair(host, value) }
pub fn answer() -> u64 {
    let pair = forward::<Host, u32>(Host { x: 42 }, 7u32);
    let swapped = <Host as Pairs>::swap(41u64, true);
    if pair.0.x == 42 && pair.1 == 7 && swapped.0 && swapped.1 == 41 {
        0xfedcba9876543210
    } else { 0 }
}
