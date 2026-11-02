trait Read { fn read(&self) -> u64; }
trait Pick { fn pick(&self) -> u64; }
struct R { x: u64 }
struct N { x: u64 }
impl Read for R { fn read(&self) -> u64 { self.x } }
struct Holder<T> { value: T }
impl<T> Holder<T> {
    fn pick(&mut self) -> u64 where T: Read { self.value.read() }
}
impl Pick for Holder<R> { fn pick(&self) -> u64 { 900 } }
impl Pick for Holder<N> { fn pick(&self) -> u64 { 901 } }
pub fn answer() -> u64 {
    let mut valid = Holder::<R> { value: R { x: 42 } };
    let mut other = Holder::<N> { value: N { x: 9 } };
    let shared_first = valid.pick();
    let mutable_first = (&mut valid).pick();
    if shared_first == 900 && mutable_first == 42 && other.pick() == 901
        && Holder::<R>::pick(&mut valid) == 42
        && <Holder<R> as Pick>::pick(&valid) == 900 { 0xfedcba9876543210 } else { 0 }
}
