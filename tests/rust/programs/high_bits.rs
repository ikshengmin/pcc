#![no_std]
fn roundtrip(value: u64) -> u64 { (value as i64) as u64 }
pub extern "C" fn answer() -> u64 {
    roundtrip(0xfedc_ba98_7654_3210u64)
}
