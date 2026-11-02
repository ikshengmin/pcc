trait Read { fn read(&self) -> u64; }
trait Child: Read {}
struct R { x: u64 }
impl Read for R { fn read(&self) -> u64 { self.x } }
impl Child for R {}
impl R { fn read(&self) -> u64 { 900 } }
struct Holder<T> { value: T }
impl<T> Holder<T> {
    fn read(&self) -> u64 where T: Read { self.value.read() }
    fn qualified(&self) -> u64 where T: Read { <T as Read>::read(&self.value) }
    fn forward(&self) -> u64 where T: Child { self.read() }
    fn unrelated(&self) -> u64 { 7 }
}
fn forwarded<U: Child>(holder: &Holder<U>) -> u64 { holder.forward() }
pub fn answer() -> u64 {
    let holder = Holder::<R> { value: R { x: 42 } };
    let mut trace = 0u64;
    let first = ({ trace = trace + 1; &holder }).read();
    let alias = &holder;
    let unbounded = Holder::<bool> { value: true };
    if first == 42 && trace == 1 && alias.read() == 42 && holder.qualified() == 42
        && forwarded(&holder) == 42 && Holder::<R>::read(&holder) == 42
        && holder.value.read() == 900 && unbounded.unrelated() == 7 {
        0xfedcba9876543210
    } else { 0 }
}
