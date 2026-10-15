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
| PSX core (CPU, GPU, SPU, MDEC, CD, DMA, timers, pad and card devices) | [MiSTer-devel/PSX_MiSTer](https://github.com/MiSTer-devel/PSX_MiSTer) `cd17b5a`, by Robert Peip (FPGAzumSpass) and contributors | GPL-2.0 ([LICENSE](https://github.com/MiSTer-devel/PSX_MiSTer/blob/main/LICENSE)) | Unmodified VHDL plus generated, patched copies made by `scripts/prepare_*.py`. `rtl/pocket/sio_fixed.vhd`, `rtl/pocket/joypad_pad_digital.vhd` and `rtl/gpu/*` are derivatives under GPL-2.0. |
| GTE | [PS-FPGA](https://github.com/PS-FPGA/ps-fpga) / [Laxer3a](https://github.com/Laxer3a/MDEC), copyright 2019 Romain Piquois | GPL-2.0, or a commercial license from the author | Geometry engine, adapted to the MiSTer CPU by `rtl/gte/laxer_mister_adapter.sv` (GPL-2.0). |
| Memory layout, VRAM on PSRAM, memory card slot | [akoaysigod/pocket_sotn](https://github.com/akoaysigod/pocket_sotn) `b9fedc5`, by akoaysigod | GPL-2.0 | The core's memory layout is pocket_sotn's: main RAM, BIOS and SPU RAM on SDRAM at the CPU-synced clock, VRAM on both PSRAM chips. Files: `vram_psram.vhd`: VRAM on both PSRAM chips; `pocket_memcard.vhd`: memory card file transfer (`rtl/pocket/pocket_memcard_format.vhd` is an adapted copy for `--memcard-format`, GPL-2.0); `RamM10Kn.vhd`: block RAM for the memory mux (`--memorymux-m10k`). No per-file headers; the repository's GPL-2.0 `LICENSE` and README cover the Pocket wrapper code. pocket_sotn's Pocket fixes to MiSTer's `sdram.sv` showed the SDRAM timing the Pocket needs; release builds use this project's own MIT controller, `rtl/memory/psx_sdram_ctrl.sv`, instead of that file. |
| Dual-clock FIFO | copyright 2012 Stefan Kristiansson, after Richard Herveille's VGA/LCD FIFO (copyright 2001); via Laxer3a | Two-clause BSD-style ([licenses/dual-clock-fifo-BSD.txt](licenses/dual-clock-fifo-BSD.txt)) | Modified copy inside `rtl/audio/psx_audio_bridge.sv`. |
| I2S audio output | Adam Gastineau (agg23), via MIRLO64 `target/pocket/sound_i2s.sv` | MIT (header in `rtl/audio/psx_audio_i2s.sv`) | Pocket audio output. |
| Analogue Pocket Framework (APF) | Analogue Enterprises Limited, via [MIRLO64](https://github.com/tortugaveloz/MIRLO64) `platform/pocket` | APF Software License Agreement and [Pocket EULA](https://www.analogue.link/pocket-eula), stated in each file. Where they conflict with the GPL, the GPL prevails (APF license text). | `apf_top.v`, `common.v`, `io_bridge_peripheral.v`, `io_pad_controller.v`, `core_bridge_cmd.v`, `apf_constraints.sdc` (no header; part of the same APF template). |
| Pocket template glue | [agg23/openfpga-litex](https://github.com/agg23/openfpga-litex) via MIRLO64 | MIT (MIRLO64 NOTICE) | Port list and bridge glue of the generated `core_top.sv`. |
| Board settings | OpenGateware, via MIRLO64 `platform/pocket/pocket.tcl` | CC0-1.0 | Pin and I/O assignments in the generated Quartus project. |
| Intel IP | Quartus megafunction outputs (`mf_ddio_bidir_12`, `mf_datatable`, `mf_audio_pll`, `altera_pll`, `altsyncram`, `scfifo`) | Intel/Altera terms stated in each file | FPGA primitives for the Cyclone V. |

## Other files in this repository

Memory probes, alternative memory layouts, simulation models and
development tools are not built into the core. Each states its license and
origin in its header or README, and upstream projects keep their own
LICENSE and NOTICE files under `references/`.

Simulation models under `sim/` are behavioral; they contain no Intel
implementation. `sim/reference/mister_memory_models.sv` derives from MiSTer's
models (GPL-2.0).
