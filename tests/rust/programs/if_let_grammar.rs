fn tuple() -> (u64,) { if let false = true {} (42,) }
fn array() -> [u64; 1] { if let false = true {} [42] }
fn chain(first: bool, second: bool) -> u64 {
    (if first { (1u64,) } else if let true = second {
        (42u64,)
    } else { (2u64,) }).0
}
fn arms(value: bool) -> u64 {
    match value {
        true => if let false = value { 0 } else { 42 }
        false => if true { 42 } else if let true = value { 0 } else { 0 }
    }
}
pub fn answer() -> u64 {
    if tuple().0 == 42 && array()[0] == 42 && chain(false, true) == 42
        && arms(true) == 42 && arms(false) == 42 {
        0xfedcba9876543210u64
    } else { 0 }
}
