#![no_std]
struct Counters { small: i32, wide: i64 }
fn bump(mut p: Counters) -> Counters {
    p.small += 1;
    p.wide += 2;
    p
}
pub extern "C" fn answer() -> i64 {
    let mut trace = 0;
    let p = Counters {
        wide: { trace = trace * 10 + 1; 40 },
        small: { trace = trace * 10 + 2; 0 },
    };
    let q = bump(p);
    let t = (q.small, q.wide);
    if trace == 12 && t.0 == 1 { t.1 } else { -1 }
}
