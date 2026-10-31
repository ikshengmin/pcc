#![no_std]
struct Empty { value: () }
fn pass_empty(value: Empty) -> Empty { value }
fn pass_tuple(value: ((), ((),))) -> ((), ((),)) { value }
pub extern "C" fn answer() -> i64 {
    let mut trace = 0;
    let p = pass_empty(Empty { value: { trace += 20; } });
    let t = pass_tuple((p.value, ({ trace += 22; },)));
    let copied = t;
    copied;
    t;
    trace
}
