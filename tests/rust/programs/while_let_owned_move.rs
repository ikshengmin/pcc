struct R { value: u64 }
enum Item { Some(R), None }
fn consume(value: R) -> u64 { value.value }
pub fn answer() -> u64 {
    let mut item = Item::Some(R { value: 14 });
    let mut sum = 0u64;
    while let Item::Some(value) = item {
        sum += consume(value);
        if sum == 42 { break; }
        item = Item::Some(R { value: 14 });
    }
    if sum == 42 { 0xfedcba9876543210u64 } else { 0 }
}
