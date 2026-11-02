struct R { x: u64 }
struct NonCopy { x: u64 }
trait Identity {
    fn id<U: Copy>(self, value: U) -> U;
    fn empty<U: Copy>() -> [U; 0];
}
impl Identity for R {
    fn id<V>(self, value: V) -> V { value }
    fn empty<V>() -> [V; 0] { [] }
}
impl R { fn id<U>(self, value: U) -> U { value } }
pub fn answer() -> u64 {
    let direct = R { x: 0 }.id(NonCopy { x: 42 });
    let first = <R as Identity>::id(R { x: 1 }, 21u64);
    let second = <R as Identity>::id::<u64>(R { x: 2 }, 21);
    let mut late = R::empty(); late = [0u32; 0];
    let other: [bool; 0] = <R as Identity>::empty();
    if direct.x == first + second && late.len() + other.len() == 0usize {
        0xfedcba9876543210
    } else { 0 }
}
