struct R { x: u64 }
trait First { fn tag<U: Copy>(&self, value: U) -> u64; }
trait Second { fn tag<U: Copy>(&self, value: U) -> u64; }
impl First for R {
    fn tag<V: Copy>(&self, value: V) -> u64 { let pair = (value, value); self.x }
}
impl Second for R {
    fn tag<V: Copy>(&self, value: V) -> u64 { let pair = (value, value); self.x + 1 }
}
impl R { fn tag<U: Copy>(&self, value: U) -> u64 { 900 } }
trait Earlier { fn select<U: Copy>(&self, value: U) -> u64; }
impl Earlier for R { fn select<V: Copy>(&self, value: V) -> u64 { 42 } }
impl R { fn select<U: Copy>(&mut self, value: U) -> u64 { 901 } }
pub fn answer() -> u64 {
    let mut r = R { x: 41 };
    let a = <R as First>::tag(&r, 1u64);
    let b = <R as Second>::tag::<u64>(&r, 1);
    let c = r.tag(true);
    let d = r.select(1u64);
    if a == 41 && b == 42 && c == 900 && d == 42 { 0xfedcba9876543210 } else { 0 }
}
