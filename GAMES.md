# Games

How to put games on the SD card so the core can find them. The short
version is in the [README](README.md#setup).

## BIOS

Copy your BIOS to `Assets/playstation/common/`. The core finds it under its
usual name: USA `scph5501`, `scph7001`, `scph1001`, `scph7501`, `scph9001`
or `scph101`; Japan `scph5500`, `scph7000`, `scph3500` or `scph100`; Europe
`scph5502`, `scph7002`, `scph1002`, `scph7502`, `scph9002` or `scph102`
(each with `.bin`). With several, it takes the first in that order. A
missing or damaged BIOS shows a one-line message, such as
`Missing BIOS scph5501.bin` or `Bad BIOS <name>`.

## Disc images

The core plays disc images as a `.cue` file with its `.bin` files
(MODE2/2352): one `.bin` per track (redump style) or one `.bin` for the
whole disc (as ImgBurn makes them), with up to 99 tracks. Games with CD
audio tracks are supported. The core reads each disc as one `.bin` plus a
small track table (`.trk`); the [SD card helper](#changing-discs) writes
both, joining per-track files into one `<name> (one file).bin` next to them
(the originals stay and are needed: the helper reads them each time it
runs, so such games take twice the space). pupdate and Pocket Sync can't write the table: with their game
files, a game with CD music starts but stops or stays silent where the
music should play. `.chd` images need extracting first (see
[Other ways to make game files](#other-ways-to-make-game-files)).

Put each disc in its own folder under `Assets/playstation/common/`, with
the file names unchanged:

```
Assets/playstation/common/
  Final Fantasy VII (USA) (Disc 1)/
    Final Fantasy VII (USA) (Disc 1).cue
    Final Fantasy VII (USA) (Disc 1).bin
  Tekken 3 (USA)/
    Tekken 3 (USA).cue
    Tekken 3 (USA) (Track 1).bin
    Tekken 3 (USA) (Track 2).bin
```

Multi-disc games need one folder per disc. pupdate skips games copied
loose into `common/` (the SD card helper and the script below don't).

The Pocket starts a game from a small game file (`.json`) that lists the
disc's files. Let an SD card tool make them for you:

- **[pupdate](https://github.com/mattpannella/pupdate):** *Update All*
  makes them for every game on the card (or use *Generate Instance JSON
  Files* on its own; the menu says PC Engine CD, but it works for this core
  too).
- **[Pocket Sync](https://github.com/neil-morrison44/pocket-sync):** open
  *Games → PlayStation* and press *Instance JSON*.
- **[PlayStation game files plugin](https://github.com/GuardianSoul/openfpga-PlayStation-plugin)** for pupdate: in *Pocket Extras → Plugins → Install from
  GitHub…* enter `GuardianSoul/openfpga-PlayStation-plugin`, then run
  *PlayStation game files* from the same menu. It does everything the SD
  card helper does, and also extracts `.chd` images.
- **[SD card helper](https://guardiansoul.github.io/openfpga-PlayStation/)**
  (see [Changing discs](#changing-discs)).

Do this again after adding games. Then, in the core, pick the game's
`.json` file. The list starts with a *How to add games* entry until the SD
card helper or the script below makes your first game file; you can delete
it from `Assets/playstation/GuardianSoul.PlayStation/`. Discs the core
can't play yet are skipped with a message. Every game, including each disc
of a multi-disc game, starts from these files.

## Without game files

The core can also start a disc directly: in the game list press Back to
reach the files in `Assets/playstation/common/` and pick the disc's `.cue`
(or `.bin`). That disc then gets its own memory card, and there is no disc
swapping, so game files are the better way for multi-disc games.

## Changing discs

To swap discs from the menu, a multi-disc game needs one game file that
holds all of its discs. In pupdate the [PlayStation game files plugin](https://github.com/GuardianSoul/openfpga-PlayStation-plugin)
makes it (see above). Otherwise use the **[SD card helper](https://guardiansoul.github.io/openfpga-PlayStation/)**
page: open it in Chrome or Edge on a computer, pick the SD card, and press
*Make game files*. Nothing to install, and nothing is uploaded. It also
writes each disc's track table and joins per-track files into one `.bin`
(see [Disc images](#disc-images)).

When the game asks for the next disc, open the Pocket menu, choose **Core
Settings → Swap Discs** and pick the disc. The core opens and closes the lid for
you, and remembers the disc you picked for next time.

All discs of a game then share one memory card,
`Saves/playstation/common/<game>.sav` (the game's name without
"(Disc n)"), so a save made on disc 1 is there on disc 2. The helper
starts it as a copy of your most recently used disc's card.

Without the plugin or the helper, each disc keeps its own game file: to
change discs, quit and start the next disc's game file.

## Other ways to make game files

[make_pocket_instances.py](scripts/make_pocket_instances.py) does the same
as the SD card helper, from [Python 3](https://www.python.org/downloads/).
With the SD card in your computer, run

```
python3 make_pocket_instances.py <SD card> --core PlayStation
```

where `<SD card>` is the card's drive or mount point (for example `E:\`
or `/Volumes/POCKET`). Add `--convert` to also extract `.chd` images with
`chdman` from [MAME](https://www.mamedev.org/) (install it first), each
into a folder next to the original. The originals stay. The SD card helper can't read `.chd` images.
