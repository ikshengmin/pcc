#![no_std]
struct State { value: i64 }
pub extern "C" fn answer() -> i64 {
    let mut p = State { value: 0 };
    let mut n = 0;
    while n < 4 {
        let q = p;
        n += 1;
        p = State { value: q.value + 10 };
        if n == 2 { continue; }
        if n == 4 { let q = p; p = q; break; }
    }
    p.value + 2
}
