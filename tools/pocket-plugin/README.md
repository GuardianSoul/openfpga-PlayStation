# PlayStation game files plugin

A [Pocket plugin](https://github.com/openfpga-library/pocket-plugin) for
Pocket Sync and pupdate. It generates the game files (`.json`) for the
[PlayStation core](https://github.com/GuardianSoul/openfpga-PlayStation) on
the SD card, the same way the core's `make_pocket_instances.py` script does:

- one entry per multi-disc game, with every disc in it (switch discs in
  *Core Settings → Swap Discs*) and one shared memory card,
  `Saves/playstation/common/<game>.sav`, started from the newest per-disc card;
- for cores from 0.5.3, which read the `.cue` themselves, game files that
  name the cue and disc 1's files (the core finds the other discs by
  itself), so merged and per-track discs are used as they are. From core
  0.5.4, a LibCrypt `.sbi` with the cue's name goes in the game file too.
  When a game's disc names differ by more than their number (Final Fantasy
  IX's "(Disc 3) (v1.1)" and "(USA) (Disc 4) (Rev 1)"), it writes a `.m3u`
  playlist of the discs next to them and the game file names that;
- for one-file test cores, each disc's track table (`.trk`), joining
  per-track files into `<name> (one file).bin`;
- for older cores, LibCrypt `.sbi` files next to a cue, and splitting merged
  `.bin` images into one file per track;
- a `.cue` for a `.bin` that came without one (one data track; a game with
  CD music needs its real `.cue`);
- `.chd` images, extracted into a folder next to them with the same files
  `chdman extractcd` writes.

## Use

In Pocket Sync or pupdate, add a plugin from this repository's link, put the
SD card in, open *PlayStation game files* and choose *Generate game
files*. Put your games in
`Assets/playstation/common/`, each in its own folder.

When discs need converting (CHD images, or discs an older core needs in
another layout), the plugin lists them and asks: keep the originals,
replace them, or skip them. A full SD card corrupts easily, so:

- every large file first claims its size plus 256 MB on the card, and is
  only written when that fits (FAT and exFAT have no sparse files);
- a file gets its real name only after it is written, synced and read back;
- originals are removed only after that;
- with *replace*, a disc that doesn't fit next to its originals is converted
  in the plugin's own folder on the computer first, then the originals are
  removed and the new files copied over. If that copy fails, the converted
  files stay on the computer (`PlayStation conversions/<game>`);
- if a run is stopped part-way (the updater closed, the card pulled, power
  lost), the next run first finishes any copy that was waiting on the
  computer, then removes unfinished `.part` files. Nothing under its real
  name is ever half-written.

## Build

```
rustup target add wasm32-wasip1
scripts/build.sh               # target/wasm32-wasip1/release/plugin.wasm
```

`scripts/build.sh` is `cargo build --release --locked` with the build
machine's folders removed from the paths Rust keeps in the binary.

`host/run_plugin.py` (needs `pip install extism`) runs the plugin headless
on any folder:

```
python3 host/run_plugin.py target/wasm32-wasip1/release/plugin.wasm /path/to/sd-card --answer "Generate game files"
```

The tests live in the core's repository (`tests/test_pocket_plugin.py`, with
this plugin in `tools/pocket-plugin`): they run the plugin and the script on
the same test card and compare every file, on CHD images too when `chdman`
is installed.

## Release

Pocket Sync and pupdate use the latest release of this repository, which
must carry `plugin.wasm` and `plugin.json` (the release title is the
version). Publishing a release runs `.github/workflows/release.yml`, which
builds `plugin.wasm` and attaches both files.

## How this was made

This plugin was written with AI coding assistants (Anthropic Claude). I
directed the work, made the design decisions and checked the results. The
tests compare its output byte for byte with the core's script and with
`chdman`, and its disk-space handling was tested on a full exFAT card image.
Even so, review it with that origin in mind.

## License

MIT (see [LICENSE](LICENSE)). `plugin.wasm` also contains Rust crates under
MIT, Apache-2.0, BSD-3-Clause, Zlib and Unicode terms; they are listed, with
their license texts, in [THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md)
(`python3 scripts/third_party.py` regenerates it).
