enum State { More(u64), Done }
fn next(count: &mut u64, selected: bool) -> State {
    *count += 1;
    if selected { State::More(40) } else { State::Done }
}
pub fn answer() -> u64 {
    let mut count = 0u64;
    let first = if let State::More(value) = next(&mut count, true) {
        value
    } else if let State::More(value) = next(&mut count, true) {
        value + 1
    } else { 0 };
    let second = if let State::More(_) = next(&mut count, false) {
        0
    } else if let State::More(value) = next(&mut count, true) {
        value + 2
    } else { 0 };
    if first == 40 && second == 42 && count == 3 {
        0xfedcba9876543210u64
    } else { 0 }
}
