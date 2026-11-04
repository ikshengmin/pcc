#[inline(always)]
fn add(value: &mut u64, amount: u64) { *value += amount; }
#[inline(never)]
fn read(value: &u64) -> u64 { *value }
pub fn answer() -> u64 {
    let mut value = 40u64;
    add(&mut value, 2);
    if read(&value) == 42 { 0xfedcba9876543210u64 } else { 0 }
}
