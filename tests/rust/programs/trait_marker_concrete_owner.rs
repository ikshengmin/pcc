#![no_std]
struct Box<T> { value: T }
struct NonCopy { x: u64 }
trait Transform {
    fn identity<U: Sized>(self, value: U) -> U;
    fn duplicate<U: core::marker::Copy>(value: U) -> (U, U);
    fn unused<U: ?Sized>();
}
impl Transform for Box<u64> {
    fn identity<V>(self, value: V) -> V { value }
    fn duplicate<V: Copy>(value: V) -> (V, V) { (value, value) }
    fn unused<V: ?Sized>() {}
}
impl Transform for Box<bool> {
    fn identity<V: Sized>(self, value: V) -> V { value }
    fn duplicate<V: core::marker::Copy>(value: V) -> (V, V) { (value, value) }
    fn unused<V: ?Sized>() {}
}
pub fn answer() -> u64 {
    let r = Box::<u64> { value: 1 }.identity(NonCopy { x: 42 });
    let pair = <Box<u64> as Transform>::duplicate(21u64);
    let yes = Box::<bool> { value: true }.identity(true);
    <Box<u64> as Transform>::unused::<bool>();
    <Box<bool> as Transform>::unused::<u64>();
    if r.x == pair.0 + pair.1 && yes { 0xfedcba9876543210 } else { 0 }
}
