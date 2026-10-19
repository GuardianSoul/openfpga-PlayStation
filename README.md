# PlayStation core for Analogue Pocket

An FPGA core that runs original PlayStation games on the Analogue Pocket,
built on the MiSTer PSX core.

> Most games tried so far play with sound, FMVs and CD music. Some may still glitch or not boot. See the
> [game list](https://guardiansoul.github.io/openfpga-PlayStation/compatibility.html)
> and vote how your games run.

## Setup

You need an Analogue Pocket with the latest firmware, a PlayStation BIOS
dumped from your own console, and your own game discs as `.cue`/`.bin`
images. The BIOS and games are not included and cannot be shared.

### First time

1. **Core:** download the latest `GuardianSoul.PlayStation_<version>_<date>.zip`
   from [Releases](../../releases), unzip it and copy its contents to the
   root of the SD card, merging with the folders already there. To update,
   do the same with the new zip; saves and settings stay.
2. **BIOS:** copy it to `Assets/playstation/common/`, for example as
   `scph5501.bin` (USA), `scph5500.bin` (Japan) or `scph5502.bin` (Europe).
   Other common BIOS names work too (see [GAMES.md](GAMES.md#bios)). Any
   BIOS plays discs from every region; with several on the card, the core
   uses the one that matches the game's region.
3. **Games:** give each disc its own folder in `Assets/playstation/common/`,
   for example `Assets/playstation/common/Tekken 3 (USA)/`, holding its
   `.cue` and all its `.bin` files with their names unchanged. Do this for
   every disc of a multi-disc game too. A disc with only a `.bin` and no
   `.cue` is fine: the plugin makes the `.cue`.

   **LibCrypt discs:** some European discs (for example Ape Escape, Spyro:
   Year of the Dragon and Crash Team Racing) are copy protected and also
   need the `.sbi` file for your disc, from
   [PSXDataCenter](https://psxdatacenter.com/sbifiles.html). Rename it like
   the `.cue` (for example `Ape Escape (Europe).sbi`) and put it in the same
   folder **before** step 4. Without it the game can freeze.
4. **Game files (the way to play):** with the SD card in your computer,
   generate one game file (`.json`) per game with the
   [PlayStation game files plugin](https://github.com/GuardianSoul/openfpga-PlayStation-plugin).
   It gives every game CD music, disc swapping and one shared memory card
   for multi-disc games.
   - [pupdate](https://github.com/mattpannella/pupdate): the first time,
     *Pocket Extras → Plugins → Install from GitHub…* and enter
     `GuardianSoul/openfpga-PlayStation-plugin`. Then open *PlayStation game
     files* from *Pocket Extras → Plugins* and choose *Generate game files*.
   - [Pocket Sync](https://github.com/neil-morrison44/pocket-sync): the
     first time, *Plugins → Add Plugin*, paste
     `https://github.com/GuardianSoul/openfpga-PlayStation-plugin` and press
     *Verify & install plugin*. Then press the play button next to it on the
     *Plugins* page and choose *Generate game files*.
5. **Play:** put the card back in the Pocket, open **openFPGA →
   PlayStation** and pick the game.

No pupdate or Pocket Sync? The [SD card helper](https://guardiansoul.github.io/openfpga-PlayStation/)
web page generates the same game files from Chrome or Edge on a computer.

### Adding games later

1. Copy the new disc folders into `Assets/playstation/common/`, as in step 3,
   with the `.sbi` for any LibCrypt disc.
2. Generate game files again with the plugin (or the SD card helper). It adds the
   new games and leaves your saves alone. If you add an `.sbi` to a game
   that already has a game file, generate again too, so the game file picks
   it up.
3. Pick the new game in **openFPGA → PlayStation**.

Always make game files with the plugin. Pocket Sync's own *Games →
Instance JSON* button and pupdate's core updates also write game files, but
only one per disc, so multi-disc games lose Swap Discs and get a memory
card per disc. After updating cores with pupdate, generate game files with
the plugin again.

### Without game files (fallback)

Always generate game files when you can. Starting a disc directly is only a
fallback for a disc that has no game file yet, for example because you
copied it to the card without generating game files: in the game list press
Back to reach `Assets/playstation/common/`, open the disc's folder and pick
its `.cue`. Two things to know:

- The list shows `.cue` files, not `.bin` files: without the `.cue` the
  core doesn't know the disc's tracks, so the game would play without CD
  music and some games would stop where the music starts. A disc that came
  without a `.cue` gets one from the plugin.
- Multi-disc games have no Swap Discs this way, and each disc gets its own
  memory card, so a save made on disc 1 is not there on disc 2.

If a disc doesn't start this way, generate its game file as above.

More details are in [GAMES.md](GAMES.md).

## Controls

| Pocket | PlayStation |
| --- | --- |
| D-Pad | D-Pad |
| A | Circle |
| B | Cross |
| X | Triangle |
| Y | Square |
| L / R | L1 / R1 |
| Select / Start | Select / Start |

By default, hold Select and press L or R for L2 or R2, the D-Pad for the
left analog stick, or A, B, X or Y for the right analog stick (X up, B
down, Y left, A right). Tap Select on its own for Select, or hold it alone
for a second. All of this can be changed in [Settings](#settings).

The pad is a DualShock. **Controller Mode** switches it between Digital
and Analog at any time, like the controller's Analog button; games that
use the analog sticks also switch it themselves, and L3 + R3 + Up does too.
Controllers connected to the Dock keep their own L2, R2, L3 and R3.

## Settings

Under **Core Settings** in the Pocket menu. Defaults are in bold; the Pocket
remembers your choices.

| Setting | Choices | What it does |
| --- | --- | --- |
| Swap Discs | **Disc 1**, Disc 2, Disc 3, Disc 4 | The disc in the drive, for multi-disc games (see [Changing discs](GAMES.md#changing-discs)) |
| Skip BIOS Logo | On, **Off** | Starts the game without the PlayStation intro |
| Aspect Ratio | **Original**, Fill Screen | Original shows the console's 4:3 picture; Fill Screen fills the Pocket's whole screen |
| Hide Overscan | **On**, Off | In Original, blacks out the lines at the top and bottom that a TV hides; Fill Screen always crops them |
| Controller Mode | **Digital**, Analog | The DualShock's mode, switched at once (see [Controls](#controls)) |
| L Button | **L1**, L2, L3 | What the Pocket's L sends |
| R Button | **R1**, R2, R3 | What the Pocket's R sends |
| D-Pad | **D-Pad**, Face Buttons, Left Analog Stick, Right Analog Stick | What the Pocket's D-Pad sends |
| A/B/X/Y | **Face Buttons**, D-Pad, Left Analog Stick, Right Analog Stick | What the Pocket's A, B, X and Y send |
| SELECT + L/R | None, L1/R1, **L2/R2**, L3/R3 | What Select held with L or R sends |
| SELECT + D-Pad | None, D-Pad, Face Buttons, **Left Analog Stick**, Right Analog Stick | What Select held with the D-Pad sends |
| SELECT + A/B/X/Y | None, Face Buttons, D-Pad, L1/R1, L2/R2, L3/R3, Left Analog Stick, **Right Analog Stick** | What Select held with A, B, X or Y sends (the L and R pairs go on Y and A) |

High-resolution (interlaced, 480i) screens are woven into full frames
automatically; there is no setting for it.

### Other aspect ratios

Aspect Ratio offers Original (4:3) and Fill Screen. For another shape, such
as 8:7, edit the core's `video.json`:

1. Open `Cores/GuardianSoul.PlayStation/video.json` on the SD card.
2. In every entry with `"aspect_w": 4` and `"aspect_h": 3`, change the two
   numbers to the shape you want, for example `8` and `7`. Leave the other
   entries alone: they are Fill Screen.
3. In the core, set Aspect Ratio to Original. It now shows your shape.

A core update replaces `video.json`, so make the edit again after updating.

## Memory cards

Memory card 1 is saved to the SD card, one card per game (multi-disc games
share one), in `Saves/playstation/`. A game's first card starts blank and
formatted.

**Quit through the Pocket menu before you turn the Pocket off.** The card
is written to the SD card when you quit, so turning off mid-game loses the
saves made since the game started. To use a card from another emulator,
copy its raw 128 KB image (`.mcr` or `.sav`) over the game's `.sav` file.

## Known issues

- Some games may run slower than on a real console.
- A thin diagonal line can split the white Sony screen while it fades.
- The picture can jump sideways for one frame when a game switches video
  mode.
- Stray lines can show on some screens.

## Building from source

See [BUILDING.md](BUILDING.md).

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
- Adam Gastineau (agg23) and [MIRLO64](https://github.com/tortugaveloz/MIRLO64)
  by tortuga: Pocket platform glue and audio output.

## License

The core is licensed under the [GNU GPL version 2](LICENSE). Files written
for this project are also available under the [MIT license](licenses/MIT.txt)
unless their header says otherwise. Third-party parts keep their own terms;
see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

Not affiliated with Sony Interactive Entertainment or Analogue.
"PlayStation" is a trademark of Sony Interactive Entertainment.
