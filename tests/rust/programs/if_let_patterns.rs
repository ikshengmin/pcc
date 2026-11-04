enum Value { A(u64), B(u64), Empty }
fn alternatives(value: Value) -> u64 {
    if let Value::A(40) | Value::B(42) = value { 42 } else { 0 }
}
fn nested(value: (bool, Value)) -> u64 {
    if let (true, Value::B(42)) = value { 42 } else { 0 }
}
pub fn answer() -> u64 {
    let mut count = 0u64;
    if let () = () { count += 1; }
    if let true = (false || true) { count += 1; }
    if let () = (count += 1) {}
    if let mut item = 40u64 { item += 2; count += item; }
    if alternatives(Value::A(40)) == 42 && alternatives(Value::B(42)) == 42
        && alternatives(Value::Empty) == 0 && alternatives(Value::A(41)) == 0
        && nested((true, Value::B(42))) == 42
        && nested((false, Value::B(42))) == 0 && count == 45 {
        0xfedcba9876543210u64
    } else { 0 }
}
