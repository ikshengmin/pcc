fn identity<T>(value: T) -> T {
    loop { return value; }
}
struct R { x: u64 }
pub fn answer() -> u64 {
    let value = identity(R { x: 42 });
    if value.x == 42 { 0xfedcba9876543210u64 } else { 0 }
}
