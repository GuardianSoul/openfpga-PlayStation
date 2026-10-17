# PlayStation core for Analogue Pocket

An FPGA core that runs original PlayStation games on the Analogue Pocket,
built on the MiSTer PSX core.

> **Status: early.** Most games tried so far play with sound, FMVs and CD
> music. Some may still glitch or not boot. See
> [Compatibility](#compatibility).

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
   Other common BIOS names work too (see [GAMES.md](GAMES.md#bios)). The
   console region follows the BIOS, and any BIOS plays discs from every
   region.
3. **Games:** copy each disc, with its `.cue` and all its `.bin` files, into
   its own folder in `Assets/playstation/common/`, for example
   `Assets/playstation/common/Tekken 3 (USA)/`. Keep the file names as they
   are. A disc that came as a single `.bin` without a `.cue` works too: the
   plugin writes a `.cue` for it.

   **LibCrypt discs:** some European discs (for example Ape Escape, Spyro:
   Year of the Dragon and Crash Team Racing) have LibCrypt copy protection.
   The protection key is stored on the original disc in a way `.cue`/`.bin`
   images can't hold, so these discs also need an `.sbi` file: a small file
   with that key, from [redump.org](http://redump.org/) for your disc. Name it like the `.cue` (for example `Ape Escape (Europe).sbi`)
   and put it in the same folder **before** you make the game files in step
   5. Without it the game can freeze or misbehave.
4. **Plugin:** install the [PlayStation game files plugin](https://github.com/GuardianSoul/openfpga-PlayStation-plugin)
   once in your SD card tool:
   - [pupdate](https://github.com/mattpannella/pupdate): *Pocket Extras →
     Plugins → Install from GitHub…*, enter
     `GuardianSoul/openfpga-PlayStation-plugin`.
   - [Pocket Sync](https://github.com/neil-morrison44/pocket-sync): *Plugins
     → Add Plugin*, paste
     `https://github.com/GuardianSoul/openfpga-PlayStation-plugin` and press
     *Verify & install plugin*.
5. **Game files (the way to play):** with the SD card in your computer, run
   *PlayStation game files*: in pupdate from *Pocket Extras → Plugins*, in Pocket Sync with
   the play button next to it on the *Plugins* page. It makes one game file
   (`.json`) per game, with CD music, disc swapping and one shared memory
   card for multi-disc games.
6. **Play:** put the card back in the Pocket, open **openFPGA →
   PlayStation** and pick the game.

No pupdate or Pocket Sync? The [SD card helper](https://guardiansoul.github.io/openfpga-PlayStation/)
web page makes the same game files from Chrome or Edge on a computer.

### Adding games later

1. Copy the new disc folders into `Assets/playstation/common/`, as in step 3,
   with the `.sbi` for any LibCrypt disc.
2. Run *PlayStation game files* again (or the SD card helper). It adds the
   new games and leaves your saves alone. If you add an `.sbi` to a game
   that already has a game file, run it again too, so the game file picks
   it up.
3. Pick the new game in **openFPGA → PlayStation**.

pupdate also makes game files by itself when it updates your cores, and
Pocket Sync's *Games → Instance JSON* button does the same, one per disc.
Run the plugin after either, so each multi-disc game is one entry again,
with its shared memory card.

### Without game files (fallback)

Always make game files when you can. Starting a disc directly is only a
fallback for a disc that has no game file yet, for example because you
copied it to the card without running the plugin: in the game list press
Back to reach `Assets/playstation/common/`, open the disc's folder and pick
its `.cue`. Two things can go wrong:

- Always pick the `.cue` when the disc has one, not a `.bin`. Without the
  `.cue` the core doesn't know the disc's tracks, so the game plays
  without CD music and some games stop where the music starts. Pick the
  `.bin` only for a disc that came without a `.cue`.
- Multi-disc games have no Swap Discs this way, and each disc gets its own
  memory card, so a save made on disc 1 is not there on disc 2.

If a disc doesn't start this way, make its game file as above.

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

The pad is a DualShock (**Enable DualShock**, on by default); games that
use the analog sticks switch it to analog mode themselves, or press L3 + R3 +
Up. Turn it off for a plain digital pad. A change takes effect when the game
starts, since games check the controller once. Controllers connected to the Dock keep their own
L2, R2, L3 and R3.

## Settings

Under **Core Settings** in the Pocket menu. Defaults are in bold; the Pocket
remembers your choices.

| Setting | Choices | What it does |
| --- | --- | --- |
| Swap Discs | **Disc 1**, Disc 2, Disc 3, Disc 4 | The disc in the drive, for multi-disc games (see [Changing discs](GAMES.md#changing-discs)) |
| Skip BIOS logo | On, **Off** | Starts the game without the PlayStation intro |
| Enable DualShock | **On**, Off | Makes the pad a DualShock with analog sticks, for games that use them. Takes effect when the game starts: reset or relaunch it after changing |
| L Button | **L1**, L2, L3 | What the Pocket's L sends |
| R Button | **R1**, R2, R3 | What the Pocket's R sends |
| D-Pad | **D-Pad**, Face Buttons, Left Analog Stick, Right Analog Stick | What the Pocket's D-Pad sends |
| A/B/X/Y | **Face Buttons**, D-Pad, Left Analog Stick, Right Analog Stick | What the Pocket's A, B, X and Y send |
| SELECT + L/R | None, L1/R1, **L2/R2**, L3/R3 | What Select held with L or R sends |
| SELECT + D-Pad | None, **Left Analog Stick**, Right Analog Stick, D-Pad | What Select held with the D-Pad sends |
| SELECT + A/B/X/Y | None, Face Buttons, L1/R1, L2/R2, L3/R3, Left Analog Stick, **Right Analog Stick** | What Select held with A, B, X or Y sends (the L and R pairs go on Y and A) |

High-resolution (interlaced, 480i) screens are woven into full frames
automatically; there is no setting for it.

## Memory cards

Memory card 1 is saved to the SD card, one card per game (multi-disc games
share one), in `Saves/playstation/`. A new card may show as unformatted the
first time; format it from the game's save menu.

**Quit through the Pocket menu before you turn the Pocket off.** The card
is written to the SD card when you quit, so turning off mid-game loses the
saves made since the game started. To use a card from another emulator,
copy its raw 128 KB image (`.mcr` or `.sav`) over the game's `.sav` file.

## Compatibility

Most games tried so far work. See the
[game list](https://guardiansoul.github.io/openfpga-PlayStation/compatibility.html)
and vote how your games run.

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
