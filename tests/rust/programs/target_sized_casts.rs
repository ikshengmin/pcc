#![no_std]
fn wrap(x:usize)->usize{x+1usize}
fn signed(x:isize)->bool{x<0isize}
fn id<T>(x:T)->T{x}
pub fn answer()->u64{
    let a:i32=-2;
    if a as usize!=18446744073709551614usize{return 0;}
    let b:u32=4294967295;
    if b as isize!=4294967295isize{return 0;}
    let wide:usize=0x100000002usize;
    if wide as u32!=2u32{return 0;}
    let negative:isize=-4294967295isize;
    if negative as i32!=1i32{return 0;}
    if wrap(18446744073709551615usize)!=0usize{return 0;}
    if !signed(-1isize){return 0;}
    if !0usize!=18446744073709551615usize{return 0;}
    match id(2usize){2usize=>{},_=>return 0,}
    let u=id(42usize);
    let f=id(42u64);
    if u as u64!=f{return 0;}
    0xfedcba9876543210
}
