trait Read { fn select(&self)->u64; }
trait Write { fn put<U>(&mut self,next:u64,value:U)->U; }
struct Cell { value:u64 }
impl Read for Cell { fn select(&self)->u64 {self.value} }
impl Write for Cell { fn put<V>(&mut self,next:u64,value:V)->V {self.value=next;value} }
impl Cell { fn select(&mut self)->u64 {99} }
pub fn answer()->u64 {
    let mut c=Cell{value:17};
    let a=c.select();
    let r=&c;
    let b=r.select();
    let m=&mut c;
    let d=m.select();
    let ok=<Cell as Write>::put(m,42,true);
    let e=c.select();
    if a==17 && b==17 && d==99 && ok && e==42 {0xfedcba9876543210u64} else {0}
}
