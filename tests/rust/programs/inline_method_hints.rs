struct R { value: u64 }
trait Read {
    #[inline(always)]
    fn read(self) -> u64;
}
impl Read for R {
    #[inline(never)]
    fn read(self) -> u64 { self.value }
}
impl R {
    #[inline]
    fn create(value: u64) -> Self { Self { value: value } }
    #[inline(always)]
    fn identity<T>(self, value: T) -> T { value }
}
pub fn answer() -> u64 {
    let left = R::create(40);
    let first = left.read();
    let right = R::create(0);
    let last = right.identity(2u64);
    if first + last == 42 { 0xfedcba9876543210u64 } else { 0 }
}
