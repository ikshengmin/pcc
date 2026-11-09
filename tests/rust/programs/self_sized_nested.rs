struct Wrap<I> { it: I }
trait It {
    fn get(&self) -> u64;
    fn wrap(self) -> Wrap<Self> where Self: Sized { Wrap { it: self } }
}
struct R { v: u64 }
impl It for R {
    fn get(&self) -> u64 { self.v }
}
pub fn answer() -> u64 {
    let w = R { v: 42 }.wrap();
    if w.it.get() == 42 { 0xfedcba9876543210u64 } else { 0 }
}
