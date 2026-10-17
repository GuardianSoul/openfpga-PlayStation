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
audio tracks are supported. A LibCrypt disc needs its redump `.sbi` next to
the `.cue`, with the same name. `.chd` images need extracting first; the
game files plugin does that for you.

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

Multi-disc games need one folder per disc.

## Game files

The Pocket starts a game from a small game file (`.json`). The
[PlayStation game files plugin](https://github.com/GuardianSoul/openfpga-PlayStation-plugin)
makes them for every game on the card:

- **[pupdate](https://github.com/mattpannella/pupdate):** *Pocket Extras →
  Plugins → Install from GitHub…*, enter
  `GuardianSoul/openfpga-PlayStation-plugin`.
- **[Pocket Sync](https://github.com/neil-morrison44/pocket-sync):**
  *Plugins → Add Plugin*, paste
  `https://github.com/GuardianSoul/openfpga-PlayStation-plugin` and press
  *Verify & install plugin*.

Then, with the SD card in your computer, run *PlayStation game files* (in
pupdate from *Pocket Extras → Plugins*, in Pocket Sync with the play button
next to it on the *Plugins* page), and again whenever you add games. In the
core, pick the game. The list starts with a *How to add games* entry until your first game
file is made; you can delete it from
`Assets/playstation/GuardianSoul.PlayStation/`. Discs the core can't play
yet are skipped with a message.

No pupdate or Pocket Sync? The
**[SD card helper](https://guardiansoul.github.io/openfpga-PlayStation/)**
page does the same as the plugin: open it in Chrome or Edge on a computer,
pick the SD card, and press *Make game files*. Nothing to install, and
nothing is uploaded. It can't read `.chd` images.

## Without game files

Game files are the way to play. Only when a disc has no game file yet,
for example because you copied it to the card without running the plugin,
the core can start it directly: in the game list press Back to reach the
files in `Assets/playstation/common/` and pick the disc's `.cue`. That
disc then gets its own memory card, and there is no disc swapping. If a
disc doesn't start this way, make its game file.

## Changing discs

A multi-disc game gets one game file that holds all of its discs. When the
game asks for the next disc, open the Pocket menu, choose **Core Settings →
Swap Discs** and pick the disc. The core opens and closes the lid for you,
and remembers the disc you picked for next time.

All discs of a game share one memory card,
`Saves/playstation/common/<game>.sav` (the game's name without
"(Disc n)"), so a save made on disc 1 is there on disc 2. It starts as a
copy of your most recently used disc's card.

## Other ways to make game files

[make_pocket_instances.py](scripts/make_pocket_instances.py) does the same
as the plugin, from [Python 3](https://www.python.org/downloads/). With the
SD card in your computer, run

```
python3 make_pocket_instances.py <SD card> --core PlayStation
```

where `<SD card>` is the card's drive or mount point (for example `E:\`
or `/Volumes/POCKET`). Add `--convert` to also extract `.chd` images with
`chdman` from [MAME](https://www.mamedev.org/) (install it first), each
into a folder next to the original. The originals stay.
