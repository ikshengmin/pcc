#![no_std]
struct Part { value: u64 }
struct Pair<T, U> { left: T, right: U }
fn answer() -> u64 {
    let mut pair = Pair::<Part, Part> {
        left: Part { value: 17 }, right: Part { value: 25 },
    };
    let left = pair.left;
    if left.value + pair.right.value != 42 { return 0; }
    pair = Pair::<Part, Part> {
        left: Part { value: 0xfedcba9876543210u64 }, right: Part { value: 0 },
    };
    let whole = pair;
    whole.left.value + whole.right.value
}
