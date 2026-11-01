#![no_std]
fn forward<T>(value:T,n:u64)->T{if n==0{value}else{back(value,n-1)}}
fn back<U>(value:U,n:u64)->U{forward(value,n)}
pub extern "C" fn answer()->u64{forward(0xfedcba9876543210,6)}
