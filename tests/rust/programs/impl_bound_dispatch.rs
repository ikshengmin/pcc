trait Read { fn read(&self) -> u64; }
trait Fallback { fn read(&self) -> u64; }
struct R { x: u64 }
struct N { x: u64 }
impl Read for R { fn read(&self) -> u64 { self.x } }
impl R { fn read(&self) -> u64 { 901 } }
struct Holder<T> { value: T }
impl<T: Read> Holder<T> {
    fn read(&self) -> u64 { self.value.read() }
    fn qualified(&self) -> u64 { <T as Read>::read(&self.value) }
}
impl Fallback for Holder<R> { fn read(&self) -> u64 { 902 } }
impl Fallback for Holder<N> { fn read(&self) -> u64 { 900 } }
fn forward<U: Read>(holder: &Holder<U>) -> u64 { holder.read() }
pub fn answer() -> u64 {
    let yes = Holder::<R> { value: R { x: 42 } };
    let no = Holder::<N> { value: N { x: 7 } };
    if yes.read() == 42 && yes.qualified() == 42 && forward(&yes) == 42
        && no.read() == 900 && Holder::<N>::read(&no) == 900
        && <Holder<R> as Fallback>::read(&yes) == 902 && yes.value.read() == 901 {
        0xfedcba9876543210
    } else { 0 }
}
