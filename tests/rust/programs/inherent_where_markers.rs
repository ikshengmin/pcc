struct Holder<T> { value: T }
impl<T> Holder<T> {
    fn twice<U: ?Sized>(&self, value: U) -> (U, U) where U: Copy { (value, value) }
    fn sized<U: Sized>(&self, value: &U) -> u64 where U: ?Sized { 42 }
    fn forward<V>(&self, value: V) -> (V, V) where V: Copy, V: Sized { self.twice(value) }
}
pub fn answer() -> u64 {
    let holder = Holder::<bool> { value: true };
    let pair = holder.forward(21u64);
    if pair.0 + pair.1 == 42 && holder.sized(&pair.0) == 42 && holder.value {
        0xfedcba9876543210
    } else { 0 }
}
