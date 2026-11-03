// Value-only Rust source: no reference/borrow-checking claim is implied.
pub extern "C" fn python_mix32(a: i32, b: i32, c: i32, d: i32,
                             e: i32, f: i32, g: i32, h: i32) -> i32 {
    a - b - c - d - e - f - g - h
}

pub extern "C" fn python_mix64(a: i64, b: i64, c: i64, d: i64,
                             e: i64, f: i64, g: i64, h: i64) -> i64 {
    a + b + 2*c + 3*d + 4*e + 5*f + 6*g + 7*h
}
