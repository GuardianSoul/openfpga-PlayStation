# Third-party notices

The PSX Pocket core is a combined work. **The core as a whole, and every
bitstream built from this repository, is distributed under the GNU General
Public License, version 2 ([LICENSE](LICENSE)).** Files written for this
project are MIT ([licenses/MIT.txt](licenses/MIT.txt)) unless their header
says otherwise, so they can be reused anywhere. Third-party files keep the
terms below.

Upstream projects are pinned Git submodules under `references/`. Each keeps
its own LICENSE, NOTICE and per-file headers; nothing here relabels them.
Build scripts copy some upstream files into the build tree and patch them.
Those generated copies are derivatives under the same upstream license.

This project is not affiliated with, endorsed by or sponsored by Sony
Interactive Entertainment or Analogue. "PlayStation" is a trademark of Sony
Interactive Entertainment; it is used only to describe what the core
emulates. No BIOS, game or other Sony data is included.

## In the core

| Component | Upstream | License | Used for |
| --- | --- | --- | --- |
| PSX core (CPU, GPU, SPU, MDEC, CD, DMA, timers, pad and card devices) | [MiSTer-devel/PSX_MiSTer](https://github.com/MiSTer-devel/PSX_MiSTer) `cd17b5a`, by Robert Peip (FPGAzumSpass) and contributors; from 0.6.1 also later fixes by kuba-j (`08633cc`, `da3208d`, `b7a13a8`, `085448e`, `3e2b728`, `9f1a35c`, `ac69cb4`, `f35b4b3`), adapted to this core | GPL-2.0 ([LICENSE](https://github.com/MiSTer-devel/PSX_MiSTer/blob/main/LICENSE)) | Unmodified VHDL plus generated, patched copies made by `scripts/prepare_*.py`. `rtl/pocket/sio_fixed.vhd`, `rtl/pocket/joypad_pad_digital.vhd` and `rtl/gpu/*` are derivatives under GPL-2.0. |
| GTE | [PS-FPGA](https://github.com/PS-FPGA/ps-fpga) / [Laxer3a](https://github.com/Laxer3a/MDEC), copyright 2019 Romain Piquois | GPL-2.0, or a commercial license from the author | Geometry engine, adapted to the MiSTer CPU by `rtl/gte/laxer_mister_adapter.sv` (GPL-2.0). |
| MDEC (video decoder) arithmetic | [Mednafen](https://mednafen.github.io/) `psx/mdec.c`, as in [libretro/beetle-psx-libretro](https://github.com/libretro/beetle-psx-libretro) `e57860d8`, by the Mednafen team | GPL-2.0-or-later (file header; used under GPL-2.0) | From 0.5.8: the decoder's colour conversion, rounding and clamping follow Mednafen's mdec.c; from 0.6.1 its dequantisation (apart from the edge cases), zigzag order, monochrome table, wrap-and-clamp and signed output still do; the rest is bit-exact (next row) (`scripts/prepare_mdec_beetle.py` rewrites MiSTer's `mdec.vhd`; `scripts/run_mdec_beetle.py` checks the result against the unmodified C). |
| MDEC bit-exact arithmetic | kuba-j, MiSTer PSX commit `1e6494c`, based on psyz by Xeeynamo and the emu-russia MDEC die analysis | GPL-2.0 (PSX_MiSTer) | From 0.6.1: dequantisation edge cases, the inverse DCT second pass, the colour sum and 15-bit rounding, and the chroma table match a PlayStation bit for bit (`mdec-ps1-exact` parts of `mdec.vhd` and `psx_mdec_chroma_rom.vhd`). |
| Dual-clock FIFO | copyright 2012 Stefan Kristiansson, after Richard Herveille's VGA/LCD FIFO (copyright 2001); via Laxer3a | Two-clause BSD-style ([licenses/dual-clock-fifo-BSD.txt](licenses/dual-clock-fifo-BSD.txt)) | Modified copy inside `rtl/audio/psx_audio_bridge.sv`. |
| SERV RISC-V core (only with `--cue-mount`) | [olofk/serv](https://github.com/olofk/serv) `f200eb2`, copyright Olof Kindgren | ISC ([LICENSE](https://github.com/olofk/serv/blob/main/LICENSE)) | Unmodified `rtl/serv_*.v` (not `serv_compdec.v`, which is Apache-2.0 and unused) runs the cue reader firmware in `rtl/pocket/psx_cue_mount.sv`. |
| Analogizer interface (only in builds with the Analogizer option) | [RndMnkIII/Analogizer](https://github.com/RndMnkIII/Analogizer) `b9e9fb3`, by RndMnkIII | MIT ([LICENSE](https://github.com/RndMnkIII/Analogizer/blob/main/LICENSE)) | The cartridge-port pin map, the pin use of each video mode and the SNAC configuration B lines for PlayStation controllers, which `rtl/pocket/psx_analogizer.sv` follows (our own code; no Analogizer code is copied). |
| SNAC controller port (only in builds with the Analogizer option) | [MiSTer-devel/PSX_MiSTer](https://github.com/MiSTer-devel/PSX_MiSTer) `PSX.sv`, by Robert Peip (FPGAzumSpass) | GPL-2.0 | `rtl/pocket/psx_snac.sv` is the SNAC block of MiSTer's `PSX.sv` on the core's own SIO (a GPL-2.0 derivative), so a real controller or memory card on the Analogizer talks to the game. |
| I2S audio output | Adam Gastineau (agg23), via MIRLO64 `target/pocket/sound_i2s.sv` | MIT (header in `rtl/audio/psx_audio_i2s.sv`) | Pocket audio output. |
| Analogue Pocket Framework (APF) | Analogue Enterprises Limited, via [MIRLO64](https://github.com/tortugaveloz/MIRLO64) `platform/pocket` | APF Software License Agreement and [Pocket EULA](https://www.analogue.link/pocket-eula), stated in each file. Where they conflict with the GPL, the GPL prevails (APF license text). | `apf_top.v`, `common.v`, `io_bridge_peripheral.v`, `io_pad_controller.v`, `core_bridge_cmd.v`, `apf_constraints.sdc` (no header; part of the same APF template). |
| Pocket template glue | [agg23/openfpga-litex](https://github.com/agg23/openfpga-litex) via MIRLO64 | MIT (`licenses/APF-NOTICE.txt`) | Port list and bridge glue of the generated `core_top.sv`. |
| Board settings | OpenGateware, via MIRLO64 `platform/pocket/pocket.tcl` | CC0-1.0 | Pin and I/O assignments in the generated Quartus project. |
| Intel IP | Quartus megafunction outputs (`mf_ddio_bidir_12`, `mf_datatable`, `mf_audio_pll`, `altera_pll`, `altsyncram`, `scfifo`) | Intel/Altera terms stated in each file | FPGA primitives for the Cyclone V. |

Memory layout idea and memory card slot settings:
[akoaysigod/pocket_sotn](https://github.com/akoaysigod/pocket_sotn) by
akoaysigod.

## Other files in this repository

Memory probes, alternative memory layouts, simulation models and
development tools are not built into the core. Each states its license and
origin in its header or README, and upstream projects keep their own
LICENSE and NOTICE files under `references/`.

Simulation models under `sim/` are behavioral; they contain no Intel
implementation. `sim/reference/mister_memory_models.sv` derives from MiSTer's
models (GPL-2.0).

`docs/games.tsv`, the game names and disc serials behind the game list page,
comes from [libretro-database](https://github.com/libretro/libretro-database)
(built on redump.org data) and is licensed CC BY-SA 4.0
(<https://creativecommons.org/licenses/by-sa/4.0/>), stated in its header.
`scripts/make_compat_games.py` makes it from that project's
`metadat/redump/Sony - PlayStation.dat`.

`docs/fonts/` holds the [Inter](https://github.com/rsms/inter) typeface used by the web
pages, © The Inter Project Authors, under the SIL Open Font License 1.1
(`docs/fonts/Inter-OFL.txt`).
