#![no_std]
struct R { value: u64 }
impl R {
    fn identity<U: Sized>(self, value: U) -> U { value }
    fn empty<U: ?Sized>() {}
    fn duplicate<U: Copy>(value: U) -> (U, U) { (value, value) }
}
pub fn answer() -> u64 {
    R::empty::<u64>();
    let r = R { value: 42 }.identity(R { value: 42 });
    let values = R::duplicate(21u64);
    if r.value == values.0 + values.1 { 0xfedcba9876543210 } else { 0 }
}
