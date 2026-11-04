trait Read {
    type Item: ?Sized;
    fn read(self, item: &Self::Item) -> u64;
}
struct R { value: u64 }
impl Read for R {
    type Item = u64;
    fn read(self, item: &Self::Item) -> u64 { self.value + *item }
}
pub fn answer() -> u64 {
    let value = 22u64;
    let result = R { value: 20 }.read(&value);
    if result == 42 { 0xfedcba9876543210u64 } else { 0 }
}
