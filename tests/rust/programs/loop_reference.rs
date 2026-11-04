fn bump(value: &mut u64) {
    loop {
        *value += 1;
        if *value == 42 { break; }
    }
}
pub fn answer() -> u64 {
    let mut value = 40u64;
    bump(&mut value);
    if value == 42 { 0xfedcba9876543210u64 } else { 0 }
}
