#![no_std]
struct Leaf { value: i64 }
struct Pair { a: Leaf, b: Leaf }
fn consume(value: Leaf) -> i64 { value.value }
pub extern "C" fn answer() -> i64 {
    let mut pair = Pair { a: Leaf { value: 10 }, b: Leaf { value: 20 } };
    let first = consume(pair.a);
    let second = pair.b.value;
    pair.a = Leaf { value: 12 };
    let restored = pair;
    first + second + restored.a.value
}
