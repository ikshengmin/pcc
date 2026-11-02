fn need<T>() -> u64 { 7 }
fn relaxed_inline<T: ?Sized>(value: &T) -> u64 where T: Sized { need::<T>() }
fn relaxed_where<T: Sized>(value: &T) -> u64 where T: ?Sized { need::<T>() }
fn copy_first<T: Copy>(value: &T) -> u64 where T: ?Sized { need::<T>() }
fn copy_later<T: ?Sized>(value: &T) -> u64 where T: Copy { need::<T>() }
fn split<T>(value: &T) -> u64 where T: ?Sized, T: Sized, T: Sized { need::<T>() }
fn ordered<T>(value: &T) -> u64 where T: Copy, T: ?Sized, T: Copy { need::<T>() }
pub fn answer() -> u64 {
    let n = 42u64;
    let total = relaxed_inline(&n) + relaxed_where(&n) + copy_first(&n)
        + copy_later(&n) + split(&n) + ordered(&n);
    if total == 42 { 0xfedcba9876543210 } else { 0 }
}
