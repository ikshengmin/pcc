// Original PCC specification fixture, not extracted or rewritten core code.
#![no_std]
#[repr(i32)]
enum Sign { Minus = -7, Zero = 0, Plus }
enum Mode { First, Second = 17, Third }
fn reverse(x: Sign) -> Sign {
    match x { Sign::Minus => Sign::Plus, Sign::Zero => Sign::Zero, Sign::Plus => Sign::Minus }
}
fn classify(x: Sign) -> i64 {
    let first = match x { Sign::Minus => 11, Sign::Zero => 13, Sign::Plus => 17 };
    // Tag-only matching must not move the non-Copy place.
    let second = match x { Sign::Minus | Sign::Plus => 19, _ => 23 };
    first + second + x as i64
}
fn choose(x: i64) -> Mode { if x < 0 {Mode::First} else {Mode::Third} }
fn wide(x: Mode) -> u64 {
    match x { Mode::First => 0x8123456789abcdef, Mode::Second => 3, Mode::Third => 0xfedcba9876543210 }
}
pub extern "C" fn answer() -> u64 {
    if classify(Sign::Minus) != 23 {return 0;}
    if classify(reverse(Sign::Minus)) != 37 {return 0;}
    if (Sign::Minus as i64) != -7 {return 0;}
    if (Mode::Third as u64) != 18 {return 0;}
    wide(choose(9))
}
