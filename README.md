# PlayStation core for Analogue Pocket

An FPGA core that runs original PlayStation games on the Analogue Pocket,
built on the MiSTer PSX core.

> **Status: early.** Final Fantasy VII is the reference game and plays from
> start to gameplay with sound and FMVs. Other games may work, may glitch or
> may not boot. See [Compatibility](#compatibility).

## What you need

- An Analogue Pocket with the latest firmware.
- A PlayStation BIOS file dumped from your own console. It is not included
  and cannot be shared.
- Your own game discs as `.bin` image files (see [Games](#games)).

## Install

1. Download the latest `GuardianSoul.PlayStation_<version>_<date>.zip` from
   [Releases](../../releases).
2. Unzip it and copy its contents to the root of your Pocket's SD card. It
   adds `Cores/GuardianSoul.PlayStation`, `Platforms/playstation.json` and
   `Assets/playstation`. Merge them with existing folders; nothing is
   overwritten.
3. Copy your BIOS to `Assets/playstation/common/` on the SD card and name
   it `scph5501.bin` (the file name the core looks for).
4. Copy your games to `Assets/playstation/common/`, one folder per disc,
   and make their game files with pupdate or Pocket Sync (see
   [Games](#games)).
5. Put the SD card back, open **openFPGA → PlayStation** and pick a
   game.

To update, copy the new zip over the old files the same way.

## Games

The core plays redump-style disc images: a `.cue` file with its `.bin`
files (one per track, MODE2/2352), up to 19 `.bin` files per disc. Games
with CD audio tracks are supported. A disc merged into one `.bin` with a
multi-track `.cue` needs splitting first (the [SD card helper](#changing-discs)
does it), and `.chd` images need extracting first (see
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

Multi-disc games need one folder per disc.

The Pocket starts a game from a small game file (`.json`) that lists the
disc's files. Let an SD card tool make them for you:

- **[pupdate](https://github.com/mattpannella/pupdate):** *Update All*
  makes them for every game on the card (or use *Generate Instance JSON
  Files* on its own).
- **[Pocket Sync](https://github.com/neil-morrison44/pocket-sync):** open
  *Games → PlayStation* and press *Instance JSON*.

Do this again after adding games. Then, in the core, pick the game's
`.json` file. Discs the core can't play yet are skipped with a message.
These files are all you need: every game, including each disc of a
multi-disc game, starts from them.

### Changing discs

To swap discs from the menu, a multi-disc game needs one game file that
holds all of its discs. pupdate and Pocket Sync can't make that yet, so
use the **[SD card helper](https://guardiansoul.github.io/openfpga-PlayStation/)**
page: open it in Chrome or Edge on a computer, pick the SD card, and press
*Make game files*. Nothing to install, and nothing is uploaded. It also
splits merged `.bin` images into one file per track.

When the game asks for the next disc, open the Pocket menu, choose **Core
Settings → Disc** and pick the disc. The core opens and closes the lid for
you, and remembers the disc you picked for next time.

All discs of a game then share one memory card,
`Saves/playstation/common/<game>.sav` (the game's name without
"(Disc n)"), so a save made on disc 1 is there on disc 2. The helper
starts it as a copy of your most recently used disc's card.

Without the helper, each disc keeps its own game file, as in 0.5.1: to
change discs, quit and start the next disc's game file.

### Updating from 0.5.1

Nothing to change: your game files and saves keep working. Run the SD card
helper only if you want to swap discs from the menu in multi-disc games.

### Other ways to make game files

[make_pocket_instances.py](scripts/make_pocket_instances.py) does the same
as the SD card helper, from [Python 3](https://www.python.org/downloads/).
With the SD card in your computer, run

```
python3 make_pocket_instances.py <SD card> --core PlayStation
```

where `<SD card>` is the card's drive or mount point (for example `E:\`
or `/Volumes/POCKET`). Add `--convert` to also extract `.chd` images with
`chdman` from [MAME](https://www.mamedev.org/) (install it first) and split
merged `.bin` files, each into a folder next to the original. The originals
stay. The SD card helper can't read `.chd` images.

## Controls

| Pocket | PlayStation |
| --- | --- |
| D-pad | D-pad |
| A | Circle |
| B | Cross |
| X | Triangle |
| Y | Square |
| L / R | L1 / R1 |
| Select / Start | Select / Start |

The core emulates a digital pad. Hold Select and press L, R, Y or A for L2, R2, L3 or R3 (changeable in [Settings](#settings)). Tap Select on its own for Select. Analog sticks are not mapped yet.

## Settings

Under **Core Settings** in the Pocket menu:

- **Skip BIOS logo:** starts the game without the PlayStation intro.
- **Disc:** the disc in the drive, for multi-disc games (see
  [Changing discs](#changing-discs)).
- **L Button / R Button:** what the Pocket's L and R send: L1, L2 or L3
  (R1, R2 or R3). The default is L1 and R1.
- **SELECT + L/R** and **SELECT + Y/A:** what those combinations send:
  nothing, L1/R1, L2/R2 or L3/R3. The defaults are L2/R2 and L3/R3.

High-resolution (interlaced, 480i) screens are woven into full frames
automatically; there is no setting for it.

## Memory cards

Memory card 1 works and is saved to the SD card:

- Each game gets its own memory card:
  `Saves/playstation/GuardianSoul.PlayStation/<game>.sav` on the SD card
  (multi-disc games share one, see [Changing discs](#changing-discs)).
  It is created the first time you start the game, and updating the core
  keeps it.
- The first time, the game may say the card is unformatted. Format it from
  the game's own save menu.
- Save in the game as usual. The card is written to the SD card when you
  quit the core from the Pocket menu, so **quit through the menu before
  you turn the Pocket off**. Turning it off mid-game loses saves made since
  the game started.
- To use a card from another emulator or a backup, copy its raw 128 KB
  image (`.mcr` or `.sav`) over the game's `.sav` file.

## Compatibility

Final Fantasy VII is the reference game. The full list, with what was tested
and how far, is in [COMPATIBILITY.md](COMPATIBILITY.md). Reports are
welcome: open an issue with the game, region, what happened and how far you
got.

## What's in the core

| Part | Status |
| --- | --- |
| CPU (R3000A with instruction cache and scratchpad) | Yes |
| GTE (3D geometry) | Yes |
| GPU: 2D and 3D drawing, transparency, dithering, 240p and 480i | Yes |
| SPU: 24 voices, ADPCM, reverb | Yes |
| MDEC (FMV movies) | Yes |
| CD-ROM with XA streamed audio | Yes |
| Multi-disc games (disc swapping) | Yes |
| Digital controller, port 1 | Yes |
| Memory card, slot 1 (saved to SD) | Yes |
| CD audio tracks (CD-DA), up to 19 files per disc | Yes |
| Analog controller / DualShock, rumble | Not yet |
| Second controller, multitap, light guns | Not yet |
| Memory card slot 2 | Not yet |
| Save states | Not planned for now |
| Serial port (link cable) | No |

## How it fits on the Pocket

The Pocket's FPGA (a Cyclone V with 18,480 logic blocks) is smaller than the
MiSTer's. The first complete build of the MiSTer PSX core with the Pocket
platform around it needed about 28,000 logic blocks, roughly 150% of the
chip, so a full PlayStation on the Pocket looked out of reach. The current
core uses about 17,500 (95%). It got there without dropping anything a game
needs:

- **Save states and rewind are left out.** MiSTer keeps a shadow copy of
  much of the machine's state for them. Without them that logic goes away.
- **Tables move into block RAM.** Register files in the DMA, GTE, SPU and
  memory interface were built from logic cells. They now live in the FPGA's
  memory blocks, which are otherwise idle. Timing is unchanged.
- **A smaller GTE.** The PS-FPGA GTE does the same math as MiSTer's in about
  half the space, with results checked against MiSTer's GTE.
- **External memory instead of on-chip buffers.** Main RAM, BIOS and sound
  RAM sit in the Pocket's SDRAM, and video RAM is spread across both PSRAM
  chips, following pocket_sotn.
- **Unused hardware is trimmed.** The serial port keeps only its reset
  values, the controller port has one digital pad, and an optional MiSTer
  data cache that the real console doesn't have is removed.
- **The Pocket does the scaling.** Video goes out at the console's own
  resolution and the Pocket's scaler handles the rest.

Each change is checked in simulation against the unmodified MiSTer core
before it goes into a build.

## Known issues

- Some games may run slower than on a real console.
- A thin diagonal line can split the white Sony screen while it fades.

## Building from source

You need Quartus Prime Lite 25.1 with Cyclone V support, Python 3 and,
for the tests, Icarus Verilog, GHDL and Verilator.

```bash
git clone --recurse-submodules https://github.com/GuardianSoul/openfpga-PlayStation.git
cd openfpga-PlayStation
make test
```

The release build steps are in [BUILDING.md](BUILDING.md).

## How this was made

This core was written with heavy use of AI coding assistants. OpenAI Codex
and Anthropic Claude wrote most of the new HDL, build scripts, tests and
documentation. A human maintainer directed the work, made the design
decisions and tested every build on real hardware. The upstream code it
builds on (MiSTer PSX, the PS-FPGA GTE and the other projects below) was
written by its human authors.

AI-written changes are checked by simulation against the upstream cores and
by hardware tests. Even so, review it with that origin in mind.

## Credits

- [MiSTer PSX core](https://github.com/MiSTer-devel/PSX_MiSTer) by Robert
  Peip (FPGAzumSpass) and contributors: the PlayStation itself.
- [PS-FPGA](https://github.com/PS-FPGA/ps-fpga) GTE by Romain Piquois
  (Laxer3a).
- [pocket_sotn](https://github.com/akoaysigod/pocket_sotn) by akoaysigod:
  the memory layout this core uses (main RAM on SDRAM, video RAM across
  both PSRAM chips), the PSRAM video RAM and memory card code, the memory
  write FIFO and CPU write path, the Pocket SDRAM timing fixes, and the
  approach to fitting the MiSTer core on the Pocket.
- Adam Gastineau (agg23) and [MIRLO64](https://github.com/tortugaveloz/MIRLO64)
  by tortuga: Pocket platform glue and audio output.

## License

The core is licensed under the [GNU GPL version 2](LICENSE). Files written
for this project are also available under the [MIT license](licenses/MIT.txt)
unless their header says otherwise. Third-party parts keep their own terms;
see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

Not affiliated with Sony Interactive Entertainment or Analogue.
"PlayStation" is a trademark of Sony Interactive Entertainment.
