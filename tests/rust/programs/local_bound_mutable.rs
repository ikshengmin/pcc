trait Step { fn bump(&mut self, value: u64) -> u64; }
struct R { x: u64 }
impl Step for R { fn bump(&mut self, value: u64) -> u64 { self.x += value; self.x } }
impl R { fn bump(&mut self, value: u64) -> u64 { 900 } }
fn apply<T: Step>(value: &mut T, delta: u64) -> u64 { value.bump(delta) }
fn forward<U: Step>(value: &mut U) -> u64 { apply(value, 1) }
pub fn answer() -> u64 {
    let mut r = R { x: 38 };
    let mut trace = 0u64;
    let a = apply({ trace = trace * 10 + 1; &mut r }, { trace = trace * 10 + 2; 2u64 });
    let b = forward(&mut r);
    let c = forward(&mut r);
    let direct = r.bump(1);
    if trace == 12 && a == 40 && b == 41 && c == 42 && r.x == 42 && direct == 900 {
        0xfedcba9876543210
    } else { 0 }
}
