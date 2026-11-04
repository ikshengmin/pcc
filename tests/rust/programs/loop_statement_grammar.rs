fn unit() {}
fn tuple() -> (u64,) { loop { break; } (42,) }
fn array() -> [u64; 1] { loop { break; } [42] }
pub fn answer() -> u64 {
    let done: () = (loop { break; });
    let mut value = 0u64;
    match false {
        true => loop { break; }
        false => { value = 42; }
    }
    loop { break; }
    unit();
    if value == 42 && tuple().0 == 42 && array()[0] == 42 {
        0xfedcba9876543210u64
    } else { 0 }
}
