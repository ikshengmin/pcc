enum State { More(u64), Done }
fn read(value: State) -> u64 {
    if let State::More(value) = value {
        value
    } else {
        match value { State::More(_) => 0, State::Done => 2 }
    }
}
pub fn answer() -> u64 {
    let value = 42u64;
    let result = read(State::More(40)) + read(State::Done);
    if let false = true { let value = 0u64; }
    if result == value { 0xfedcba9876543210u64 } else { 0 }
}
