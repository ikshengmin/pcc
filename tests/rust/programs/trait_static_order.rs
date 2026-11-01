trait Change { fn step(&mut self,next:u64)->u64; }
struct Cell { value:u64 }
impl Change for Cell { fn step(&mut self,next:u64)->u64 {self.value=self.value*10+next;self.value} }
pub fn answer()->u64 {
    let mut c=Cell{value:1};
    let mut count=0u64;
    let a=({count=count*10+1;&mut c}).step({count=count*10+2;2});
    let alias=&mut c;
    let b=alias.step({count=count*10+3;3});
    if a==12 && b==123 && count==123 && c.value==123 {0xfedcba9876543210u64} else {0}
}
