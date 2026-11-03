// PCC-authored API observer, separate from the unchanged upstream crate.
extern crate fallible_streaming_iterator;

use fallible_streaming_iterator::{convert, FallibleStreamingIterator};
use std::io::{self, Write};

struct Cursor {
    values: [u64; 3],
    position: usize,
    current: Option<usize>,
    fail_at: Option<usize>,
}

impl FallibleStreamingIterator for Cursor {
    type Item = u64;
    type Error = u32;

    fn advance(&mut self) -> Result<(), u32> {
        let position = self.position;
        self.position += 1;
        self.current = None;
        if self.fail_at == Some(position) {
            return Err(7);
        }
        if position < self.values.len() {
            self.current = Some(position);
        }
        Ok(())
    }

    fn get(&self) -> Option<&u64> {
        match self.current {
            Some(position) => Some(&self.values[position]),
            None => None,
        }
    }
}

fn cursor(fail_at: Option<usize>) -> Cursor {
    Cursor { values: [10, 20, 30], position: 0, current: None, fail_at: fail_at }
}

fn main() {
    let mut success = cursor(None);
    assert_eq!(success.get(), None);
    assert_eq!(success.next(), Ok(Some(&10)));
    assert_eq!(success.next(), Ok(Some(&20)));
    assert_eq!(success.next(), Ok(Some(&30)));
    assert_eq!(success.next(), Ok(None));
    assert_eq!(success.position, 4);

    let mut failing = cursor(Some(1)).map_err(|error| error + 100);
    assert_eq!(failing.next(), Ok(Some(&10)));
    assert_eq!(failing.next(), Err(107));

    let mut borrowed = cursor(None);
    assert_eq!(borrowed.by_ref().take(2).count(), Ok(2));
    assert_eq!(borrowed.next(), Ok(Some(&30)));
    let filtered = cursor(None).filter(|value| *value >= 20);
    assert_eq!(filtered.count(), Ok(2));

    let values = [4u64, 8u64];
    let inputs = [Ok(&values[0]), Err(9u32), Ok(&values[1])];
    let mut converted = convert(inputs.iter().cloned());
    assert_eq!(converted.next(), Ok(Some(&4)));
    assert_eq!(converted.next(), Err(9));

    let mut empty = fallible_streaming_iterator::empty::<u64, u32>();
    assert_eq!(empty.next(), Ok(None));
    io::stdout().write_all(&0xfedcba9876543210u64.to_le_bytes()).unwrap();
}
