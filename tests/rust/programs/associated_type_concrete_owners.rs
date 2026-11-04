trait Read { type Output; fn read(self) -> Self::Output; }
struct Cell<T> { value: T }
impl Read for Cell<u64> {
    type Output = u64;
    fn read(self) -> Self::Output { self.value }
}
impl Read for Cell<i64> {
    type Output = i64;
    fn read(self) -> Self::Output { self.value }
}
pub fn answer() -> u64 {
    let a = Cell::<u64> { value: 30 }.read();
    let b = Cell::<i64> { value: 12 }.read();
    if a + (b as u64) == 42 { 0xfedcba9876543210u64 } else { 0 }
}
