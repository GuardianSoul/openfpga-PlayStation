// SPDX-License-Identifier: MIT
// CHD CD images to .cue/.bin, as `chdman extractcd -o <name>.cue -ob <name>.bin`
// writes them (MAME src/tools/chdman.cpp), so the files match what
// make_pocket_instances.py --convert makes with chdman.

use super::{convert, msf_text as msf, name_of, split_cue, write_bytes, write_parts};
use ::chd::Chd;
use std::fs;
use std::path::{Path, PathBuf};

const PADDING: u64 = 4; // CD_TRACK_PADDING: tracks start on 4-frame boundaries

struct Track {
    kind: String,
    frames: u64,
    pregap: u64,
    pregap_stored: bool, // PGTYPE V...: the pregap is part of FRAMES
    postgap: u64,
}

impl Track {
    fn data_size(&self) -> Option<u64> {
        Some(match self.kind.as_str() {
            "MODE1" | "MODE2_FORM1" => 2048,
            "MODE2_FORM2" => 2324,
            "MODE2" | "MODE2_FORM_MIX" => 2336,
            "MODE1_RAW" | "MODE2_RAW" | "AUDIO" => 2352,
            _ => return None,
        })
    }

    fn cue_kind(&self) -> String {
        match self.kind.as_str() {
            "AUDIO" => "AUDIO".into(),
            k => format!("{}/{:04}", if k.starts_with("MODE1") { "MODE1" } else { "MODE2" }, self.data_size().unwrap_or(0)),
        }
    }
}

/// CHT2 / CHTR text metadata: "TRACK:1 TYPE:MODE2_RAW SUBTYPE:NONE FRAMES:4 ...".
fn parse_track(text: &[u8]) -> Option<(u64, Track)> {
    let text = String::from_utf8_lossy(text);
    let field = |key: &str| {
        text.trim_end_matches('\0')
            .split_whitespace()
            .find_map(|w| w.strip_prefix(key).and_then(|v| v.strip_prefix(':')))
            .map(String::from)
    };
    let number = |key: &str| field(key).and_then(|v| v.parse::<u64>().ok());
    Some((
        number("TRACK")?,
        Track {
            kind: field("TYPE")?,
            frames: number("FRAMES")?,
            pregap: number("PREGAP").unwrap_or(0),
            pregap_stored: field("PGTYPE").is_some_and(|t| t.starts_with('V')),
            postgap: number("POSTGAP").unwrap_or(0),
        },
    ))
}

type Image = Chd<std::io::BufReader<fs::File>>;

fn open(chd: &Path) -> Result<(Image, Vec<(u64, Track)>), String> {
    let file = fs::File::open(chd).map_err(|e| e.to_string())?;
    let mut image = Chd::open(std::io::BufReader::new(file), None).map_err(|e| format!("not a readable CHD ({e})"))?;
    if image.header().parent_sha1().is_some_and(|sha| sha.iter().any(|&b| b != 0)) {
        return Err("needs its parent CHD".into());
    }
    let metadata: Vec<::chd::metadata::Metadata> = image.metadata_refs().try_into().map_err(|e| format!("metadata: {e}"))?;
    let cht2 = u32::from_be_bytes(*b"CHT2");
    let chtr = u32::from_be_bytes(*b"CHTR");
    let mut tracks: Vec<(u64, Track)> = metadata
        .iter()
        .filter(|m| m.metatag == cht2 || m.metatag == chtr)
        .map(|m| parse_track(&m.value).ok_or("unreadable track metadata"))
        .collect::<Result<_, _>>()?;
    if tracks.is_empty() {
        return Err("not a CD image (no track metadata)".into());
    }
    tracks.sort_by_key(|t| t.0);
    if tracks.iter().any(|(_, t)| t.data_size().is_none()) {
        return Err("unknown track type".into());
    }
    Ok((image, tracks))
}

/// Bytes of the extracted .bin (the sum of the tracks' frames).
pub(super) fn output_bytes(chd: &Path) -> Result<u64, String> {
    let (_, tracks) = open(chd)?;
    Ok(tracks.iter().map(|(_, t)| t.frames * t.data_size().unwrap()).sum())
}

/// Extract chd into dest/<name>.cue and <name>.bin; with split, into one
/// file per track (as split_cue writes them). Returns the cue.
pub(super) fn extract(chd: &Path, dest: &Path, split: bool) -> Result<PathBuf, String> {
    let (mut image, tracks) = open(chd)?;
    let total: u64 = tracks.iter().map(|(_, t)| t.frames * t.data_size().unwrap()).sum();
    let hunk_size = image.header().hunk_size() as u64;
    let unit = image.header().unit_bytes() as u64;
    let hunks = image.header().hunk_count() as u64;
    let stem = super::stem(&name_of(chd)).to_string();
    let work = if split { dest.join(".chd-extract") } else { dest.to_path_buf() };
    let bin_name = format!("{stem}.bin");
    let mut cue = String::new();
    let mut hunk = image.get_hunksized_buffer();
    let mut compressed = Vec::new();
    let mut loaded: Option<u64> = None;
    let mut error = None;
    write_parts(&work.join(&bin_name), Some(total), &mut |out| {
        let (mut written, mut disc) = (0u64, 0u64); // frames in the .bin, units in the CHD
        let mut buffer = Vec::with_capacity(1 << 22);
        for (k, (number, t)) in tracks.iter().enumerate() {
            if k == 0 {
                cue += &format!("FILE \"{bin_name}\" BINARY\n");
            }
            cue += &format!("  TRACK {number:02} {}\n", t.cue_kind());
            if t.pregap > 0 && !t.pregap_stored {
                cue += &format!("    PREGAP {}\n    INDEX 01 {}\n", msf(t.pregap), msf(written));
            } else if t.pregap > 0 {
                cue += &format!("    INDEX 00 {}\n    INDEX 01 {}\n", msf(written), msf(written + t.pregap));
            } else {
                cue += &format!("    INDEX 01 {}\n", msf(written));
            }
            if t.postgap > 0 {
                cue += &format!("    POSTGAP {}\n", msf(t.postgap));
            }
            let size = t.data_size().unwrap() as usize;
            for _ in 0..t.frames {
                let offset = disc * unit;
                let (n, at) = (offset / hunk_size, (offset % hunk_size) as usize);
                if n >= hunks || at + size > hunk.len() {
                    error = Some("track data past the end of the image".to_string());
                    return Err(std::io::Error::other("short image"));
                }
                if loaded != Some(n) {
                    let result = image.hunk(n as u32).and_then(|mut h| h.read_hunk_in(&mut compressed, &mut hunk));
                    if let Err(e) = result {
                        error = Some(format!("hunk {n}: {e}"));
                        return Err(std::io::Error::other("bad hunk"));
                    }
                    loaded = Some(n);
                }
                let frame = &hunk[at..at + size];
                if t.kind == "AUDIO" {
                    // CHD keeps CD audio big-endian; .bin files hold it little-endian.
                    buffer.extend(frame.chunks_exact(2).flat_map(|p| [p[1], p[0]]));
                } else {
                    buffer.extend_from_slice(frame);
                }
                if buffer.len() >= 1 << 22 {
                    out.write_all(&buffer)?;
                    buffer.clear();
                }
                disc += 1;
                written += 1;
            }
            disc += (PADDING - t.frames % PADDING) % PADDING;
        }
        out.write_all(&buffer)
    })
    .map_err(|e| error.take().unwrap_or_else(|| convert::reason(e)))?;
    let cue_path = work.join(format!("{stem}.cue"));
    write_bytes(&cue_path, cue.as_bytes()).map_err(|e| e.to_string())?;
    if !split {
        return Ok(cue_path);
    }
    let result = split_cue(&cue_path, dest, true);
    let _ = fs::remove_dir_all(&work);
    result
}
