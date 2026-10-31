#![no_std]
struct Pair<T, U> { first: T, second: U }
enum Sum<T, U> { Pair(T, U), Empty }
fn answer() -> u64 {
    let mut order = 0;
    let pair = Pair::<u64, u64> {
        second: { order = order * 10 + 2; 25 },
        first: { order = order * 10 + 1; 17 },
    };
    if order != 21 || pair.first + pair.second != 42 { return 0; }
    order = 0;
    let sum = Sum::<u64, u64>::Pair(
        { order = order * 10 + 1; 0xfedcba9876543200u64 },
        { order = order * 10 + 2; 16 },
    );
    if order != 12 { return 0; }
    match sum { Sum::Pair(first, second) => first + second, Sum::Empty => 0 }
}
