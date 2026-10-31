#![no_std]
struct Pair<T, U> { first: T, second: U }
struct UnusedButChecked<T> { value: T }
fn pass(value: Pair<u64, bool>) -> Pair<u64, bool> { value }
fn answer() -> u64 {
    let value = Pair::<u64, bool> {
        second: true,
        first: 0xfedcba9876543210u64,
    };
    let result = pass(value);
    if result.second { result.first } else { 0 }
}
