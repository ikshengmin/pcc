struct R { value: u64 }
fn identity<T>(value: T) -> T {
    let mut flag = true;
    while let true = flag { flag = false; }
    value
}
fn tuple() -> (u64,) { while let false = true {} (42,) }
fn array() -> [u64; 1] { while let false = true {} [42] }
pub fn answer() -> u64 {
    let value = identity(R { value: 42 });
    if value.value == 42 && tuple().0 == 42 && array()[0] == 42 {
        0xfedcba9876543210u64
    } else { 0 }
}
