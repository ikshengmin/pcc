pub extern "C" fn mix32(a: u32, b: u32) -> u32 {
    a ^ b
}

pub extern "C" fn mix64(a: u64, b: u64, c: u64, d: u64,
                      e: u64, f: u64, g: u64, h: u64) -> u64 {
    a + b + c + d + e + f + g + h
}
