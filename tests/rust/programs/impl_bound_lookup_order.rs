trait Pick { fn pick(&self) -> u64; }
struct N { x: u64 }
struct Holder<T> { value: T }
impl<T: Copy> Holder<T> { fn pick(&mut self) -> u64 { 42 } }
impl Pick for Holder<u64> { fn pick(&self) -> u64 { 900 } }
impl Pick for Holder<N> { fn pick(&self) -> u64 { 901 } }
pub fn answer() -> u64 {
    let mut eligible = Holder::<u64> { value: 7 };
    let mut ineligible = Holder::<N> { value: N { x: 8 } };
    let shared_first = eligible.pick();
    let mutable_first = (&mut eligible).pick();
    let fallback = (&mut ineligible).pick();
    if shared_first == 900 && mutable_first == 42 && fallback == 901
        && Holder::<u64>::pick(&mut eligible) == 42 && ineligible.value.x == 8 {
        0xfedcba9876543210
    } else { 0 }
}
