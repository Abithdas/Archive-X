//! Port of the pure parts of `telegram_archive/core/size_optimizer.py`: bitrate maths and the FFmpeg command line.
//!
//! The async encode loop (`_run_encode`, `optimize_for_limit`) spawns FFmpeg and reports progress; it moves with the media worker in
//! a later phase. Everything here is deterministic.

use std::path::Path;

/// A conservative video bitrate in bits/sec for a target file size (`audio_kbps` 128 and `safety` 0.94 in the app).
pub fn target_video_bitrate(duration: f64, target_bytes: i64, audio_kbps: i64, safety: f64) -> i64 {
    let duration = if duration >= 1.0 { duration } else { 1.0 };
    let usable = ((target_bytes as f64 * safety) as i64).max(1_000_000);
    let total_bps = usable as f64 * 8.0 / duration;
    let video_bps = total_bps - (audio_kbps * 1000) as f64;
    (video_bps as i64).max(250_000)
}

/// The bitrate for the next pass after a pass produced `size` bytes against `limit` bytes.
pub fn next_bitrate(bitrate: i64, limit: i64, size: i64) -> i64 {
    let ratio = (limit as f64 / size.max(1) as f64) * 0.92;
    ((bitrate as f64 * ratio) as i64).max(250_000)
}

/// Duration to size the target by: the probed one, else derived from the container bitrate and file size, else 1 second.
pub fn effective_duration(duration: f64, bitrate: f64, file_size: u64) -> f64 {
    if duration > 1.0 {
        return duration;
    }
    if bitrate > 0.0 {
        return (file_size as f64 * 8.0 / bitrate).max(1.0);
    }
    1.0
}

fn args(list: &[&str]) -> Vec<String> {
    list.iter().map(|s| s.to_string()).collect()
}

/// Encoder arguments for one pass. `fps` of 0 means "unknown" (Python's `fps or 30`).
pub fn quality_args(encoder: &str, bitrate: i64, fps: f64) -> Vec<String> {
    let kbps = (bitrate / 1000).max(250);
    let max_kbps = (kbps + 1).max((kbps as f64 * 1.10) as i64);
    let buf = (kbps * 2).max(max_kbps * 2);
    let fps = if fps == 0.0 { 30.0 } else { fps };
    let gop = 30.max((fps.max(10.0) * 2.0).round_ties_even() as i64); // Python's round() is round-half-even
    let (k, m, b, g) = (format!("{kbps}k"), format!("{max_kbps}k"), format!("{buf}k"), gop.to_string());
    let rate = |extra_before: &[&str], extra_after: &[&str]| -> Vec<String> {
        let mut v = args(extra_before);
        v.extend(args(&["-b:v", &k, "-maxrate", &m, "-bufsize", &b]));
        v.extend(args(extra_after));
        v.extend(args(&["-g", &g]));
        v
    };
    match encoder {
        "h264_nvenc" => {
            let mut v = args(&["-c:v", "h264_nvenc", "-preset", "p5", "-tune", "hq", "-rc", "vbr"]);
            v.extend(args(&["-b:v", &k, "-maxrate", &m, "-bufsize", &b]));
            v.extend(args(&["-rc-lookahead", "16", "-spatial_aq", "1", "-temporal_aq", "1", "-aq-strength", "8", "-pix_fmt", "yuv420p", "-g", &g]));
            v
        }
        // No -global_quality: with it QSV switches to constant quality and ignores the bitrate.
        "h264_qsv" => rate(&["-c:v", "h264_qsv", "-preset", "medium"], &["-pix_fmt", "nv12"]),
        "h264_amf" => rate(&["-c:v", "h264_amf", "-quality", "quality", "-rc", "vbr_peak"], &["-pix_fmt", "yuv420p"]),
        _ => rate(&["-c:v", "libx264", "-preset", "veryfast"], &["-pix_fmt", "yuv420p"]),
    }
}

/// The FFmpeg command for one pass. Source resolution is kept; odd sizes are rounded down to even.
pub fn encode_cmd(ffmpeg: &str, src: &Path, out: &Path, width: i64, height: i64, fps: f64, encoder: &str, bitrate: i64) -> Vec<String> {
    let fps = if fps == 0.0 { 30.0 } else { fps };
    let mut cmd = args(&[ffmpeg, "-hide_banner", "-loglevel", "error", "-nostats", "-progress", "pipe:1", "-y", "-i"]);
    cmd.push(src.display().to_string());
    cmd.extend(args(&["-map", "0:v:0", "-map", "0:a:0?", "-sn", "-dn", "-map_metadata", "-1"]));
    if width % 2 != 0 || height % 2 != 0 {
        cmd.push("-vf".into());
        cmd.push(format!("scale={}:{}", width - width.rem_euclid(2), height - height.rem_euclid(2)));
    }
    cmd.extend(quality_args(encoder, bitrate, fps));
    cmd.extend(args(&["-c:a", "aac", "-b:a", "128k", "-ac", "2", "-ar", "48000", "-movflags", "+faststart"]));
    cmd.push(out.display().to_string());
    cmd
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn bitrate_matches_python() {
        // values produced by the Python original (see docs/rust-migration-plan.md, progress log)
        assert_eq!(target_video_bitrate(600.0, 2_000_000_000, 128, 0.94), 24_938_666);
        assert_eq!(target_video_bitrate(0.0, 2_000_000_000, 128, 0.94), 15_039_872_000);
        assert_eq!(target_video_bitrate(36000.0, 1_000, 128, 0.94), 250_000);
        assert_eq!(target_video_bitrate(3600.5, 1_999_999_999, 128, 0.94), 4_049_197);
    }

    #[test]
    fn next_pass_scales_down_with_a_margin() {
        assert_eq!(next_bitrate(10_000_000, 2_000, 3_000), 6_133_333);
        assert_eq!(next_bitrate(300_000, 1, 1_000_000), 250_000);
    }

    #[test]
    fn duration_falls_back_to_container_bitrate() {
        assert_eq!(effective_duration(90.0, 0.0, 0), 90.0);
        assert_eq!(effective_duration(0.0, 8_000_000.0, 100_000_000), 100.0);
        assert_eq!(effective_duration(0.5, 0.0, 10), 1.0);
        assert_eq!(effective_duration(0.0, 1e12, 1), 1.0);
    }

    #[test]
    fn libx264_args() {
        let a = quality_args("libx264", 4_000_000, 25.0);
        assert_eq!(a.join(" "), "-c:v libx264 -preset veryfast -b:v 4000k -maxrate 4400k -bufsize 8800k -pix_fmt yuv420p -g 50");
    }

    #[test]
    fn hardware_encoders_keep_their_flags() {
        assert_eq!(
            quality_args("h264_nvenc", 4_000_000, 30.0).join(" "),
            "-c:v h264_nvenc -preset p5 -tune hq -rc vbr -b:v 4000k -maxrate 4400k -bufsize 8800k -rc-lookahead 16 -spatial_aq 1 -temporal_aq 1 -aq-strength 8 -pix_fmt yuv420p -g 60"
        );
        let qsv = quality_args("h264_qsv", 4_000_000, 30.0).join(" ");
        assert_eq!(qsv, "-c:v h264_qsv -preset medium -b:v 4000k -maxrate 4400k -bufsize 8800k -pix_fmt nv12 -g 60");
        assert!(!qsv.contains("global_quality"));
        assert_eq!(
            quality_args("h264_amf", 4_000_000, 30.0).join(" "),
            "-c:v h264_amf -quality quality -rc vbr_peak -b:v 4000k -maxrate 4400k -bufsize 8800k -pix_fmt yuv420p -g 60"
        );
    }

    #[test]
    fn low_bitrate_and_gop_floors() {
        let a = quality_args("libx264", 100_000, 0.0).join(" ");
        assert!(a.contains("-b:v 250k -maxrate 275k -bufsize 550k"), "{a}");
        assert!(a.ends_with("-g 60"), "{a}");
        // fps 3 is lifted to 10 -> gop 20, floored at 30
        assert!(quality_args("libx264", 4_000_000, 3.0).join(" ").ends_with("-g 30"));
        // 14.75 fps -> 29.5 -> banker's rounding gives 30 (round-half-even) and 15.25 -> 30.5 -> 30
        assert!(quality_args("libx264", 4_000_000, 15.25).join(" ").ends_with("-g 30"));
        assert!(quality_args("libx264", 4_000_000, 29.97).join(" ").ends_with("-g 60"));
    }

    #[test]
    fn odd_sizes_are_scaled_down_to_even() {
        let c = encode_cmd("ffmpeg", Path::new("in.mkv"), Path::new("out.mp4"), 1921, 1081, 24.0, "libx264", 4_000_000);
        let line = c.join(" ");
        assert!(line.contains("-vf scale=1920:1080"), "{line}");
        assert!(line.starts_with("ffmpeg -hide_banner -loglevel error -nostats -progress pipe:1 -y -i in.mkv -map 0:v:0 -map 0:a:0? -sn -dn -map_metadata -1 -vf"));
        assert!(line.ends_with("-c:a aac -b:a 128k -ac 2 -ar 48000 -movflags +faststart out.mp4"));
        let even = encode_cmd("ffmpeg", Path::new("in.mkv"), Path::new("out.mp4"), 1920, 1080, 24.0, "libx264", 4_000_000).join(" ");
        assert!(!even.contains("-vf"));
    }
}
