#![no_std]
struct WithUnit { empty: (), value: i64 }
fn unit() {}
fn pair(value: ((), i64)) -> ((), i64) { value }
pub extern "C" fn answer() -> i64 {
    let p = WithUnit { empty: unit(), value: 42 };
    let q = pair((p.empty, p.value));
    q.1
}
