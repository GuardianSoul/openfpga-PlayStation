// SPDX-License-Identifier: MIT
// Pocket Sync / pupdate plugin (openfpga-library/pocket-plugin): makes the
// game files for the PlayStation core on the SD card, with one entry per
// multi-disc game, as scripts/make_pocket_instances.py does.

use extism_pdk::*;
use serde::{Deserialize, Serialize};
use std::path::Path;

mod instances;
use instances::convert::{self, Mode};

const CARD: &str = "pocket";
const MAKE: &str = "Generate game files";
const KEEP: &str = "Convert them, keep the originals";
const REPLACE: &str = "Convert them, replace the originals";
const SKIP: &str = "Skip them, generate the other game files";
const EXIT: &str = "Exit";

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

fn println(msg: &str) {
    unsafe {
        let _ = print_msg(&format!("{msg}\n"));
    }
}

fn mb(bytes: u64) -> u64 { (bytes + (1 << 20) - 1) >> 20 }

fn platform(core: &str) -> Option<String> {
    instances::find_cores(Path::new(CARD)).into_iter().find(|c| c.id == core).map(|c| c.platform)
}

/// What to do for this core: when discs need converting first, how.
fn menu(core: &str) -> PluginMessage {
    let Some(platform) = platform(core) else { return PluginMessage::Exit };
    let jobs = convert::plan(Path::new(CARD), core, &platform);
    let name = format!("action:{core}");
    if jobs.is_empty() {
        return PluginMessage::Choice { name, query: format!("Game files for {core}"), choices: vec![MAKE.into(), EXIT.into()] };
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
    println(&format!("Looking for games in Assets/{platform}/common ..."));
    match instances::make_instances(card, core, &platform, &mut |line| println(line)) {
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
    PluginMessage::Exit
}

#[plugin_fn]
pub fn start() -> FnResult<PluginMessage> {
    println("PlayStation game files");
    let cores = instances::find_cores(Path::new(CARD));
    Ok(match cores.len() {
        0 => {
            println("No PlayStation core found in Cores/ on this SD card. Install the core first.");
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
            Some(core) if value == MAKE || value == SKIP => run(core, None),
            Some(core) if value.starts_with(KEEP) => run(core, Some(Mode::Keep)),
            Some(core) if value.starts_with(REPLACE) => run(core, Some(Mode::Replace)),
            _ => PluginMessage::Exit,
        },
        HostMessage::Kill => PluginMessage::Exit,
    })
}
