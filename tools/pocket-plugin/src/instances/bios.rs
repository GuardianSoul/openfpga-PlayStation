// SPDX-License-Identifier: MIT
// The BIOS check: whether the core will find a BIOS it can start, as its
// Chip32 loader (support/chip32/psx_bios_check.asm) decides: a file under
// one of the core's BIOS names in Assets/<platform>/common that is exactly
// 512 KB and has a "System ROM Version" line at 0x7FF32 ending in A (USA),
// J (Japan) or E (Europe).

use super::{has_ext, read_json, rel, BIOS_BYTES};
use std::fs;
use std::io::{Read, Seek, SeekFrom};
use std::path::{Path, PathBuf};

/// The names the core accepts when its data.json doesn't list them
/// (scripts/build_chip32_loader.py's BIOS_NAMES: USA, Japan, Europe).
const BIOS_NAMES: [&str; 16] = [
    "scph5501.bin", "scph7001.bin", "scph1001.bin", "scph7501.bin", "scph9001.bin", "scph101.bin",
    "scph5500.bin", "scph7000.bin", "scph3500.bin", "scph100.bin",
    "scph5502.bin", "scph7002.bin", "scph1002.bin", "scph7502.bin", "scph9002.bin", "scph102.bin",
];
const VERSION_AT: u64 = 0x7ff32;
const VERSION_PREFIX: &[u8] = b"System ROM Version ";
const VERSION_BYTES: usize = 33;

/// What a file is, as the core's loader sees it.
enum Bios {
    /// Its version line ("System ROM Version 4.1 12/16/97 E") and region.
    Usable(String, &'static str),
    WrongSize(u64),
    NotBios,
}

fn region_name(letter: u8) -> Option<&'static str> {
    match letter {
        b'A' => Some("USA"),
        b'J' => Some("Japan"),
        b'E' => Some("Europe"),
        _ => None,
    }
}

fn examine(path: &Path) -> Bios {
    let Ok(mut file) = fs::File::open(path) else { return Bios::NotBios };
    let bytes = file.metadata().map(|m| m.len()).unwrap_or(0);
    if bytes != BIOS_BYTES {
        return Bios::WrongSize(bytes);
    }
    let mut line = [0u8; VERSION_BYTES];
    if file.seek(SeekFrom::Start(VERSION_AT)).is_err() || file.read_exact(&mut line).is_err() || !line.starts_with(VERSION_PREFIX) {
        return Bios::NotBios;
    }
    let text: String = line.iter().take_while(|&&b| b != 0).map(|&b| b as char).collect();
    match region_name(line[VERSION_BYTES - 1]) {
        Some(region) => Bios::Usable(text.trim().to_string(), region),
        None => Bios::NotBios,
    }
}

/// The core's BIOS file names: its data.json's 512 KB slots, in order.
fn bios_names(card: &Path, core: &str) -> Vec<String> {
    let declared: Vec<String> = read_json(&card.join("Cores").join(core).join("data.json"))
        .and_then(|d| d.get("data")?.get("data_slots")?.as_array().cloned())
        .unwrap_or_default()
        .iter()
        .filter(|s| s.get("size_exact").and_then(|v| v.as_u64()) == Some(BIOS_BYTES))
        .filter_map(|s| s.get("filename")?.as_str().map(String::from))
        .collect();
    if declared.is_empty() { BIOS_NAMES.iter().map(|n| n.to_string()).collect() } else { declared }
}

/// The file in folder named name, ignoring case (the Pocket's FAT/exFAT does).
fn named(folder: &Path, name: &str) -> Option<PathBuf> {
    fs::read_dir(folder).ok()?.flatten().map(|e| e.path()).find(|p| p.is_file() && super::name_of(p).eq_ignore_ascii_case(name))
}

/// The name to give a BIOS of this region: the first of the core's names
/// whose last digit is the region's (Sony's scph...1 USA, ...0 Japan, ...2 Europe).
fn name_for(names: &[String], region: &str) -> String {
    let digit = match region {
        "USA" => '1',
        "Japan" => '0',
        _ => '2',
    };
    names
        .iter()
        .find(|n| n.to_ascii_lowercase().strip_suffix(".bin").map_or(false, |s| s.starts_with("scph") && s.ends_with(digit)))
        .or(names.first())
        .cloned()
        .unwrap_or_default()
}

/// Files elsewhere on the card that are a usable BIOS: in common under
/// another name, in another platform's common, the core's own Assets folder
/// or the card's root.
fn strays(card: &Path, platform: &str, core: &str, names: &[String]) -> Vec<(PathBuf, &'static str)> {
    let mut folders = vec![card.to_path_buf(), card.join("Assets").join(platform).join(core)];
    for entry in fs::read_dir(card.join("Assets")).into_iter().flatten().flatten() {
        folders.push(entry.path().join("common"));
    }
    let mut found = vec![];
    for folder in folders {
        for entry in fs::read_dir(&folder).into_iter().flatten().flatten() {
            let path = entry.path();
            let name = super::name_of(&path);
            let candidate = (has_ext(&name, ".bin") || has_ext(&name, ".rom") || has_ext(&name, ".mcr") || !name.contains('.'))
                && !name.starts_with("._")
                && path.is_file();
            let in_place = folder == card.join("Assets").join(platform).join("common") && names.iter().any(|n| n.eq_ignore_ascii_case(&name));
            if candidate && !in_place {
                if let Bios::Usable(_, region) = examine(&path) {
                    found.push((path, region));
                }
            }
        }
    }
    found.sort();
    found
}

/// The BIOS report: one line per BIOS file, then what to do when none is usable.
pub fn check_bios(card: &Path, core: &str, platform: &str) -> Vec<String> {
    let names = bios_names(card, core);
    let common = card.join("Assets").join(platform).join("common");
    let place = format!("Assets/{platform}/common");
    let mut lines = vec![];
    let mut usable = 0;
    for name in &names {
        let Some(path) = named(&common, name) else { continue };
        let shown = super::name_of(&path);
        match examine(&path) {
            Bios::Usable(version, region) => {
                usable += 1;
                lines.push(format!("BIOS {shown}: {region} ({version}), OK"));
            }
            Bios::WrongSize(bytes) => lines.push(format!(
                "BIOS {shown} is {} KB, but a PlayStation BIOS is exactly 512 KB: replace it with a full dump of your console's BIOS",
                (bytes + 1023) / 1024
            )),
            Bios::NotBios => lines.push(format!(
                "BIOS {shown} is not a PlayStation BIOS (it has no \"System ROM Version\" line): replace it with a dump of your console's BIOS"
            )),
        }
    }
    if usable > 0 {
        return lines;
    }
    let found = strays(card, platform, core, &names);
    for (path, region) in &found {
        lines.push(format!(
            "{} is a {region} PlayStation BIOS, but the core doesn't look for it there: copy it to {place}/ as {}",
            rel(path, card),
            name_for(&names, region)
        ));
    }
    if found.is_empty() {
        let [usa, japan, europe] = ["USA", "Japan", "Europe"].map(|r| name_for(&names, r));
        let as_names = if usa == japan && japan == europe {
            usa
        } else {
            format!("{usa} (USA), {japan} (Japan) or {europe} (Europe)")
        };
        lines.push(format!("No BIOS found: the core can't start games without one. Copy your PlayStation BIOS to {place}/ as {as_names}"));
    }
    lines
}
