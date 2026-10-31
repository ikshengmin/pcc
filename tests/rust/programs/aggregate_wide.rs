#![no_std]
struct Wide { a: u64, b: u64, c: u64 }
fn rotate(p: Wide) -> Wide { Wide { c: p.a, a: p.b, b: p.c } }
pub extern "C" fn answer() -> u64 {
    let a = 0xfedc_ba98_7654_3210u64;
    let p = Wide { a, b: 20, c: 22 };
    let q = rotate(p);
    if q.a + q.b == 42 { q.c } else { 0 }
}
