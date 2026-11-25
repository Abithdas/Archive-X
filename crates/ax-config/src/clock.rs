//! The two clock readings `config.py` writes: UTC to the second (`datetime.now(timezone.utc).isoformat(timespec="seconds")`) and
//! local time as `%Y%m%d_%H%M%S` (the name of the folder a restore moves the old data to).

use std::time::{SystemTime, UNIX_EPOCH};

/// Days since 1970-01-01 to (year, month, day), proleptic Gregorian (Howard Hinnant's algorithm).
fn civil_from_days(days: i64) -> (i64, u32, u32) {
    let z = days + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z.rem_euclid(146_097);
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let y = yoe + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = (doy - (153 * mp + 2) / 5 + 1) as u32;
    let m = if mp < 10 { mp + 3 } else { mp - 9 } as u32;
    (if m <= 2 { y + 1 } else { y }, m, d)
}

/// Broken-down time of a Unix time shifted by `offset_secs`.
pub(crate) fn fields(unix: i64, offset_secs: i64) -> (i64, u32, u32, u32, u32, u32) {
    let t = unix + offset_secs;
    let (y, m, d) = civil_from_days(t.div_euclid(86_400));
    let rem = t.rem_euclid(86_400);
    (y, m, d, (rem / 3600) as u32, (rem % 3600 / 60) as u32, (rem % 60) as u32)
}

pub(crate) fn utc_iso_at(unix: i64) -> String {
    let (y, mo, d, h, mi, s) = fields(unix, 0);
    format!("{y:04}-{mo:02}-{d:02}T{h:02}:{mi:02}:{s:02}+00:00")
}

fn now_unix() -> i64 {
    SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_secs() as i64).unwrap_or(0)
}

pub(crate) fn utc_iso_now() -> String {
    utc_iso_at(now_unix())
}

#[cfg(unix)]
fn local_offset(unix: i64) -> i64 {
    // SAFETY: localtime_r writes into the struct we give it and reads only the time_t.
    unsafe {
        let t: libc::time_t = unix as libc::time_t;
        let mut tm: libc::tm = std::mem::zeroed();
        if libc::localtime_r(&t, &mut tm).is_null() {
            return 0;
        }
        tm.tm_gmtoff as i64
    }
}

#[cfg(windows)]
fn local_offset(unix: i64) -> i64 {
    use windows_sys::Win32::Foundation::{FILETIME, SYSTEMTIME};
    use windows_sys::Win32::System::Time::{FileTimeToSystemTime, SystemTimeToTzSpecificLocalTime};
    // SAFETY: plain out-parameters; a failed call leaves the offset at zero.
    unsafe {
        let ticks = (unix as u64 + 11_644_473_600) * 10_000_000;
        let ft = FILETIME { dwLowDateTime: ticks as u32, dwHighDateTime: (ticks >> 32) as u32 };
        let (mut utc, mut local): (SYSTEMTIME, SYSTEMTIME) = (std::mem::zeroed(), std::mem::zeroed());
        if FileTimeToSystemTime(&ft, &mut utc) == 0 || SystemTimeToTzSpecificLocalTime(std::ptr::null(), &utc, &mut local) == 0 {
            return 0;
        }
        let secs = |s: &SYSTEMTIME| s.wHour as i64 * 3600 + s.wMinute as i64 * 60 + s.wSecond as i64;
        let day = |s: &SYSTEMTIME| (s.wYear as i64) * 400 + s.wMonth as i64 * 31 + s.wDay as i64;
        let mut diff = secs(&local) - secs(&utc);
        if day(&local) > day(&utc) {
            diff += 86_400;
        } else if day(&local) < day(&utc) {
            diff -= 86_400;
        }
        diff
    }
}

pub(crate) fn local_stamp_at(unix: i64) -> String {
    let (y, mo, d, h, mi, s) = fields(unix, local_offset(unix));
    format!("{y:04}{mo:02}{d:02}_{h:02}{mi:02}{s:02}")
}

pub(crate) fn local_stamp_now() -> String {
    local_stamp_at(now_unix())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn utc_text_matches_isoformat() {
        assert_eq!(utc_iso_at(0), "1970-01-01T00:00:00+00:00");
        assert_eq!(utc_iso_at(1_700_000_000), "2023-11-14T22:13:20+00:00");
        assert_eq!(utc_iso_at(951_782_400), "2000-02-29T00:00:00+00:00");
        assert_eq!(utc_iso_at(1_930_713_967), "2031-03-08T05:26:07+00:00");
        assert_eq!(utc_iso_at(-1), "1969-12-31T23:59:59+00:00");
    }
}
