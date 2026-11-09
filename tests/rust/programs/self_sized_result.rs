trait Make {
    fn make(v: u64) -> Self where Self: Sized;
    fn again(&self) -> Self where Self: Sized { Self::make(42) }
}
struct R { v: u64 }
impl Make for R {
    fn make(v: u64) -> R { R { v: v } }
}
pub fn answer() -> u64 {
    let r = R { v: 1 };
    let s = r.again();
    if r.v == 1 && s.v == 42 { 0xfedcba9876543210u64 } else { 0 }
}
