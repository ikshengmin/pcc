mod data { pub struct R{pub value:u64} }
mod ops {
    pub fn bump(x:&mut crate::data::R,n:u64)->u64{x.value=x.value+n;x.value}
    pub fn read(x:&crate::data::R)->u64{x.value}
}
pub fn answer()->u64 {
    let mut r=data::R{value:1};
    let mut trace=0u64;
    let n=ops::bump(&mut r,{trace=trace*10+1;2});
    let m=ops::bump(&mut r,{trace=trace*10+2;n});
    if m==6 && trace==12 && ops::read(&r)==6 {0xfedcba9876543210u64} else {0}
}
