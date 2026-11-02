trait Read { fn read(self) -> u64; }
trait Child: Read {}
struct R { x: u64 }
impl Read for R { fn read(self) -> u64 { self.x } }
impl Child for R {}
impl R { fn read(self) -> u64 { 900 } }
struct Holder<T> { value: T }
impl<T> Holder<T> {
    fn read<Read, U>(&self, value: U, marker: Read) -> u64 where U: Child { value.read() }
    fn forward<V: Child>(&self, value: V) -> u64 { self.read(value, true) }
    fn identity<R>(self, marker: R) -> Self { self }
}
pub fn answer() -> u64 {
    let holder = Holder::<R> { value: R { x: 42 } };
    let result = holder.forward(R { x: 42 });
    let same = holder.identity::<bool>(true);
    if result == 42 && same.value.x == 42 && (R { x: 42 }).read() == 900 {
        0xfedcba9876543210
    } else { 0 }
}
