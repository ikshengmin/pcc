trait It {
    fn get(&self) -> u64;
    fn twice(&self) -> u64 where Self: core::marker::Sized { self.get() * 2 }
    fn plus_one(&self) -> u64 where Self: Sized { self.twice() + 1 }
}
struct R { v: u64 }
impl It for R {
    fn get(&self) -> u64 { self.v }
}
fn use_it<T: It>(t: &T) -> u64 { t.plus_one() }
pub fn answer() -> u64 {
    let r = R { v: 20 };
    if use_it(&r) + 1 == 42 { 0xfedcba9876543210u64 } else { 0 }
}
