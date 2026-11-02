trait Build {
    fn make<U: Make>(&self) -> U;
    fn create<U: Make>() -> U;
    fn empty<U: Make>(&self) -> [U; 0];
}
trait Make { fn make() -> Self; }
struct R { x: u64 }
struct S { x: u64 }
struct Factory { x: u64 }
impl Make for R { fn make() -> Self { R { x: 42 } } }
impl Make for S { fn make() -> Self { S { x: 43 } } }
impl Build for Factory {
    fn make<V: Make>(&self) -> V { V::make() }
    fn create<V: Make>() -> V { V::make() }
    fn empty<V>(&self) -> [V; 0] { [] }
}
fn forward<H: Build, U: Make>(host: &H) -> U { host.make() }
pub fn answer() -> u64 {
    let factory = Factory { x: 7 };
    let first: R = factory.make();
    let second: S = forward(&factory);
    let third: R = Factory::create();
    let fourth: S = <Factory as Build>::create();
    let mut later = factory.empty(); later = [R { x: 0 }; 0];
    if first.x == 42 && second.x == 43 && third.x == 42 && fourth.x == 43
        && later.len() == 0usize && factory.x == 7 { 0xfedcba9876543210 } else { 0 }
}
