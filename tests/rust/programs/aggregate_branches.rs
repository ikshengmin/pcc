#![no_std]
struct Pair { value: u64, tag: i32 }
fn select(p: Pair, take: bool) -> Pair {
    if take { p } else { Pair { value: 0, tag: 0 } }
}
fn indirect(take: bool) -> u64 {
    (if take {
        Pair { value: 0xfedc_ba98_7654_3210u64, tag: 1 }
    } else {
        Pair { value: 0, tag: 0 }
    }).value
}
pub extern "C" fn answer() -> u64 {
    let first = select(Pair { value: indirect(true), tag: 42 }, true);
    let second = select(Pair { value: 99, tag: 99 }, false);
    if first.tag == 42 && second.value == 0 && indirect(false) == 0 {
        first.value
    } else { 1 }
}
