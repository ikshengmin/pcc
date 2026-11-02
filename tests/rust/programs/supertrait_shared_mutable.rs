trait Read { fn read(&self) -> u64; }
trait Write: Read { fn add(&mut self, n:u64); }
struct R { n:u64 }
impl Write for R { fn add(&mut self, n:u64) { self.n = self.n + n; } }
impl Read for R { fn read(&self) -> u64 { self.n } }
pub fn answer() -> u64 {
    let mut r=R{n:40};
    let shared=&r;
    let before=shared.read();
    let mutable=&mut r;
    mutable.add(2);
    if before==40 && <R as Read>::read(&r)==42 {0xfedcba9876543210u64} else {0}
}
