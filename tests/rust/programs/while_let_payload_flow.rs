enum Next { Value(u64), Done }
fn next(cursor: &mut u64) -> Next {
    *cursor += 1;
    if *cursor < 4 { Next::Value(*cursor * 7) } else { Next::Done }
}
pub fn answer() -> u64 {
    let mut cursor = 0u64;
    let mut sum = 0u64;
    let mut after_continue = 0u64;
    while let Next::Value(mut value) = next(&mut cursor) {
        let original = value;
        value += 0;
        sum += value;
        if original == 14 { continue; }
        after_continue += 1;
    }
    if sum == 42 && cursor == 4 && after_continue == 2 {
        0xfedcba9876543210u64
    } else { 0 }
}
