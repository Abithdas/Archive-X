//! Port of `telegram_archive/core/sql_batches.py`.
//!
//! SQLite refuses a statement with too many `?` placeholders, so long id lists are walked in batches.

pub const BATCH: usize = 500;

/// Split `items` into chunks of `size` (a size below 1 means 1, as in Python's `max(1, int(size))`).
pub fn batches<T: Clone>(items: &[T], size: usize) -> Vec<Vec<T>> {
    items.chunks(size.max(1)).map(<[T]>::to_vec).collect()
}

/// `"?,?,?"` for `n` placeholders (empty string for 0).
pub fn marks(n: usize) -> String {
    vec!["?"; n].join(",")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn splits_and_keeps_order() {
        assert_eq!(batches(&[1, 2, 3, 4, 5], 2), vec![vec![1, 2], vec![3, 4], vec![5]]);
        assert!(batches::<i32>(&[], 2).is_empty());
    }

    #[test]
    fn size_below_one_is_one() {
        assert_eq!(batches(&[1, 2], 0), vec![vec![1], vec![2]]);
    }

    #[test]
    fn marks_match_python() {
        assert_eq!(marks(0), "");
        assert_eq!(marks(1), "?");
        assert_eq!(marks(3), "?,?,?");
    }
}
