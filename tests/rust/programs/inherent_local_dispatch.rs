trait Read { fn read(&self) -> u64; }
struct R { x: u64 }
impl Read for R { fn read(&self) -> u64 { self.x } }
impl R { fn read(&self) -> u64 { 900 } }
struct Holder<T> { value: T }
impl<T> Holder<T> {
    fn read<U: Read>(&self, value: &U) -> u64 { value.read() }
    fn qualified<U>(&self, value: &U) -> u64 where U: Read { <U as Read>::read(value) }
    fn associated<U>(&self, value: &U) -> u64 where U: Read { U::read(value) }
    fn forward<V: Read>(&self, value: &V) -> u64 { self.read(value) }
}
pub fn answer() -> u64 {
    let holder = Holder::<u64> { value: 7 };
    let value = R { x: 42 };
    let mut trace = 0u64;
    let first = ({ trace = trace * 10 + 1; &holder }).read({ trace = trace * 10 + 2; &value });
    if first == 42 && trace == 12 && holder.read::<R>(&value) == 42 && holder.qualified(&value) == 42
        && holder.associated(&value) == 42 && holder.forward(&value) == 42
        && Holder::<u64>::read(&holder, &value) == 42
        && value.read() == 900 && holder.value == 7 { 0xfedcba9876543210 } else { 0 }
}
