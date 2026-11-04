enum State { More(u64), Done }
fn read(value: State) -> u64 {
    let mut result = 0u64;
    while let State::More(value) = value {
        result = value;
        break;
    }
    result
}
pub fn answer() -> u64 {
    if read(State::More(42)) == 42 && read(State::Done) == 0 {
        0xfedcba9876543210u64
    } else { 0 }
}
