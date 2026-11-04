trait Read { type Output; fn read(self) -> Self::Output; }
struct Wide { value: u64 }
struct Signed { value: i64 }
impl Read for Wide {
    type Output = u64;
    fn read(self) -> Self::Output { self.value }
}
impl Read for Signed {
    type Output = i64;
    fn read(self) -> i64 { self.value }
}
pub fn answer() -> u64 {
    let a = Wide { value: 50 }.read();
    let b = <Signed as Read>::read(Signed { value: -8 });
    if (a as i64) + b == 42 { 0xfedcba9876543210u64 } else { 0 }
}
