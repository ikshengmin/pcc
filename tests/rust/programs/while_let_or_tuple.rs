enum State { More(u64), Done }
pub fn answer() -> u64 {
    let mut number = 1u64;
    let mut sum = 0u64;
    while let 1 | 2 = number {
        sum += number;
        number += 1;
    }
    let mut pair = (true, State::More(39));
    while let (true, State::More(value)) = pair {
        sum += value;
        pair = (false, State::Done);
    }
    if sum == 42 && number == 3 { 0xfedcba9876543210u64 } else { 0 }
}
