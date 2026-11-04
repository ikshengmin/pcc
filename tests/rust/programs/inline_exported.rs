#[inline(always)]
#[no_mangle]
pub extern "C" fn inline_exported_value() -> u64 { 42 }
pub fn answer() -> u64 {
    if inline_exported_value() == 42 { 0xfedcba9876543210u64 } else { 0 }
}
