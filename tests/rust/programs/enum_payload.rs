// Original PCC specification program, not a substitute for generic core Option.
#![no_std]
struct Parcel { value:u64, spare:i32 }
enum Choice { Empty, Number(i32), Packet(Parcel,u64) }
fn create(x:i32)->Choice {
    if x<0 {Choice::Number(-7)} else {Choice::Packet(Parcel{value:0xfedcba9876543210,spare:-11},0x8123456789abcdef)}
}
fn extract(x:Choice)->u64 {
    match x {
        Choice::Empty=>0,
        Choice::Number(n)=>n as u64,
        Choice::Packet(p,stamp)=>{if p.spare != -11 || stamp != 0x8123456789abcdef {return 0;} p.value},
    }
}
pub extern "C" fn answer()->u64 {
    if extract(Choice::Empty)!=0 || extract(create(-1))!=0xfffffffffffffff9 {return 0;}
    extract(create(1))
}
