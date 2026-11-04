fn forever() -> u64 { loop {} }
fn nested_forever() -> u64 { loop { loop { break; } } }
fn choose(first: bool) -> u64 {
    if first { 42 } else { loop {} }
}
fn early_return() -> u64 {
    loop { return 42; }
}
pub fn answer() -> u64 {
    if choose(true) == 42 && early_return() == 42 {
        0xfedcba9876543210u64
    } else { 0 }
}
