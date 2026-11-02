struct Cell<T>{value:T}
impl<T> Cell<T>{
    fn choose<U,V>(&self,first:U,last:V)->U{first}
    fn put<U>(&mut self,value:T,extra:U)->U{self.value=value;extra}
    fn take(self)->T{self.value}
}
fn replace<T>(place:&mut T,value:T){*place=value;}
fn keep<T,U>(place:&T,value:U)->U{value}
fn make<T>(value:T)->Cell<T>{Cell::<T>{value:value}}
pub fn answer()->u64{
    let mut cell=make(1);
    let shared=&cell;
    let flag=shared.choose::<_,u32>(true,2);
    let extra=cell.put::<_>(4294967296u64,7u32);
    let unique=&mut cell;
    let ok=unique.put::<_>(0xfedcba9876543210u64,true);
    let mut number=1u64;
    replace::<_>(&mut number,9);
    let another=keep::<_,bool>(&mut number,true);
    if flag && ok && another && extra==7u32 && number==9u64{cell.take()}else{0}
}
