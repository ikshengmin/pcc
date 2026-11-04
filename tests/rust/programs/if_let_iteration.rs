enum Item { Value(u64), Stop }
pub fn answer() -> u64 {
    let mut sum = 0u64;
    for item in [Item::Value(40), Item::Value(2), Item::Stop, Item::Value(100)] {
        if let Item::Stop = item { break; }
        else if let Item::Value(value) = item {
            sum += value;
            continue;
        }
    }
    if sum == 42 { 0xfedcba9876543210u64 } else { 0 }
}
