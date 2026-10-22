// SPDX-License-Identifier: MIT
// Game files for the PlayStation core on an Analogue Pocket SD card.
//
// A port of scripts/make_pocket_instances.py (and of the SD card helper
// page, release/docs/instances.js); keep the three in step.
// tests/test_pocket_plugin.py runs this and the script on the same card and
// compares. CHD images are extracted as chdman extractcd would
// (chd_image.rs).
//
// Large writes are guarded (convert.rs): the space for a file plus
// RESERVE is claimed before it is written, a file only gets its name once
// it is written and read back, and originals are only removed after that.

use regex::Regex;
use std::collections::{BTreeMap, HashMap};
use std::fs;
use std::io::{self, Read, Seek, SeekFrom, Write};
use std::path::{Path, PathBuf};
use std::sync::OnceLock;

mod chd_image;
pub mod convert;

const SECTOR: u64 = 2352;
const PREGAP: u64 = 150;
const LIBCRYPT_SLOT: i64 = 99;
const CHEAT_SLOT: i64 = 98;
const CARD_SLOT: i64 = 16;
/// Files a cue-reader core reads from the game file's slots 101+ (disc 1).
const CUE_FILE_SLOTS: usize = 19;
/// Space a large write always leaves free on the SD card.
const RESERVE: u64 = 256 << 20;
const HELP_ENTRY: &str = "! How to add games (see README).json"; // package_release.py's placeholder

fn re(cell: &'static OnceLock<Regex>, pattern: &str) -> &'static Regex {
    cell.get_or_init(|| Regex::new(pattern).unwrap())
}
macro_rules! regex {
    ($pattern:expr) => {{
        static CELL: OnceLock<Regex> = OnceLock::new();
        re(&CELL, $pattern)
    }};
}
fn disc() -> &'static Regex { regex!(r" \(Disc \d+\)") }
fn disc_n() -> &'static Regex { regex!(r"(?i)\s*\(Disc (\d+)\)") }
// Other tags in parentheses (region, release, languages) can differ between
// a game's discs (e.g. FFIX: "(Disc 1) (v1.1)" .. "(USA) (Disc 4) (Rev 1)"),
// so grouping uses the title without them.
fn release() -> &'static Regex { regex!(r"\s*\([^()]*\)") }

// ---- paths ------------------------------------------------------------------

fn name_of(path: &Path) -> String {
    path.file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_default()
}

/// Python's Path.stem: the name without its last suffix.
fn stem(name: &str) -> &str {
    match name.rfind('.') {
        Some(i) if i > 0 => &name[..i],
        _ => name,
    }
}

/// path below base, '/'-separated ('.' for base itself).
fn rel(path: &Path, base: &Path) -> String {
    let parts: Vec<String> = path
        .strip_prefix(base)
        .unwrap_or(path)
        .components()
        .map(|c| c.as_os_str().to_string_lossy().into_owned())
        .collect();
    if parts.is_empty() { ".".into() } else { parts.join("/") }
}

fn segments(path: &Path) -> Vec<String> {
    path.components().map(|c| c.as_os_str().to_string_lossy().into_owned()).collect()
}

fn is_file(path: &Path) -> bool { path.is_file() }

fn size(path: &Path) -> Option<u64> {
    fs::metadata(path).ok().filter(|m| m.is_file()).map(|m| m.len())
}

/// Files under folder whose name passes test, sorted, without macOS '._' files.
fn walk(folder: &Path, test: &dyn Fn(&str) -> bool) -> Vec<PathBuf> {
    fn visit(dir: &Path, test: &dyn Fn(&str) -> bool, found: &mut Vec<PathBuf>) {
        let Ok(entries) = fs::read_dir(dir) else { return };
        for entry in entries.flatten() {
            let path = entry.path();
            let name = name_of(&path);
            match entry.file_type() {
                Ok(t) if t.is_dir() => visit(&path, test, found),
                Ok(t) if t.is_file() && test(&name) && !name.starts_with("._") => found.push(path),
                _ => {}
            }
        }
    }
    let mut found = vec![];
    visit(folder, test, &mut found);
    found.sort_by_key(|p| segments(p));
    found
}

fn has_ext(name: &str, ext: &str) -> bool {
    name.len() > ext.len() && name[name.len() - ext.len()..].eq_ignore_ascii_case(ext)
}

/// A cue's FILE names are relative to its folder.
fn join(folder: &Path, name: &str) -> PathBuf {
    let mut path = folder.to_path_buf();
    for seg in name.split('/').filter(|s| !s.is_empty() && *s != ".") {
        path.push(seg);
    }
    path
}

/// FNV-1a over everything written or read back.
struct Hashing<'a> {
    inner: &'a mut fs::File,
    hash: u64,
    len: u64,
}

impl Write for Hashing<'_> {
    fn write(&mut self, data: &[u8]) -> io::Result<usize> {
        let n = self.inner.write(data)?;
        self.hash = fnv(self.hash, &data[..n]);
        self.len += n as u64;
        Ok(n)
    }
    fn flush(&mut self) -> io::Result<()> { self.inner.flush() }
}

fn fnv(mut hash: u64, data: &[u8]) -> u64 {
    for &b in data {
        hash = (hash ^ b as u64).wrapping_mul(0x100000001b3);
    }
    hash
}
const FNV: u64 = 0xcbf29ce484222325;

fn no_space(needed: u64) -> io::Error {
    io::Error::new(io::ErrorKind::StorageFull, format!("not enough free space (needs {} MB plus {} MB to spare)", mb(needed), mb(RESERVE)))
}

fn mb(bytes: u64) -> u64 { (bytes + (1 << 20) - 1) >> 20 }

/// Whether an error means the disk is (or would get) too full.
fn is_no_space(e: &io::Error) -> bool { e.kind() == io::ErrorKind::StorageFull }

/// Tests set test_card_bytes in the plugin config to make the SD card that
/// small (hosts never set it): a claim fails when the card's files, the claim
/// and RESERVE would not fit.
fn card_too_small_for(path: &Path, bytes: u64) -> bool {
    let card = Path::new("pocket");
    if !path.starts_with(card) {
        return false;
    }
    let Some(limit) = extism_pdk::config::get("test_card_bytes").ok().flatten().and_then(|v| v.parse::<u64>().ok()) else {
        return false;
    };
    let used: u64 = walk(card, &|_| true).iter().filter_map(|p| size(p)).sum();
    used + bytes + RESERVE > limit
}

/// Claim bytes plus RESERVE for file (FAT and exFAT have no sparse files,
/// so this fails with nothing written when the card is too full).
fn claim(file: &fs::File, path: &Path, bytes: u64) -> io::Result<()> {
    if card_too_small_for(path, bytes) {
        return Err(no_space(bytes));
    }
    file.set_len(bytes + RESERVE).map_err(|_| no_space(bytes))
}

/// Write path via path.part, so a stopped run never leaves a short file
/// under the real name. With reserve, the part file first claims that many
/// bytes plus RESERVE. The file is synced and read back before it is renamed.
fn write_parts(path: &Path, reserve: Option<u64>, parts: &mut dyn FnMut(&mut dyn Write) -> io::Result<()>) -> io::Result<()> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)?;
    }
    let partial = path.with_file_name(format!("{}.part", name_of(path)));
    let result = (|| {
        let mut file = fs::OpenOptions::new().read(true).write(true).create(true).truncate(true).open(&partial)?;
        if let Some(n) = reserve {
            claim(&file, &partial, n)?;
        }
        let mut out = Hashing { inner: &mut file, hash: FNV, len: 0 };
        parts(&mut out)?;
        out.flush()?;
        let (hash, len) = (out.hash, out.len);
        file.set_len(len)?;
        file.sync_all()?;
        file.seek(SeekFrom::Start(0))?;
        let (mut check, mut buffer) = (FNV, vec![0u8; 1 << 20]);
        loop {
            let n = file.read(&mut buffer)?;
            if n == 0 { break; }
            check = fnv(check, &buffer[..n]);
        }
        if check != hash {
            return Err(io::Error::other(format!("{}: read back differently from what was written", name_of(path))));
        }
        Ok(())
    })();
    match result {
        Ok(()) => fs::rename(&partial, path),
        Err(e) => {
            let _ = fs::remove_file(&partial);
            Err(e)
        }
    }
}

/// Remove what a run that was stopped part-way (closed, card pulled, power
/// lost) can leave behind: unfinished .part files under the plugin's folders
/// and the space check. Real files are only ever replaced by a finished,
/// verified .part, so these are never needed. Returns how many went.
pub fn clean_leftovers(card: &Path, platform: &str) -> usize {
    const OURS: [&str; 5] = [".bin.part", ".cue.part", ".trk.part", ".json.part", ".sav.part"];
    let mut found = walk(&card.join("Assets").join(platform), &|n| OURS.iter().any(|e| n.ends_with(e)));
    found.extend(walk(&card.join("Saves").join(platform), &|n| OURS.iter().any(|e| n.ends_with(e))));
    let check = card.join(".psx-space-check");
    if check.is_file() { found.push(check); }
    found.iter().filter(|f| fs::remove_file(f).is_ok()).count()
}

fn write_bytes(path: &Path, data: &[u8]) -> io::Result<()> {
    write_parts(path, None, &mut |out| out.write_all(data))
}

/// Copy count bytes of src (from offset) to out.
fn copy_range(src: &Path, offset: u64, count: u64, out: &mut dyn Write) -> io::Result<()> {
    let mut file = fs::File::open(src)?;
    file.seek(SeekFrom::Start(offset))?;
    let mut left = count;
    let mut buffer = vec![0u8; 1 << 22];
    while left > 0 {
        let want = left.min(buffer.len() as u64) as usize;
        let got = file.read(&mut buffer[..want])?;
        if got == 0 {
            return Err(io::Error::new(io::ErrorKind::UnexpectedEof, format!("{}: short read", name_of(src))));
        }
        out.write_all(&buffer[..got])?;
        left -= got as u64;
    }
    Ok(())
}

fn write_zeros(out: &mut dyn Write, count: u64) -> io::Result<()> {
    let zeros = vec![0u8; SECTOR as usize];
    for _ in 0..count / SECTOR {
        out.write_all(&zeros)?;
    }
    Ok(())
}

/// MM:SS:FF as cue sheets write it.
fn msf_text(frames: u64) -> String {
    format!("{:02}:{:02}:{:02}", frames / (75 * 60), frames / 75 % 60, frames % 75)
}

// ---- cue sheets -------------------------------------------------------------

#[derive(Clone, Debug)]
struct Track {
    number: u64,
    kind: String,
    index: BTreeMap<u64, u64>,
    pregap: u64, // 0 when absent (Python treats PREGAP 00:00:00 as absent too)
}

type CueFiles = Vec<(String, Vec<Track>)>;

/// Python's str.splitlines().
fn split_lines(text: &str) -> Vec<&str> {
    let mut lines = vec![];
    let mut start = 0;
    let mut chars = text.char_indices().peekable();
    while let Some((i, c)) = chars.next() {
        if matches!(c, '\n' | '\r' | '\x0b' | '\x0c' | '\x1c' | '\x1d' | '\x1e' | '\u{85}' | '\u{2028}' | '\u{2029}') {
            lines.push(&text[start..i]);
            let mut end = i + c.len_utf8();
            if c == '\r' {
                if let Some(&(j, '\n')) = chars.peek() {
                    chars.next();
                    end = j + 1;
                }
            }
            start = end;
        }
    }
    if start < text.len() {
        lines.push(&text[start..]);
    }
    lines
}

fn number(text: &str) -> Result<u64, String> {
    text.parse().map_err(|_| format!("bad number {text}"))
}

/// [(filename, [track])] in cue order.
fn parse_cue_text(text: &str) -> Result<CueFiles, String> {
    let file_quoted = regex!(r#"^FILE\s+"(.+)"\s+\S+"#);
    let file_plain = regex!(r"^FILE\s+(\S+)\s+\S+");
    let track = regex!(r"^TRACK\s+([0-9]+)\s+(\S+)");
    let index = regex!(r"^(INDEX\s+([0-9]+)|PREGAP)\s+([0-9]+):([0-9]+):([0-9]+)");
    let mut files: CueFiles = vec![];
    for line in split_lines(text) {
        let words = line.trim();
        if let Some(m) = file_quoted.captures(words).or_else(|| file_plain.captures(words)) {
            files.push((m[1].to_string(), vec![]));
        } else if let Some(m) = track.captures(words) {
            let Some(last) = files.last_mut() else { return Err("TRACK before FILE".into()) };
            last.1.push(Track { number: number(&m[1])?, kind: m[2].to_uppercase(), index: BTreeMap::new(), pregap: 0 });
        } else if let Some(m) = index.captures(words) {
            let Some(t) = files.last_mut().and_then(|f| f.1.last_mut()) else {
                return Err(format!("{} before TRACK", m[1].split_whitespace().next().unwrap_or("")));
            };
            let frames = (number(&m[3])? * 60 + number(&m[4])?) * 75 + number(&m[5])?;
            match m.get(2) {
                Some(n) => { t.index.insert(number(n.as_str())?, frames); }
                None => t.pregap = frames,
            }
        }
    }
    Ok(files)
}

fn parse_cue(path: &Path) -> Result<CueFiles, String> {
    let data = fs::read(path).map_err(|e| e.to_string())?;
    let data = data.strip_prefix(b"\xef\xbb\xbf").unwrap_or(&data);
    let files = parse_cue_text(&String::from_utf8_lossy(data))?;
    let names: Vec<String> = files.iter().map(|f| f.0.clone()).collect();
    Ok(files.into_iter().map(|(name, tracks)| (resolve_file(path, &name, &names), tracks)).collect())
}

fn track_file() -> &'static Regex { regex!(r"(?i)\(Track (\d+)\)") }

fn track_number(name: &str) -> Option<u64> { track_file().captures(name)?[1].parse().ok() }

/// The file in cue's folder that FILE name means: name itself when it is
/// there, else the one file that matches it ignoring case (or its folders),
/// else for a one-file cue "<cue name>.bin" (renamed along with its cue, as
/// the core's cue reader also takes), else the one .bin with the same
/// "(Track n)" when the cue is the only one in its folder and its .bin files
/// are exactly the cue's tracks, one per "(Track n)". Without any of these,
/// name (reported as missing).
fn resolve_file(cue: &Path, name: &str, names: &[String]) -> String {
    let folder = cue.parent().unwrap_or(Path::new(""));
    if is_file(&join(folder, &name.replace('\\', "/"))) {
        return name.to_string();
    }
    let mut files: Vec<String> = fs::read_dir(folder)
        .into_iter()
        .flatten()
        .flatten()
        .filter(|e| e.file_type().map_or(false, |t| t.is_file()))
        .map(|e| e.file_name().to_string_lossy().into_owned())
        .filter(|n| !n.starts_with("._"))
        .collect();
    files.sort();
    let one = |test: &dyn Fn(&str) -> bool| {
        let found: Vec<&String> = files.iter().filter(|f| test(f)).collect();
        (found.len() == 1).then(|| found[0].clone())
    };
    let base = name.rsplit(['/', '\\']).next().unwrap_or(name);
    let lower = base.to_lowercase();
    let renamed = format!("{}.bin", stem(&name_of(cue)).to_lowercase());
    let track = track_number(base);
    let bins: Vec<&String> = files.iter().filter(|f| has_ext(f, ".bin") && !f.ends_with(" (one file).bin")).collect();
    let mut numbers: Vec<Option<u64>> = names.iter().map(|n| track_number(n.rsplit(['/', '\\']).next().unwrap_or(n))).collect();
    let mut bin_numbers: Vec<Option<u64>> = bins.iter().map(|f| track_number(f)).collect();
    numbers.sort();
    bin_numbers.sort();
    let by_track = files.iter().filter(|f| has_ext(f, ".cue")).count() == 1
        && !numbers.contains(&None)
        && numbers.windows(2).all(|w| w[0] != w[1])
        && numbers == bin_numbers;
    one(&|f| f == base)
        .or_else(|| one(&|f| f.to_lowercase() == lower))
        .or_else(|| if names.len() == 1 { one(&|f| f.to_lowercase() == renamed) } else { None })
        .or_else(|| if track.is_some() && by_track { one(&|f| has_ext(f, ".bin") && track_number(f) == track) } else { None })
        .unwrap_or_else(|| name.to_string())
}

fn raw_track(kind: &str) -> bool {
    kind == "AUDIO" || kind.ends_with("/2352") || kind.ends_with("/RAW")
}

/// Reason the core cannot play this layout (per-track cores), or None.
fn check(files: &CueFiles, folder: &Path) -> Option<String> {
    if files.is_empty() {
        return Some("no FILE entries".into());
    }
    for (n, (name, tracks)) in files.iter().enumerate() {
        if tracks.len() != 1 {
            return Some(format!("{name}: {} tracks in one file (merged image)", tracks.len()));
        }
        let t = &tracks[0];
        let Some(bytes) = size(&join(folder, name)) else { return Some(format!("{name}: file missing")) };
        if bytes % SECTOR != 0 {
            return Some(format!("{name}: not whole 2352-byte sectors"));
        }
        if n == 0 {
            if t.kind == "AUDIO" || t.index.get(&1) != Some(&0) {
                return Some("track 1 must be data with INDEX 01 at 00:00:00".into());
            }
        } else if t.kind != "AUDIO" || t.index.get(&0).copied().unwrap_or(0) != 0 || t.index.get(&1) != Some(&150) {
            return Some(format!("track {}: expected AUDIO with INDEX 00 00:00:00 and INDEX 01 00:02:00", t.number));
        }
    }
    None
}

/// Write cue's single merged image as one file per track in the layout
/// check() accepts: track 1 data from INDEX 01 00:00:00, every later track
/// starting with its 150-sector pregap (zeros when the cue's PREGAP has no
/// data in the image). With move_single, a one-track image is moved, not
/// copied. Returns the new cue.
fn split_cue(cue: &Path, dest: &Path, move_single: bool) -> Result<PathBuf, String> {
    let files = parse_cue(cue)?;
    if files.len() != 1 {
        return Err(format!("{} FILE entries (expected one merged image)", files.len()));
    }
    let (name, tracks) = &files[0];
    let source = join(cue.parent().unwrap(), name);
    let Some(bytes) = size(&source) else { return Err(format!("{name}: file missing")) };
    if bytes % SECTOR != 0 {
        return Err(format!("{name}: not whole 2352-byte sectors"));
    }
    let sectors = bytes / SECTOR;
    if tracks.is_empty() || tracks[0].kind == "AUDIO" || tracks[0].index.get(&1) != Some(&0) || tracks[0].pregap != 0 {
        return Err("track 1 must be data with INDEX 01 at 00:00:00".into());
    }
    // (number, kind, start, zeros, end)
    let mut plan: Vec<(u64, String, u64, u64, u64)> = vec![];
    for (k, t) in tracks.iter().enumerate() {
        if !raw_track(&t.kind) {
            return Err(format!("track {}: {} is not 2352-byte sectors", t.number, t.kind));
        }
        let Some(&index1) = t.index.get(&1) else { return Err(format!("track {}: no INDEX 01", t.number)) };
        let start = if k > 0 { t.index.get(&0).copied().unwrap_or(index1) } else { 0 };
        if k > 0 {
            let stored = index1 as i64 - start as i64;
            if stored < 0 || stored as u64 + t.pregap != PREGAP {
                return Err(format!("track {}: pregap is not 00:02:00", t.number));
            }
            if t.kind != "AUDIO" {
                return Err(format!("track {}: data after track 1", t.number));
            }
        }
        let zeros = if k > 0 { PREGAP - (index1 - start) } else { 0 };
        plan.push((t.number, t.kind.clone(), start, zeros, 0));
    }
    for k in 0..plan.len() {
        let end = if k + 1 < plan.len() { plan[k + 1].2 } else { sectors };
        if !(plan[k].2 < end && end <= sectors) {
            return Err(format!("track {}: indexes out of order or past the image", plan[k].0));
        }
        plan[k].4 = end;
    }
    let cue_name = name_of(cue);
    let base = stem(&cue_name);
    let first = plan[0].0;
    let mut lines = vec![];
    for (number, kind, start, zeros, end) in &plan {
        let track = if plan.len() > 1 { format!("{base} (Track {number:02}).bin") } else { format!("{base}.bin") };
        if plan.len() == 1 && move_single {
            fs::create_dir_all(dest).and_then(|_| fs::rename(&source, dest.join(&track))).map_err(|e| e.to_string())?;
        } else {
            write_parts(&dest.join(&track), Some((zeros + end - start) * SECTOR), &mut |out| {
                write_zeros(out, zeros * SECTOR)?;
                copy_range(&source, start * SECTOR, (end - start) * SECTOR, out)
            })
            .map_err(convert::reason)?;
        }
        lines.push(format!("FILE \"{track}\" BINARY"));
        lines.push(format!("  TRACK {number:02} {kind}"));
        if *number != first {
            lines.push("    INDEX 00 00:00:00".into());
            lines.push("    INDEX 01 00:02:00".into());
        } else {
            lines.push("    INDEX 01 00:00:00".into());
        }
    }
    let out = dest.join(format!("{base}.cue"));
    write_bytes(&out, (lines.join("\n") + "\n").as_bytes()).map_err(|e| e.to_string())?;
    Ok(out)
}

// ---- one-file discs (cores packaged with --one-file-disc) --------------------

/// (sources, tracks) for a disc played as one image (its files back to back):
/// sources [(path, sectors)], tracks [(start, index01)] in sectors from the
/// start of the image (start is INDEX 00, or INDEX 01 without one).
fn one_file_plan(files: &CueFiles, folder: &Path) -> Result<(Vec<(PathBuf, u64)>, Vec<(u64, u64)>), String> {
    if files.is_empty() {
        return Err("no FILE entries".into());
    }
    let (mut sources, mut tracks, mut offset) = (vec![], vec![], 0u64);
    for (name, entries) in files {
        let path = join(folder, name);
        let Some(bytes) = size(&path) else { return Err(format!("{name}: file missing")) };
        if bytes % SECTOR != 0 || bytes == 0 {
            return Err(format!("{name}: not whole 2352-byte sectors"));
        }
        for t in entries {
            if !raw_track(&t.kind) {
                return Err(format!("track {}: {} is not 2352-byte sectors", t.number, t.kind));
            }
            let Some(&index1) = t.index.get(&1) else { return Err(format!("track {}: no INDEX 01", t.number)) };
            if t.pregap != 0 {
                return Err(format!("track {}: PREGAP outside the image", t.number));
            }
            if !tracks.is_empty() && t.kind != "AUDIO" {
                return Err(format!("track {}: data after track 1", t.number));
            }
            tracks.push((offset + t.index.get(&0).copied().unwrap_or(index1), offset + index1));
        }
        sources.push((path, bytes / SECTOR));
        offset += bytes / SECTOR;
    }
    let first_audio = files[0].1.first().map_or(true, |t| t.kind == "AUDIO");
    if tracks.is_empty() || first_audio || tracks[0] != (0, 0) {
        return Err("track 1 must be data with INDEX 01 at 00:00:00".into());
    }
    if tracks.len() > 99 {
        return Err(format!("{} tracks (a CD has at most 99)", tracks.len()));
    }
    let ordered = tracks.windows(2).all(|w| w[0].1 < w[1].0 && w[0].0 <= w[0].1);
    if !ordered || !(tracks[tracks.len() - 1].1 < offset) {
        return Err("indexes out of order or past the image".into());
    }
    if offset + PREGAP > 524288 {
        return Err("longer than the core's 19-bit LBAs".into());
    }
    Ok((sources, tracks))
}

fn bcd(value: u64) -> u64 { (value / 10) * 16 + value % 10 }

// The 16 LibCrypt sector addresses (absolute BCD MM:SS:FF) behind mask bits
// 15..0, as Main_MiSTer support/psx/psx.cpp libCryptMask and psx_cd_stream.
const LIBCRYPT_MSF: [u32; 16] = [
    0x030805, 0x030956, 0x031310, 0x031429, 0x031524, 0x031849, 0x032056, 0x032155,
    0x032317, 0x032412, 0x032503, 0x032828, 0x033219, 0x033356, 0x033451, 0x033542,
];

/// 16-bit mask from a redump .sbi: 4-byte header, 14-byte entries.
fn libcrypt_mask(sbi: &Path) -> u64 {
    let data = fs::read(sbi).unwrap_or_default();
    let mut mask = 0;
    let mut offset = 4;
    while offset + 2 < data.len() {
        let msf = (data[offset] as u32) << 16 | (data[offset + 1] as u32) << 8 | data[offset + 2] as u32;
        if let Some(k) = LIBCRYPT_MSF.iter().position(|&m| m == msf) {
            mask |= 1 << (15 - k);
        }
        offset += 14;
    }
    mask
}

/// The core's 400 track-info words (scripts/psx_disc_host.py layout), as bytes.
fn track_table(tracks: &[(u64, u64)], sectors: u64, mask: u64) -> Vec<u8> {
    let region = 2u64;
    let msf = |lba: u64| {
        let (minutes, seconds) = (lba / 75 / 60, lba / 75 % 60);
        (bcd(minutes) << 8) | bcd(seconds)
    };
    let mut words = [0u64; 400];
    let n = tracks.len() as u64;
    words[0] = (bcd(n) << 8) | n;
    words[1] = sectors + PREGAP;
    words[2] = msf(sectors + PREGAP);
    words[3] = (region << 16) | mask;
    for (k, &(start, index01)) in tracks.iter().enumerate() {
        let last = if k + 1 < tracks.len() { tracks[k + 1].0 } else { sectors } + PREGAP - 1;
        words[4 * k + 4] = if k == 0 { 0 } else { start + PREGAP };
        words[4 * k + 5] = last;
        words[4 * k + 6] = (((k != 0) as u64) << 16) | msf(index01 + PREGAP);
    }
    words.iter().flat_map(|&w| (w as u32).to_le_bytes()).collect()
}

/// Write the disc's table next to its one-file image; returns (image,
/// table, track count) or a reason. A disc in several files plays from its
/// joined "<cue name> (one file).bin" (convert.rs makes it).
fn write_one_file(cue: &Path, files: &CueFiles, sbi: Option<&Path>) -> Result<(PathBuf, PathBuf, usize), String> {
    let folder = cue.parent().unwrap();
    let (sources, tracks) = one_file_plan(files, folder)?;
    let total: u64 = sources.iter().map(|s| s.1).sum();
    let image = if sources.len() == 1 {
        sources[0].0.clone()
    } else {
        let image = folder.join(format!("{} (one file).bin", stem(&name_of(cue))));
        if size(&image) != Some(total * SECTOR) {
            return Err(format!("{} files; the core needs the disc as one .bin (not converted)", sources.len()));
        }
        image
    };
    let table = image.with_file_name(format!("{}.trk", stem(&name_of(&image))));
    let data = track_table(&tracks, total, sbi.map_or(0, libcrypt_mask));
    if fs::read(&table).ok().as_deref() != Some(&data[..]) {
        write_bytes(&table, &data).map_err(|e| e.to_string())?;
    }
    Ok((image, table, tracks.len()))
}

// ---- .bin files without a cue -------------------------------------------------

/// The cue written for a .bin that came without one: one data track.
fn lone_bin_cue(bin_name: &str) -> String {
    format!("FILE \"{bin_name}\" BINARY\n  TRACK 01 MODE2/2352\n    INDEX 01 00:00:00\n")
}

/// .bin files under common that no cue in their folder names and that have
/// no <name>.cue, with the reason one is skipped: a "(Track n)" file is part
/// of a disc whose cue is missing.
fn lone_bins(common: &Path) -> Vec<(PathBuf, Option<&'static str>)> {
    let mut found = vec![];
    for bin in walk(common, &|n| has_ext(n, ".bin")) {
        let folder = bin.parent().unwrap();
        let name = name_of(&bin);
        if name.ends_with(" (one file).bin") || is_file(&folder.join(format!("{}.cue", stem(&name)))) {
            continue;
        }
        let (mut named, mut broken) = (vec![], false);
        for entry in fs::read_dir(folder).into_iter().flatten().flatten() {
            let cue = entry.path();
            let cue_name = name_of(&cue);
            if !has_ext(&cue_name, ".cue") || cue_name.starts_with("._") || !is_file(&cue) {
                continue;
            }
            match parse_cue(&cue) {
                Ok(files) => named.extend(files.iter().map(|(f, _)| join(folder, &f.replace('\\', "/")))),
                Err(_) => broken = true,
            }
        }
        if broken || named.contains(&bin) {
            continue;
        }
        let reason = if regex!(r"(?i)\(Track \d+\)").is_match(stem(&name)) {
            Some("a track file whose .cue is missing")
        } else if size(&bin).unwrap_or(0) % SECTOR != 0 {
            Some("not whole 2352-byte sectors")
        } else {
            None
        };
        found.push((bin, reason));
    }
    found
}

/// Give each lone .bin a one-track <name>.cue so it gets a game file.
/// Returns how many were skipped. The write keeps RESERVE free.
fn write_lone_cues(common: &Path, log: &mut dyn FnMut(&str)) -> usize {
    let mut skipped = 0;
    for (bin, reason) in lone_bins(common) {
        let shown = rel(&bin, common);
        if let Some(reason) = reason {
            log(&format!("skip {shown}: {reason}"));
            skipped += 1;
            continue;
        }
        let name = name_of(&bin);
        let cue = bin.with_file_name(format!("{}.cue", stem(&name)));
        let text = lone_bin_cue(&name);
        match write_parts(&cue, Some(text.len() as u64), &mut |out| out.write_all(text.as_bytes())) {
            Ok(()) => log(&format!("wrote {} (one data track; a game with CD music needs its own .cue)", rel(&cue, common))),
            Err(e) if is_no_space(&e) => {
                log(&format!("skip {shown}: not enough free space for its .cue"));
                skipped += 1;
            }
            Err(e) => {
                log(&format!("skip {shown}: {e}"));
                skipped += 1;
            }
        }
    }
    skipped
}

// ---- CHD images ------------------------------------------------------------------

/// Report .chd files under common that have no extracted copy next to them
/// (<name>/<name>.cue, as convert.rs and make_pocket_instances.py --convert
/// make it).
fn report_chds(common: &Path, log: &mut dyn FnMut(&str)) -> usize {
    let mut skipped = 0;
    for image in walk(common, &|n| has_ext(n, ".chd")) {
        let folder = image.parent().unwrap();
        let image_name = name_of(&image);
        let base = stem(&image_name);
        if name_of(folder) == base || is_file(&folder.join(base).join(format!("{base}.cue"))) {
            continue;
        }
        log(&format!("skip {}: CHD image not extracted", rel(&image, common)));
        skipped += 1;
    }
    skipped
}

// ---- game files ---------------------------------------------------------------

/// 'Game (USA) (Disc 2)' -> 'Game (USA).sav'; None for single-disc games.
fn shared_card_name(stem: &str) -> Option<String> {
    disc().is_match(stem).then(|| disc().replacen(stem, 1, "").into_owned() + ".sav")
}

/// The disc's .sbi: <cue name>.sbi, else the only .sbi in its folder.
fn find_sbi(cue: &Path) -> Option<String> {
    let folder = cue.parent()?;
    let same = format!("{}.sbi", stem(&name_of(cue)));
    if is_file(&folder.join(&same)) {
        return Some(same);
    }
    let mut found: Vec<String> = fs::read_dir(folder)
        .ok()?
        .flatten()
        .filter(|e| e.file_type().map_or(false, |t| t.is_file()))
        .map(|e| e.file_name().to_string_lossy().into_owned())
        .filter(|n| n.ends_with(".sbi"))
        .collect();
    found.sort();
    if found.len() == 1 { found.pop() } else { None }
}

/// The game's cheat list: <cue name>.cht, <cue file name>.cht, or the name
/// without its "(Disc n)" tag with .cht.
fn find_cht(cue: &Path) -> Option<String> {
    let folder = cue.parent()?;
    let name = name_of(cue);
    let s = stem(&name);
    [format!("{s}.cht"), format!("{name}.cht"), format!("{}.cht", disc_n().replace_all(s, "").trim())]
        .into_iter()
        .find(|c| is_file(&folder.join(c)))
}

/// Start Saves/<platform>/common/<name> from the newest per-disc card. Never overwrites.
fn seed_shared_card(card: &Path, platform: &str, core: &str, stems: &[String], name: &str) -> Option<String> {
    let shared = card.join("Saves").join(platform).join("common").join(name);
    if shared.exists() {
        return None;
    }
    let mut newest: Option<(String, std::time::SystemTime)> = None;
    for s in stems {
        let file = card.join("Saves").join(platform).join(core).join(format!("{s}.sav"));
        let Ok(meta) = fs::metadata(&file) else { continue };
        if !meta.is_file() {
            continue;
        }
        let modified = meta.modified().unwrap_or(std::time::UNIX_EPOCH);
        if newest.as_ref().map_or(true, |n| modified > n.1) {
            newest = Some((format!("{s}.sav"), modified));
        }
    }
    let (newest, _) = newest?;
    let data = fs::read(card.join("Saves").join(platform).join(core).join(&newest)).ok()?;
    write_bytes(&shared, &data).ok()?;
    Some(newest)
}

/// Python's json.dumps(value, indent=2) for a string: non-ASCII as \uXXXX.
fn json_string(text: &str) -> String {
    let mut out = String::from("\"");
    for c in text.chars() {
        match c {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            '\x08' => out.push_str("\\b"),
            '\x0c' => out.push_str("\\f"),
            c if (c as u32) < 0x20 || (c as u32) > 0x7e => {
                let mut units = [0u16; 2];
                for unit in c.encode_utf16(&mut units) {
                    out.push_str(&format!("\\u{unit:04x}"));
                }
            }
            c => out.push(c),
        }
    }
    out.push('"');
    out
}

fn instance_json(data_path: &str, slots: &[(i64, String)]) -> String {
    let slots: Vec<String> = slots
        .iter()
        .map(|(id, filename)| format!("      {{\n        \"id\": {id},\n        \"filename\": {}\n      }}", json_string(filename)))
        .collect();
    format!(
        "{{\n  \"instance\": {{\n    \"magic\": \"APF_VER_1\",\n    \"data_path\": {},\n    \"data_slots\": [\n{}\n    ]\n  }}\n}}\n",
        json_string(data_path),
        slots.join(",\n")
    )
}

/// A PlayStation core on the card.
pub struct Core {
    pub id: String,
    pub platform: String,
}

fn read_json(path: &Path) -> Option<serde_json::Value> {
    serde_json::from_slice(&fs::read(path).ok()?).ok()
}

/// Cores on the card that run PlayStation games, sorted by folder name.
pub fn find_cores(card: &Path) -> Vec<Core> {
    let mut found = vec![];
    let Ok(entries) = fs::read_dir(card.join("Cores")) else { return found };
    for entry in entries.flatten() {
        if !entry.file_type().map_or(false, |t| t.is_dir()) {
            continue;
        }
        let id = entry.file_name().to_string_lossy().into_owned();
        let platform = read_json(&entry.path().join("core.json"))
            .and_then(|v| v.get("core")?.get("metadata")?.get("platform_ids")?.get(0)?.as_str().map(String::from));
        if let Some(platform) = platform.filter(|p| p == "playstation" || p == "psx") {
            found.push(Core { id, platform });
        }
    }
    found.sort_by(|a, b| a.id.cmp(&b.id));
    found
}

/// Slots the core declares in its data.json, or None.
/// The core's disc menu as Core Settings names it ("Swap Discs" from 0.5.3,
/// "Disc" before): the list setting on bridge address 0x50000044.
pub fn disc_menu_name(card: &Path, core: &str) -> String {
    let named = read_json(&card.join("Cores").join(core).join("interact.json")).and_then(|j| {
        j.get("interact")?.get("variables")?.as_array()?.iter().find_map(|v| {
            let address = v.get("address")?.as_str()?.to_ascii_lowercase();
            (address == "0x50000044").then(|| v.get("name")?.as_str().map(str::to_string)).flatten()
        })
    });
    named.unwrap_or_else(|| "Swap Discs".to_string())
}

fn declared_slots(card: &Path, core: &str) -> Option<Vec<(Option<i64>, Vec<String>)>> {
    let data = read_json(&card.join("Cores").join(core).join("data.json"))?;
    let slots = data.get("data")?.get("data_slots")?.as_array()?;
    Some(
        slots
            .iter()
            .map(|s| {
                let id = s.get("id").and_then(|v| v.as_i64());
                let ext = s
                    .get("extensions")
                    .and_then(|v| v.as_array())
                    .map(|a| a.iter().filter_map(|e| e.as_str().map(String::from)).collect())
                    .unwrap_or_default();
                (id, ext)
            })
            .collect(),
    )
}

/// Whether the core reads cues itself (--cue-mount: its launch slot takes
/// .cue files). Its game files name the cue and disc 1's files.
pub fn is_cue_reader(card: &Path, core: &str) -> bool {
    declared_slots(card, core).map_or(false, |d| d.iter().any(|(id, ext)| *id == Some(0) && ext.iter().any(|e| e == "cue")))
}

/// Whether the core plays one .bin plus a .trk table per disc (--one-file-disc
/// without the cue reader).
pub fn is_one_file(card: &Path, core: &str) -> bool {
    !is_cue_reader(card, core)
        && declared_slots(card, core).map_or(false, |d| d.iter().any(|(id, ext)| *id == Some(102) && ext.iter().any(|e| e == "trk")))
}

/// Reason a cue-reader core cannot open this cue's files, or None.
fn cue_check(files: &CueFiles, folder: &Path) -> Option<String> {
    if files.is_empty() {
        return Some("no FILE entries".into());
    }
    for (name, _) in files {
        match size(&join(folder, &name.replace('\\', "/"))) {
            None => return Some(format!("{name}: file missing")),
            Some(bytes) if bytes % SECTOR != 0 => return Some(format!("{name}: not whole 2352-byte sectors")),
            _ => {}
        }
    }
    None
}

/// Whether every disc's cue is disc 1's path with "(Disc 1)" renumbered, as
/// the core's cue reader finds them.
fn sibling_paths(cue1: &Path, others: &[(u64, &Path)]) -> bool {
    let first = segments(cue1).join("/");
    others.iter().all(|(n, cue)| first.replace("(Disc 1)", &format!("(Disc {n})")) == segments(cue).join("/"))
}

/// What a game file needs to know about the core, and the shared cards so far.
struct Writer<'a> {
    out: &'a Path,
    common: &'a Path,
    shared_cards: bool,
    libcrypt: bool,
    cheats: bool,
    one_file: bool,
    cue_reader: bool,
    discs: BTreeMap<String, Vec<String>>, // shared card -> disc stems
}

impl Writer<'_> {
    fn write_instance(&self, name: &str, data_path: &str, slots: &[(i64, String)]) -> Result<(), String> {
        write_bytes(&self.out.join(format!("{name}.json")), instance_json(data_path, slots).as_bytes()).map_err(|e| format!("{name}.json: {e}"))
    }

    /// Write cue's own game file (card: the shared card's name, when its game's).
    fn single(&mut self, cue: &Path, files: &CueFiles, track_count: usize, card: Option<&str>, log: &mut dyn FnMut(&str)) -> Result<(), String> {
        let folder = cue.parent().unwrap();
        let cue_name = name_of(cue);
        let cue_stem = stem(&cue_name).to_string();
        let data_path = rel(folder, self.common);
        let mut slots = vec![(100, cue_name.clone())];
        slots.extend(files.iter().enumerate().map(|(k, (n, _))| (101 + k as i64, n.clone())));
        let sbi = if self.cue_reader {
            let same = format!("{cue_stem}.sbi");
            is_file(&folder.join(&same)).then_some(same)
        } else if self.libcrypt || self.one_file {
            find_sbi(cue)
        } else {
            None
        };
        // Cue-reader cores read a cue's .sbi by its path, which a game JSON's
        // cue doesn't have on the Pocket: they take it from slot 99 instead.
        if let (Some(sbi), true) = (&sbi, if self.cue_reader { self.libcrypt } else { !self.one_file }) {
            slots.push((LIBCRYPT_SLOT, sbi.clone()));
        }
        let cht = if self.cheats { find_cht(cue) } else { None };
        if let Some(cht) = &cht {
            slots.push((CHEAT_SLOT, cht.clone()));
        }
        let mut prefix = if data_path != "." { format!("{data_path}/") } else { String::new() };
        if let Some(shared) = self.shared_cards.then(|| card.map(String::from).or_else(|| shared_card_name(&cue_stem))).flatten() {
            for s in &mut slots {
                s.1 = format!("{prefix}{}", s.1);
            }
            slots.push((CARD_SLOT, shared.clone()));
            self.discs.entry(shared).or_default().push(cue_stem.clone());
            prefix = String::new();
        }
        self.write_instance(&cue_stem, &prefix, &slots)?;
        let count = if self.one_file || self.cue_reader { track_count } else { files.len() };
        log(&format!(
            "wrote {cue_stem}.json ({count} track{}{}{}{})",
            if count > 1 { "s" } else { "" },
            if self.one_file { ", one file" } else if self.cue_reader { ", cue" } else { "" },
            sbi.map(|s| format!(", LibCrypt {s}")).unwrap_or_default(),
            cht.map(|s| format!(", cheats {s}")).unwrap_or_default()
        ));
        Ok(())
    }
}

pub struct Summary {
    pub written: usize,
    pub shared: usize,
}

type Part = (PathBuf, CueFiles, Vec<String>, usize);

/// Write Assets/<platform>/<core>/<game>.json for every playable disc under
/// Assets/<platform>/common. log(line) gets the report.
pub fn make_instances(card: &Path, core: &str, platform: &str, log: &mut dyn FnMut(&str)) -> Result<Summary, String> {
    let declared = declared_slots(card, core);
    let ids: Vec<i64> = declared.iter().flatten().filter_map(|s| s.0).collect();
    let max_tracks = if declared.is_none() { 8 } else { ids.iter().filter(|&&id| (101..=199).contains(&id)).count() };
    let shared_cards = declared.is_none() || ids.contains(&CARD_SLOT);
    let libcrypt = declared.is_some() && ids.contains(&LIBCRYPT_SLOT);
    let cheats = declared.is_some() && ids.contains(&CHEAT_SLOT);
    let one_file = is_one_file(card, core);
    let cue_reader = is_cue_reader(card, core);
    // Cue-reader cores read .m3u playlists (up to four discs); slot 100 taking
    // them means a game file can name one.
    let playlist_slot = declared.iter().flatten().any(|(id, ext)| *id == Some(100) && ext.iter().any(|e| e == "m3u"));
    let common = card.join("Assets").join(platform).join("common");
    let out = card.join("Assets").join(platform).join(core);
    if !common.is_dir() {
        return Err(format!("no Assets/{platform}/common folder on this card"));
    }
    let mut disc_tracks = HashMap::new();
    for n in 2..10i64 {
        disc_tracks.insert(n, ids.iter().filter(|&&id| (100 * n + 1..=100 * n + 99).contains(&id)).count());
    }
    let discs_declared = 1 + (2..10).filter(|n| disc_tracks[n] > 0).count();
    let (mut written, mut skipped) = (0, report_chds(&common, log));
    skipped += write_lone_cues(&common, log);
    let mut writer = Writer { out: &out, common: &common, shared_cards, libcrypt, cheats, one_file, cue_reader, discs: BTreeMap::new() };
    let mut games: BTreeMap<String, BTreeMap<u64, Vec<Part>>> = BTreeMap::new();
    for cue in walk(&common, &|n| has_ext(n, ".cue")) {
        let folder = cue.parent().unwrap().to_path_buf();
        let cue_name = name_of(&cue);
        let cue_stem = stem(&cue_name).to_string();
        let (mut files, mut reason) = match parse_cue(&cue) {
            Ok(files) => {
                let reason = if cue_reader {
                    cue_check(&files, &folder)
                } else if one_file {
                    None
                } else {
                    check(&files, &folder)
                };
                (files, reason)
            }
            Err(e) => (vec![], Some(e)),
        };
        if (one_file || cue_reader) && name_of(&folder) == cue_stem && is_file(&folder.parent().unwrap().join(&cue_name)) {
            continue; // split copy of a merged cue
        }
        let mut track_count = 0;
        if reason.is_none() && one_file {
            let sbi = find_sbi(&cue).map(|s| folder.join(s));
            match write_one_file(&cue, &files, sbi.as_deref()) {
                Ok((image, table, n)) => {
                    files = vec![(rel(&image, &folder), vec![]), (rel(&table, &folder), vec![])];
                    track_count = n;
                }
                Err(e) => reason = Some(e),
            }
        }
        if reason.is_none() && cue_reader {
            // disc 1 boots from its slots, the rest by path
            track_count = files.iter().map(|f| f.1.len()).sum();
            if files.len() > CUE_FILE_SLOTS.min(max_tracks) {
                // only slots the core declares (101-104 from 0.5.3)
                files = vec![];
            }
        }
        if reason.is_none() && !one_file && !cue_reader && files.len() > max_tracks {
            reason = Some(format!("{} track files (core takes {max_tracks})", files.len()));
        }
        if reason.is_some() && is_file(&folder.join(&cue_stem).join(format!("{cue_stem}.cue"))) {
            continue; // converted copy is listed
        }
        if let Some(reason) = reason {
            log(&format!("skip {}: {reason}", rel(&cue, &common)));
            skipped += 1;
            continue;
        }
        if let Some(m) = disc_n().captures(&cue_stem) {
            if discs_declared > 1 {
                let name = disc_n().replace_all(&cue_stem, "").into_owned();
                let game = release().replace_all(&name, "").trim().to_string();
                let tags = release().find_iter(&name).map(|m| m.as_str().to_string()).collect();
                let n = number(&m[1])?;
                games.entry(game).or_default().entry(n).or_default().push((cue.clone(), files, tags, track_count));
                continue;
            }
        }
        writer.single(&cue, &files, track_count, None, log)?;
        written += 1;
    }
    for (game, found) in &games {
        let first_tags = found.get(&1).map(|c| c[0].2.clone()).unwrap_or_default();
        // Two releases of the same disc: keep the one whose release tags
        // match disc 1's, else the first.
        let parts: BTreeMap<u64, &Part> =
            found.iter().map(|(&n, cands)| (n, cands.iter().find(|c| c.2 == first_tags).unwrap_or(&cands[0]))).collect();
        let numbers: Vec<u64> = parts.keys().copied().collect();
        if numbers.iter().enumerate().any(|(i, &n)| n != i as u64 + 1) || numbers.len() > discs_declared {
            let shown: Vec<String> = numbers.iter().map(|n| n.to_string()).collect();
            log(&format!("skip {game}: discs [{}] (core takes discs 1-{discs_declared})", shown.join(", ")));
            skipped += 1;
            continue;
        }
        if cue_reader {
            let others: Vec<(u64, &Path)> = numbers[1..].iter().map(|n| (*n, parts[n].0.as_path())).collect();
            if !sibling_paths(&parts[&1].0, &others) {
                // The core can't find the other discs by name: a playlist lists them.
                let stem1 = stem(&name_of(&parts[&1].0)).to_string();
                let name = disc_n().replace_all(&stem1, "").trim().to_string();
                let folders: Vec<Vec<String>> = numbers.iter().map(|n| segments(parts[n].0.parent().unwrap())).collect();
                let mut depth = 0;
                while folders.iter().all(|f| depth < f.len() && f[depth] == folders[0][depth]) {
                    depth += 1;
                }
                let root: PathBuf = folders[0][..depth].iter().collect();
                let m3u = root.join(format!("{name}.m3u"));
                let shown = rel(&m3u, &common);
                let card = shared_card_name(&stem1);
                if numbers.len() <= 4 {
                    let text: String = numbers.iter().map(|n| rel(&parts[n].0, &root) + "\n").collect();
                    write_bytes(&m3u, text.as_bytes()).map_err(|e| format!("{shown}: {e}"))?;
                }
                if numbers.len() <= 4 && playlist_slot {
                    let data_path = rel(&root, &common);
                    let prefix = if data_path != "." { format!("{data_path}/") } else { String::new() };
                    let shared = card.clone().filter(|_| shared_cards);
                    let (cue1, files1, _, _) = parts[&1];
                    let mut slots = vec![(100, format!("{name}.m3u"))];
                    slots.extend(
                        files1.iter().enumerate().map(|(k, (f, _))| (101 + k as i64, rel(&join(cue1.parent().unwrap(), f), &root))),
                    );
                    if let Some(shared) = &shared {
                        for s in &mut slots {
                            s.1 = format!("{prefix}{}", s.1);
                        }
                        slots.push((CARD_SLOT, shared.clone()));
                        writer.discs.entry(shared.clone()).or_default().extend(numbers.iter().map(|n| stem(&name_of(&parts[n].0)).to_string()));
                    }
                    writer.write_instance(&name, if shared.is_some() { "" } else { &prefix }, &slots)?;
                    for n in &numbers {
                        let s = stem(&name_of(&parts[n].0)).to_string();
                        if s != name {
                            let _ = fs::remove_file(out.join(format!("{s}.json")));
                        }
                    }
                    log(&format!("wrote {name}.json ({} discs, playlist {shown})", numbers.len()));
                    written += 1;
                    continue;
                }
                if numbers.len() <= 4 {
                    log(&format!(
                        "{game}: the discs' names differ by more than their number: wrote the playlist {shown} (pick it in the core's \
                         file list to swap discs); each disc also gets its own game file"
                    ));
                } else {
                    log(&format!("{game}: the discs' names differ by more than their number, so each disc gets its own game file"));
                }
                for n in &numbers {
                    let (cue, files, _, count) = parts[n];
                    writer.single(cue, files, *count, card.as_deref(), log)?;
                    written += 1;
                }
                continue;
            }
        }
        let too_long = if one_file || cue_reader { None } else { numbers[1..].iter().find(|&&n| parts[&n].1.len() > disc_tracks[&(n as i64)]) };
        if let Some(n) = too_long {
            log(&format!("skip {game}: disc {n} has more track files than the core declares"));
            skipped += 1;
            continue;
        }
        // The folder all discs sit under.
        let folders: Vec<Vec<String>> = numbers.iter().map(|n| segments(parts[n].0.parent().unwrap())).collect();
        let mut depth = 0;
        while folders.iter().all(|f| depth < f.len() && f[depth] == folders[0][depth]) {
            depth += 1;
        }
        let root: PathBuf = folders[0][..depth].iter().collect();
        let in_root = |cue: &Path, name: &str| rel(&join(cue.parent().unwrap(), name), &root);
        let cue1 = &parts[&1].0;
        let cue1_name = name_of(cue1);
        let cue1_stem = stem(&cue1_name).to_string();
        let mut slots = vec![(100, in_root(cue1, &cue1_name))];
        // the cue reader finds the other discs by path
        for n in &numbers[..if cue_reader { 1 } else { numbers.len() }] {
            let (cue, files, _, _) = parts[n];
            slots.extend(files.iter().enumerate().map(|(k, (name, _))| (100 * *n as i64 + 1 + k as i64, in_root(cue, name))));
        }
        let sbi = if !libcrypt {
            None
        } else if cue_reader {
            let same = format!("{cue1_stem}.sbi");
            is_file(&cue1.parent().unwrap().join(&same)).then_some(same)
        } else {
            find_sbi(cue1)
        };
        if let Some(sbi) = sbi {
            slots.push((LIBCRYPT_SLOT, in_root(cue1, &sbi)));
        }
        if let Some(cht) = if cheats { find_cht(cue1) } else { None } {
            slots.push((CHEAT_SLOT, in_root(cue1, &cht)));
        }
        let data_path = rel(&root, &common);
        let mut prefix = if data_path != "." { format!("{data_path}/") } else { String::new() };
        if let Some(shared) = shared_cards.then(|| shared_card_name(&cue1_stem)).flatten() {
            // the same shared card as the per-disc files would use
            for s in &mut slots {
                s.1 = format!("{prefix}{}", s.1);
            }
            slots.push((CARD_SLOT, shared.clone()));
            let entry = writer.discs.entry(shared).or_default();
            entry.extend(numbers.iter().map(|n| stem(&name_of(&parts[n].0)).to_string()));
            prefix = String::new();
        }
        // Named after disc 1 without its disc tag ("Final Fantasy VII (USA).json").
        let name = disc_n().replace_all(&cue1_stem, "").trim().to_string();
        writer.write_instance(&name, &prefix, &slots)?;
        for n in &numbers {
            // per-disc files from earlier runs
            let s = stem(&name_of(&parts[n].0)).to_string();
            if s != name {
                let _ = fs::remove_file(out.join(format!("{s}.json")));
            }
        }
        log(&format!("wrote {name}.json ({} disc{})", numbers.len(), if numbers.len() > 1 { "s" } else { "" }));
        written += 1;
    }
    if written > 0 {
        let _ = fs::remove_file(out.join(HELP_ENTRY));
    }
    let discs = writer.discs;
    for (name, stems) in &discs {
        if let Some(seeded) = seed_shared_card(card, platform, core, stems, name) {
            log(&format!("shared card {name} starts from {seeded}"));
        }
    }
    let shown_out = rel(&out, card);
    log(&format!(
        "{written} instance files in {shown_out}, {skipped} skipped{}",
        if discs.is_empty() { String::new() } else { format!(", {} multi-disc games share a card", discs.len()) }
    ));
    Ok(Summary { written, shared: discs.len() })
}
