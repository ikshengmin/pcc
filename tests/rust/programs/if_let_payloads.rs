enum State { More(u64), Done }
fn read(value: State) -> u64 {
    if let State::More(item) = value { item } else { 2 }
}
pub fn answer() -> u64 {
    if read(State::More(40)) + read(State::Done) == 42 {
        0xfedcba9876543210u64
    } else { 0 }
}
