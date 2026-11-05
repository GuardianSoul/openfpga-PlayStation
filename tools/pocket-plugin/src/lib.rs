// SPDX-License-Identifier: MIT
// Pocket Sync / pupdate plugin (openfpga-library/pocket-plugin): makes the
// game files for the PlayStation core on the SD card, with one entry per
// multi-disc game, as scripts/make_pocket_instances.py does.

use extism_pdk::*;
use serde::{Deserialize, Serialize};
use std::fs;
use std::path::Path;

mod instances;
use instances::convert::{self, Mode};

const CARD: &str = "pocket";
const MAKE: &str = "Generate game files";
const KEEP: &str = "Convert them, keep the originals";
const REPLACE: &str = "Convert them, replace the originals";
const SKIP: &str = "Skip them, generate the other game files";
const BIOS: &str = "Check the BIOS";
const EXIT: &str = "Exit";
const OURS: &str = "GuardianSoul.PlayStation";
/// The first progress line (game files); each next one at twice as many.
const PROGRESS: usize = 100;
/// The full log of the last run, next to the game files.
const REPORT_FILE: &str = "Game files report.txt";
/// Skip, warning and grouping lines shown in one run.
const MAX_DETAILS: usize = 4;

#[derive(Clone, Serialize, ToBytes)]
#[encoding(Json)]
enum PluginMessage {
    Choice { name: String, query: String, choices: Vec<String> },
    #[allow(dead_code)] // part of the host protocol; this plugin never asks for text
    Text { name: String, query: String },
    Exit,
}

#[derive(Clone, Deserialize, FromBytes)]
#[encoding(Json)]
enum HostMessage {
    Answer { name: String, value: String },
    Kill,
}

#[host_fn]
extern "ExtismHost" {
    fn open_url(url: &str) -> ();
    fn print_msg(message: &str) -> ();
}

/// Every line of this run, for the report file (the window shows fewer).
static REPORT: std::sync::Mutex<Vec<String>> = std::sync::Mutex::new(Vec::new());

/// A line kept for the report file only.
fn note(msg: &str) {
    if let Ok(mut report) = REPORT.lock() {
        report.push(msg.to_string());
    }
}

/// A line for the window only (it makes no sense inside the report).
fn say(msg: &str) {
    unsafe {
        let _ = print_msg(&format!("{msg}\n"));
    }
}

fn println(msg: &str) {
    note(msg);
    say(msg);
}

/// Where this run's report goes, once the card and core are known.
static REPORT_PATH: std::sync::Mutex<Option<std::path::PathBuf>> = std::sync::Mutex::new(None);

/// Writes every line so far to the report file. try_lock: the panic hook
/// calls this too, possibly while a lock is held.
fn write_report() -> Result<(), String> {
    let path = REPORT_PATH.try_lock().ok().and_then(|p| p.clone()).ok_or("no report path")?;
    let text = REPORT.try_lock().map(|r| r.join("\n") + "\n").map_err(|_| "report busy")?;
    fs::write(&path, text).map_err(|e| e.to_string())
}

fn mb(bytes: u64) -> u64 { (bytes + (1 << 20) - 1) >> 20 }

fn platform(core: &str) -> Option<String> {
    instances::find_cores(Path::new(CARD)).into_iter().find(|c| c.id == core && c.supported).map(|c| c.platform)
}

fn check_bios(core: &str, platform: &str) {
    println(&format!("Checking the BIOS in Assets/{platform}/common ..."));
    for line in instances::bios::check_bios(Path::new(CARD), core, platform) {
        println(&line);
    }
}

/// What to do for this core: when discs need converting first, how.
fn menu(core: &str) -> PluginMessage {
    let Some(platform) = platform(core) else { return PluginMessage::Exit };
    let jobs = convert::plan(Path::new(CARD), core, &platform);
    let name = format!("action:{core}");
    if jobs.is_empty() {
        return PluginMessage::Choice { name, query: format!("Game files for {core}"), choices: vec![MAKE.into(), BIOS.into(), EXIT.into()] };
    }
    for job in &jobs {
        println(&format!("needs converting: {} ({} MB)", job_name(job), mb(job.bytes)));
    }
    let keep: u64 = jobs.iter().map(|j| j.bytes).sum();
    let net = convert::replace_net(&jobs);
    let replace = if net > 0 { format!("adds {} MB", mb(net as u64)) } else { format!("frees {} MB", mb(net.unsigned_abs())) };
    let disc = if jobs.len() == 1 { "1 disc needs".to_string() } else { format!("{} discs need", jobs.len()) };
    PluginMessage::Choice {
        name,
        query: format!("{disc} converting first (CHD images, or discs the core needs in another layout)"),
        choices: vec![
            format!("{KEEP} (adds {} MB to the SD card)", mb(keep)),
            format!("{REPLACE} ({replace})"),
            SKIP.into(),
            BIOS.into(),
            EXIT.into(),
        ],
    }
}

fn job_name(job: &convert::Job) -> String { job.shown().to_string() }

fn run(core: &str, mode: Option<Mode>) -> PluginMessage {
    let card = Path::new(CARD);
    let Some(platform) = platform(core) else {
        println(&format!("{core} is no longer on the SD card."));
        return PluginMessage::Exit;
    };
    convert::resume(card, &mut |line| println(line));
    let left = instances::clean_leftovers(card, &platform);
    if left > 0 {
        println(&format!("Removed {left} unfinished file(s) from an earlier run that was stopped part-way."));
    }
    if let Some(mode) = mode {
        let jobs = convert::plan(card, core, &platform);
        println(&format!("Converting {} disc(s) in Assets/{platform}/common ...", jobs.len()));
        convert::run(card, &platform, &jobs, mode, &mut |line| println(line));
    }
    if let Some(elsewhere) = instances::games_elsewhere(card, &platform) {
        println(&format!(
            "Your games are in {elsewhere}, but {core} looks for them in Assets/{platform}/common: move each game's folder there, then Generate game files again."
        ));
        if !card.join("Assets").join(&platform).join("common").is_dir() {
            check_bios(core, &platform);
            return PluginMessage::Exit;
        }
    }
    println(&format!("Looking for games in Assets/{platform}/common ..."));
    // Pocket Sync's log stops following a burst of lines that overflows the
    // window, and its "can be closed" line can drop below it, so a run looked
    // hung. So the log stays short: no line per game file or shared card,
    // progress at 100, 200, 400, ... game files, and at most MAX_DETAILS
    // skip, warning and grouping lines (tests set the config key "verbose"
    // for every line).
    let verbose = config::get("verbose").ok().flatten().is_some();
    let (mut made, mut mark, mut details) = (0, PROGRESS, 0);
    const EVERY: std::time::Duration = std::time::Duration::from_secs(10);
    let mut last = std::time::Instant::now();
    let mut seeded = 0;
    let mut hidden: Vec<String> = Vec::new();
    if !verbose {
        if let Ok(mut path) = REPORT_PATH.lock() {
            *path = Some(card.join("Assets").join(&platform).join(core).join(REPORT_FILE));
        }
    }
    let mut log = |line: &str| {
        if verbose {
            return println(line);
        }
        if line.starts_with("wrote ") && line.contains(".json (") {
            note(line);
            made += 1;
            // Also every 10 seconds: a slow SD card takes a while, and a
            // window with no new line looks stuck.
            if made == mark || last.elapsed() >= EVERY {
                println(&format!("{made} game files so far ..."));
                last = std::time::Instant::now();
                while mark <= made {
                    mark *= 2;
                }
            }
        } else if line.starts_with("shared card ") {
            // Starting each shared card from a disc's save takes a while too.
            note(line);
            seeded += 1;
            if last.elapsed() >= EVERY {
                println(&format!("{seeded} shared memory cards so far ..."));
                last = std::time::Instant::now();
            }
        } else if line.starts_with(|c: char| c.is_ascii_digit()) && line.contains(" game files in ") {
            // the summary
            if !hidden.is_empty() {
                say(&format!("... and {} more like these: the report file named below lists them all.", hidden.len()));
            }
            println(line);
        } else if line.starts_with("skip ")
            || line.starts_with("warning: ")
            || line.starts_with("wrote ")
            || line.contains(" discs in one folder, grouped")
            || line.contains(": the discs' names differ")
        {
            details += 1;
            if details <= MAX_DETAILS {
                println(line)
            } else {
                note(line);
                hidden.push(line.to_string())
            }
        } else {
            println(line);
        }
    };
    match instances::make_instances(card, core, &platform, &mut log) {
        Ok(summary) => {
            if summary.written > 0 {
                println("Done. Start a game from its .json in the core's file list.");
            }
            if summary.shared > 0 {
                println(&format!("Multi-disc games: change discs in Core Settings > {}.", instances::disc_menu_name(card, core)));
            }
        }
        Err(error) => println(&format!("Stopped: {error}")),
    }
    check_bios(core, &platform);
    if !verbose {
        // Every line, with each game file and shared card, next to the game files.
        match write_report() {
            Ok(()) => say(&format!("Every line, with each game file: Assets/{platform}/{core}/{REPORT_FILE}")),
            Err(error) => {
                // No report: show the lines it would have kept.
                say(&format!("Could not save the report file Assets/{platform}/{core}/{REPORT_FILE} ({error}). The lines left out above:"));
                for line in &hidden {
                    say(line);
                }
            }
        }
    }
    println("Finished. You can close this window.");
    PluginMessage::Exit
}

#[plugin_fn]
pub fn start() -> FnResult<PluginMessage> {
    // A panic stops the plugin without an Exit, so the host keeps waiting:
    // at least say so in the log.
    std::panic::set_hook(Box::new(|info| {
        println(&format!("The plugin stopped with an error ({info}). You can close this window; please report this line."));
        // Keep what it found so far.
        let _ = write_report();
    }));
    println(&format!("PlayStation game files {}", env!("CARGO_PKG_VERSION")));
    let (cores, others): (Vec<_>, Vec<_>) = instances::find_cores(Path::new(CARD)).into_iter().partition(|c| c.supported);
    let others: Vec<String> = others.into_iter().map(|c| c.id).collect();
    if !others.is_empty() && !cores.is_empty() {
        println(&format!("Not listed: {} (another developer's PlayStation core; this plugin makes game files for {OURS}).", others.join(", ")));
    }
    Ok(match cores.len() {
        0 if !others.is_empty() => {
            println(&format!(
                "{} is another developer's PlayStation core. This plugin makes game files only for the {OURS} core, which is not on this \
                 SD card. Install {OURS} (in pupdate or Pocket Sync), put each game in its own folder in Assets/playstation/common/, \
                 then Generate game files again.",
                others.join(" and ")
            ));
            PluginMessage::Exit
        }
        0 => {
            println(&format!("No PlayStation core found in Cores/ on this SD card. Install the {OURS} core first (in pupdate or Pocket Sync)."));
            PluginMessage::Exit
        }
        1 => menu(&cores[0].id),
        _ => {
            let mut choices: Vec<String> = cores.into_iter().map(|c| c.id).collect();
            choices.push(EXIT.into());
            PluginMessage::Choice { name: "core".into(), query: "Which PlayStation core are the game files for?".into(), choices }
        }
    })
}

#[plugin_fn]
pub fn handle_response(input: HostMessage) -> FnResult<PluginMessage> {
    Ok(match input {
        HostMessage::Answer { value, .. } if value == EXIT => PluginMessage::Exit,
        HostMessage::Answer { name, value } if name == "core" => menu(&value),
        HostMessage::Answer { name, value } => match name.strip_prefix("action:") {
            Some(core) if value == BIOS => {
                match platform(core) {
                    Some(platform) => check_bios(core, &platform),
                    None => println(&format!("{core} is no longer on the SD card.")),
                }
                PluginMessage::Exit
            }
            Some(core) if value == MAKE || value == SKIP => run(core, None),
            Some(core) if value.starts_with(KEEP) => run(core, Some(Mode::Keep)),
            Some(core) if value.starts_with(REPLACE) => run(core, Some(Mode::Replace)),
            _ => PluginMessage::Exit,
        },
        HostMessage::Kill => PluginMessage::Exit,
    })
}
