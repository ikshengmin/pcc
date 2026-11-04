struct R { value: u64 }
enum State { More(R), Done }
fn read(value: State) -> u64 {
    if let State::More(item) = value {
        item.value
    } else {
        match value { State::More(other) => other.value, State::Done => 2 }
    }
}
fn tuple_fields() -> u64 {
    let pair = (R { value: 40 }, R { value: 2 });
    if let (first, _) = pair { first.value + pair.1.value } else { 0 }
}
pub fn answer() -> u64 {
    let untouched = R { value: 42 };
    if let _ = untouched {}
    if read(State::More(R { value: 40 })) + read(State::Done) == 42
        && tuple_fields() == 42 && untouched.value == 42 {
        0xfedcba9876543210u64
    } else { 0 }
}
