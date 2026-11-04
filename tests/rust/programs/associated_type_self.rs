trait Identity {
    type Item;
    fn identity(self, item: Self::Item) -> Self::Item;
}
struct R { value: u64 }
impl Identity for R {
    type Item = Self;
    fn identity(self, item: Self::Item) -> Self::Item { item }
}
pub fn answer() -> u64 {
    let result = R { value: 1 }.identity(R { value: 42 });
    if result.value == 42 { 0xfedcba9876543210u64 } else { 0 }
}
