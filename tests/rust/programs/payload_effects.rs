#![no_std]
#[repr(u32)] enum E { A(i64,u64)=0xf0000000, B=0xf0000001 }
enum Z { None, One(()) }
fn inspect(e:E)->u64{match e{E::A(count,value)=>{if count!=1{return 0;}value},E::B=>0}}
pub extern "C" fn answer()->u64 {
    let mut count=0;
    let e=E::A({count=count*10+1;count},{count=count*10+2;0xfedcba9876543210});
    if count!=12{return 0;}
    let z=Z::One({count+=1;()});
    match z{Z::None=>return 0,Z::One(unit)=>unit};
    if count!=13{return 0;}
    inspect(e)
}
