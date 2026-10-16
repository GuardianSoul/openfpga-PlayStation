# Building

## Tools

- Quartus Prime Lite 25.1 with Cyclone V device support
- Python 3.10 or newer, with Pillow for the release images
- For tests: Icarus Verilog 12, GHDL and Verilator

`scripts/setup_iverilog.sh` and `scripts/setup_ghdl.py` can install pinned
Icarus Verilog and GHDL builds under `build/tools/`.

## Get the source

```bash
git clone --recurse-submodules https://github.com/GuardianSoul/openfpga-PlayStation.git
```

For an existing checkout, run `git submodule update --init --recursive`.
The upstream cores under `references/` are pinned submodules. The build
copies and patches some of their files under `build/`; it does not change
the submodules.

## Test

```bash
make test
```

This runs the Python checks and the RTL simulations. No BIOS or game image
is needed.

## Rebuild a release

Every release comes with `GuardianSoul.PlayStation_<version>_<date>_source.tar.gz`: the exact
Quartus project its bitstream was built from, with all its sources. Open
`project/apf_top.qpf` in Quartus Prime Lite 25.1 and compile. The fitter
seed is already set in `apf_top.qsf`.

## Make a release

The scripts in `scripts/` generate the Quartus project (`prepare_full_pocket.py`,
then `prepare_pocket_early.py`), and `package_pocket_early.py` packages a fitted
build. A fitted and packaged build then becomes a release with:

```bash
make release RC_PROJECT=<fitted project> RC_PACKAGE=<package folder> VERSION=0.1.0
```

This checks timing, then writes `build/release/package/GuardianSoul.PlayStation_<version>_<date>.zip`
and `build/release/source/GuardianSoul.PlayStation_<version>_<date>_source.tar.gz`. The timing
check passes only when every path within one clock meets timing. A miss
between two clocks needs an entry in `support/release_timing_waivers.json`
that says why it is safe.

Generating the Quartus project takes several `prepare_*.py` runs with many
options, and is not yet a single command from a fresh checkout. To rebuild a
released core exactly, use its source archive (see above).

## Repository layout

| Path | Contents |
| --- | --- |
| `rtl/` | Pocket platform, memory, audio and adapter logic written for this core |
| `scripts/` | Project generators, patch scripts, simulation and packaging tools |
| `sim/` | Testbenches and behavioral models |
| `tests/` | Python unit tests |
| `docs/` | The SD card helper page (served with GitHub Pages from `main`, folder `/docs`) |
| `support/` | APF data-slot and source descriptions |
| `references/` | Pinned upstream projects (submodules) |
| `licenses/` | License texts for project and third-party code |

## Contributing

Pull requests and issues are welcome. Keep upstream files unmodified under
`references/`; changes to upstream logic belong in a `scripts/prepare_*.py`
patch with a simulation check. New files should carry an SPDX license line.
