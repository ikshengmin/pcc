#![no_std]
fn identity(value:usize)->usize{value}
pub fn answer()->u64{
    let wide:usize=0xfedcba9876543210usize;
    let negative:isize=-1isize;
    if negative as usize!=18446744073709551615usize{return 0;}
    let minimum:isize=-9223372036854775808isize;
    if minimum as u64!=0x8000000000000000u64{return 0;}
    identity(wide) as u64
}
