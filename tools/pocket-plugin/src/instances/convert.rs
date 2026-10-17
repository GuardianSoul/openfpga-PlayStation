// SPDX-License-Identifier: MIT
// Discs the core cannot play as they are: CHD images (extracted), merged
// images on per-track cores (split) and per-track files on one-file cores
// (joined), as make_pocket_instances.py --convert makes them.
//
// A full SD card corrupts easily, so every large file claims its space
// plus RESERVE before it is written (write_parts), and originals are only
// removed once the new files are written and read back. Keep needs room
// for the new files next to the originals. Replace removes the originals;
// when the card has no room for both, the disc is converted in the
// plugin's folder on the computer first, then the originals are removed
// and the new files copied over. If that copy fails, the converted files
// stay on the computer.

use super::{
    chd_image, claim, has_ext, is_file, is_no_space, mb, msf_text, name_of, no_space, one_file_plan, parse_cue, rel, size, split_cue,
    stem, walk, write_bytes, write_parts, copy_range, CueFiles, SECTOR, PREGAP,
};
use std::fs;
use std::io;
use std::path::{Path, PathBuf};

const NO_SPACE: &str = "not enough free space";
/// The plugin's own folder on the computer, under the names hosts give it.
const TEMP_ROOTS: [&str; 2] = ["host", "computer"];
const TEMP_FOLDER: &str = "PlayStation conversions";

#[derive(Clone, Copy, PartialEq)]
pub enum Mode {
    Keep,
    Replace,
}

enum Kind {
    /// Extract into dest (split into tracks for per-track cores).
    Chd { chd: PathBuf, dest: PathBuf, split: bool },
    /// Split a merged image into dest (per-track cores).
    Split { cue: PathBuf, bin: PathBuf, dest: PathBuf },
    /// Join a disc's files into image (one-file cores).
    Join { cue: PathBuf, files: CueFiles, sources: Vec<PathBuf>, tracks: Vec<(u64, u64)>, image: PathBuf },
}

pub struct Job {
    kind: Kind,
    /// The original, below common.
    shown: String,
    /// Bytes the new files take, and bytes Replace removes.
    pub bytes: u64,
    pub freed: u64,
}

/// io errors as report text; running out of space always starts with NO_SPACE.
pub(super) fn reason(e: io::Error) -> String {
    if is_no_space(&e) && !e.to_string().starts_with(NO_SPACE) {
        format!("{NO_SPACE} ({e})")
    } else {
        e.to_string()
    }
}

/// Discs under Assets/<platform>/common that need converting for this core,
/// in the order make_pocket_instances.py --convert handles them.
pub fn plan(card: &Path, core: &str, platform: &str) -> Vec<Job> {
    let common = card.join("Assets").join(platform).join("common");
    let one_file = super::is_one_file(card, core);
    // Cue-reader cores read per-track files and merged images as they are:
    // only CHD images need converting (to one merged image).
    let cue_reader = super::is_cue_reader(card, core);
    let mut jobs = vec![];
    let unconverted = |image: &Path| {
        let base = stem(&name_of(image)).to_string();
        let folder = image.parent().unwrap();
        (name_of(folder) != base && !is_file(&folder.join(&base).join(format!("{base}.cue")))).then(|| folder.join(base))
    };
    for chd in walk(&common, &|n| has_ext(n, ".chd")) {
        let Some(dest) = unconverted(&chd) else { continue };
        let bytes = chd_image::output_bytes(&chd).unwrap_or(0);
        jobs.push(Job { shown: rel(&chd, &common), bytes, freed: size(&chd).unwrap_or(0), kind: Kind::Chd { chd, dest, split: !one_file && !cue_reader } });
    }
    for cue in walk(&common, &|n| has_ext(n, ".cue")).into_iter().filter(|_| !cue_reader) {
        let Ok(files) = parse_cue(&cue) else { continue };
        let folder = cue.parent().unwrap().to_path_buf();
        let cue_name = name_of(&cue);
        if !one_file {
            let Some(dest) = unconverted(&cue) else { continue };
            if files.len() != 1 || files[0].1.len() < 2 || is_file(&folder.join(format!("{}.chd", stem(&cue_name)))) {
                continue;
            }
            let bin = super::join(&folder, &files[0].0);
            let Some(bin_bytes) = size(&bin) else { continue };
            let bytes = bin_bytes + (files[0].1.len() as u64 - 1) * PREGAP * SECTOR;
            jobs.push(Job { shown: rel(&cue, &common), bytes, freed: bin_bytes, kind: Kind::Split { cue, bin, dest } });
            continue;
        }
        if name_of(&folder) == stem(&cue_name) && is_file(&folder.parent().unwrap().join(&cue_name)) {
            continue; // split copy of a merged cue
        }
        let Ok((sources, tracks)) = one_file_plan(&files, &folder) else { continue };
        let total = sources.iter().map(|s| s.1).sum::<u64>() * SECTOR;
        let image = folder.join(format!("{} (one file).bin", stem(&cue_name)));
        if sources.len() < 2 || size(&image) == Some(total) {
            continue;
        }
        let sources = sources.into_iter().map(|s| s.0).collect();
        jobs.push(Job { shown: rel(&cue, &common), bytes: total, freed: total, kind: Kind::Join { cue, files, sources, tracks, image } });
    }
    jobs
}

impl Job {
    /// The original, below Assets/<platform>/common.
    pub fn shown(&self) -> &str { &self.shown }
}

/// What Replace adds to the card in the end (negative: frees).
pub fn replace_net(jobs: &[Job]) -> i64 {
    jobs.iter().map(|j| j.bytes as i64 - j.freed as i64).sum()
}

fn temp_root() -> Option<PathBuf> {
    TEMP_ROOTS.iter().map(PathBuf::from).find(|p| p.is_dir())
}

/// Whether bytes plus RESERVE fit on the card now (claimed, then let go).
fn has_room(card: &Path, bytes: u64) -> bool {
    let check = card.join(".psx-space-check");
    let ok = fs::File::create(&check).and_then(|f| claim(&f, &check, bytes)).is_ok();
    let _ = fs::remove_file(&check);
    ok
}

/// Where the job's new files go on the card.
fn card_dest(job: &Job) -> &Path {
    match &job.kind {
        Kind::Chd { dest, .. } | Kind::Split { dest, .. } => dest,
        Kind::Join { image, .. } => image.parent().unwrap(),
    }
}

/// Write the job's new files into dest; returns the file to report.
fn produce(job: &Job, dest: &Path, log: &mut dyn FnMut(&str)) -> Result<PathBuf, String> {
    match &job.kind {
        Kind::Chd { chd, split, .. } => {
            log(&format!("  extracting {} ({} MB)", name_of(chd), mb(job.bytes)));
            chd_image::extract(chd, dest, *split)
        }
        Kind::Split { cue, .. } => split_cue(cue, dest, false),
        Kind::Join { sources, image, .. } => {
            log(&format!("  joining {} files into {} ({} MB)", sources.len(), name_of(image), mb(job.bytes)));
            let out = dest.join(name_of(image));
            write_parts(&out, Some(job.bytes), &mut |w| {
                for path in sources {
                    copy_range(path, 0, size(path).unwrap_or(0), w)?;
                }
                Ok(())
            })
            .map_err(reason)?;
            Ok(out)
        }
    }
}

/// Undo a failed produce in dest, when produce made the folder (a join only
/// ever leaves its .part, which write_parts removes).
fn clean(job: &Job, dest: &Path, existed: bool) {
    if !existed && !matches!(job.kind, Kind::Join { .. }) {
        let _ = fs::remove_dir_all(dest);
    }
}

/// The cue of a joined disc: its tracks at their offsets in the one image.
fn joined_cue(image: &str, files: &CueFiles, tracks: &[(u64, u64)]) -> String {
    let mut lines = vec![format!("FILE \"{image}\" BINARY")];
    for (t, &(start, index01)) in files.iter().flat_map(|f| &f.1).zip(tracks) {
        lines.push(format!("  TRACK {:02} {}", t.number, t.kind));
        if start != index01 {
            lines.push(format!("    INDEX 00 {}", msf_text(start)));
        }
        lines.push(format!("    INDEX 01 {}", msf_text(index01)));
    }
    lines.join("\n") + "\n"
}

/// Remove the job's originals (its new files are in place on the card). A
/// joined disc's cue is rewritten to name the joined image.
fn replace_originals(job: &Job, log: &mut dyn FnMut(&str)) -> Result<(), String> {
    let gone: Vec<&PathBuf> = match &job.kind {
        Kind::Chd { chd, .. } => vec![chd],
        Kind::Split { cue, bin, .. } => vec![bin, cue],
        Kind::Join { cue, files, sources, tracks, image } => {
            write_bytes(cue, joined_cue(&name_of(image), files, tracks).as_bytes()).map_err(reason)?;
            sources.iter().filter(|s| *s != image).collect()
        }
    };
    for path in gone {
        fs::remove_file(path).map_err(|e| format!("{}: {e}", name_of(path)))?;
    }
    log(&format!("  removed the original {}", job.shown));
    Ok(())
}

/// The journal of a conversion made on the computer, in its work folder
/// next to files/. It is written once the new files are complete, before
/// anything on the card changes, so a run that stops part-way (closed, card
/// pulled, power lost) is finished by the next one (resume). Paths are below
/// the card.
#[derive(serde::Serialize, serde::Deserialize)]
struct Journal {
    /// The original, as reported.
    shown: String,
    /// Where files/ goes on the card.
    dest: String,
    /// Originals to remove first (the room the copy needs).
    remove: Vec<String>,
    /// Afterwards: files to write (a joined disc's new cue) and to remove.
    write: Vec<(String, String)>,
    unlink: Vec<String>,
}

const JOURNAL: &str = "journal.json";

/// Tests stop the plugin at a named step (config test_stop_at) to check
/// that the next run finishes the job.
fn test_stop(step: &str) {
    if extism_pdk::config::get("test_stop_at").ok().flatten().as_deref() == Some(step) {
        panic!("stopped at {step} (test)");
    }
}

/// Convert in the computer's folder, then swap the originals on the card for
/// the new files.
fn through_computer(card: &Path, job: &Job, temp: &Path, log: &mut dyn FnMut(&str)) -> Result<PathBuf, String> {
    let work = temp.join(TEMP_FOLDER).join(stem(&name_of(Path::new(&job.shown))));
    if work.join(JOURNAL).exists() {
        // A ready conversion that resume could not finish: never overwrite it.
        return Err(format!("an earlier conversion is still waiting to be copied back, in the plugin's folder under \"{TEMP_FOLDER}/{}\"", name_of(&work)));
    }
    let _ = fs::remove_dir_all(&work); // unfinished: its originals are still on the card
    log("  the SD card is short of space: converting on the computer first");
    let files_dir = work.join("files");
    let made = produce(job, &files_dir, log).inspect_err(|_| {
        let _ = fs::remove_dir_all(&work);
    })?;
    // After the originals go, the new files must fit with RESERVE to spare.
    let extra = job.bytes.saturating_sub(job.freed);
    if !has_room(card, extra) {
        let _ = fs::remove_dir_all(&work);
        return Err(no_space(extra).to_string());
    }
    let on_card = |p: &Path| rel(p, card);
    let mut journal = Journal { shown: job.shown.clone(), dest: on_card(card_dest(job)), remove: vec![], write: vec![], unlink: vec![] };
    match &job.kind {
        Kind::Chd { chd, .. } => journal.remove.push(on_card(chd)),
        Kind::Split { cue, bin, .. } => {
            journal.remove.push(on_card(bin));
            journal.unlink.push(on_card(cue));
        }
        Kind::Join { cue, files, sources, tracks, image } => {
            journal.remove.extend(sources.iter().filter(|s| *s != image).map(|s| on_card(s)));
            journal.write.push((on_card(cue), joined_cue(&name_of(image), files, tracks)));
        }
    }
    let text = serde_json::to_string_pretty(&journal).map_err(|e| e.to_string())?;
    write_bytes(&work.join(JOURNAL), text.as_bytes()).map_err(reason)?;
    test_stop("journal");
    finish(card, &work, &journal, log)?;
    Ok(card.join(&journal.dest).join(rel(&made, &files_dir)))
}

/// Carry out a journal: remove the originals, copy files/ to the card
/// (each file written, synced and read back), apply the cue changes, then
/// drop the work folder. Every step can be repeated, so a stopped run is
/// simply finished again.
fn finish(card: &Path, work: &Path, journal: &Journal, log: &mut dyn FnMut(&str)) -> Result<(), String> {
    let files_dir = work.join("files");
    for path in &journal.remove {
        let path = card.join(path);
        if path.exists() {
            fs::remove_file(&path).map_err(|e| format!("{}: {e}", name_of(&path)))?;
        }
        test_stop("remove");
    }
    let dest = card.join(&journal.dest);
    for file in walk(&files_dir, &|_| true) {
        let target = dest.join(rel(&file, &files_dir));
        let bytes = size(&file).unwrap_or(0);
        if same_file(&file, &target) {
            continue; // copied before the run stopped
        }
        write_parts(&target, Some(bytes), &mut |w| copy_range(&file, 0, bytes, w)).map_err(|e| {
            format!(
                "copying back to the SD card failed ({}); the original is removed and the converted files are kept on \
                 the computer, in the plugin's folder under \"{TEMP_FOLDER}/{}\". The next run copies them back.",
                reason(e),
                name_of(work)
            )
        })?;
        test_stop("copied");
    }
    for (path, text) in &journal.write {
        write_bytes(&card.join(path), text.as_bytes()).map_err(reason)?;
    }
    for path in &journal.unlink {
        let _ = fs::remove_file(card.join(path));
    }
    let _ = fs::remove_dir_all(work);
    log(&format!("  removed the original {}", journal.shown));
    Ok(())
}

/// Whether two files hold the same bytes.
fn same_file(a: &Path, b: &Path) -> bool {
    use std::io::Read;
    if size(a).is_none() || size(a) != size(b) {
        return false;
    }
    let (Ok(mut a), Ok(mut b)) = (fs::File::open(a), fs::File::open(b)) else { return false };
    // Fill the whole buffer (or up to the end), so both sides read alike.
    fn fill(f: &mut fs::File, buf: &mut [u8]) -> io::Result<usize> {
        let mut n = 0;
        while n < buf.len() {
            match f.read(&mut buf[n..])? {
                0 => break,
                k => n += k,
            }
        }
        Ok(n)
    }
    let (mut x, mut y) = (vec![0u8; 1 << 20], vec![0u8; 1 << 20]);
    loop {
        let (Ok(n), Ok(m)) = (fill(&mut a, &mut x), fill(&mut b, &mut y)) else { return false };
        if n != m || x[..n] != y[..m] {
            return false;
        }
        if n == 0 {
            return true;
        }
    }
}

/// Finish conversions that a stopped run left in the computer's folder (see
/// Journal); folders without a journal never touched the card and go. Runs
/// before anything else.
pub fn resume(card: &Path, log: &mut dyn FnMut(&str)) {
    let Some(temp) = temp_root() else { return };
    let Ok(entries) = fs::read_dir(temp.join(TEMP_FOLDER)) else { return };
    for work in entries.flatten().map(|e| e.path()).filter(|p| p.is_dir()) {
        let Ok(text) = fs::read_to_string(work.join(JOURNAL)) else {
            if work.join("files").is_dir() {
                let _ = fs::remove_dir_all(&work); // stopped while converting; the original is on the card
            }
            continue;
        };
        let Ok(journal) = serde_json::from_str::<Journal>(&text) else {
            log(&format!("could not read {TEMP_FOLDER}/{}/{JOURNAL}; its files are left there", name_of(&work)));
            continue;
        };
        log(&format!("Finishing the conversion of {} that an earlier run left part-way", journal.shown));
        let files_dir = work.join("files");
        let dest = card.join(&journal.dest);
        let needed: u64 = walk(&files_dir, &|_| true).iter().map(|f| size(f).unwrap_or(0)).sum::<u64>()
            .saturating_sub(walk(&files_dir, &|_| true).iter().map(|f| size(&dest.join(rel(f, &files_dir))).unwrap_or(0)).sum())
            .saturating_sub(journal.remove.iter().map(|p| size(&card.join(p)).unwrap_or(0)).sum());
        if !has_room(card, needed) {
            log(&format!("  {}; the files stay in the plugin's folder under \"{TEMP_FOLDER}/{}\"", no_space(needed), name_of(&work)));
            continue;
        }
        if let Err(e) = finish(card, &work, &journal, log) {
            log(&format!("  {e}"));
        }
    }
}

fn run_job(card: &Path, job: &Job, mode: Mode, temp: Option<&Path>, log: &mut dyn FnMut(&str)) -> Result<PathBuf, String> {
    let dest = card_dest(job).to_path_buf();
    let existed = dest.exists();
    match produce(job, &dest, log) {
        Ok(made) => {
            if mode == Mode::Replace {
                replace_originals(job, log)?;
            }
            Ok(made)
        }
        Err(e) => {
            clean(job, &dest, existed);
            match (e.starts_with(NO_SPACE), mode, temp) {
                (true, Mode::Replace, Some(temp)) => through_computer(card, job, temp, log),
                (true, Mode::Keep, _) => Err(format!("{e}; converting with \"replace the originals\" needs less")),
                _ => Err(e),
            }
        }
    }
}

/// Convert the planned discs. Reports like make_pocket_instances.py
/// --convert (joins and progress go on indented lines).
pub fn run(card: &Path, platform: &str, jobs: &[Job], mode: Mode, log: &mut dyn FnMut(&str)) -> (usize, usize) {
    let common = card.join("Assets").join(platform).join("common");
    let temp = temp_root();
    let (mut done, mut failed) = (0, 0);
    for job in jobs {
        let joining = matches!(job.kind, Kind::Join { .. });
        match run_job(card, job, mode, temp.as_deref(), log) {
            Ok(_) if joining => log(&format!("  joined {}", job.shown)),
            Ok(made) => {
                log(&format!("converted {} -> {}", job.shown, rel(&made, &common)));
                done += 1;
            }
            Err(e) if joining => log(&format!("  could not join {}: {e}", job.shown)),
            Err(e) => {
                log(&format!("skip {}: {e}", job.shown));
                failed += 1;
            }
        }
    }
    log(&format!("{done} images converted, {failed} failed"));
    (done, failed)
}
