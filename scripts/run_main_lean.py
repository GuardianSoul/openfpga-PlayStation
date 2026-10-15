#!/usr/bin/env python3
"""Run run_mister_main_adapter.py's oracle, timing and fault cases on the
main-lean adapter (scripts/prepare_main_lean.py) instead of the source.

The runner is reused unchanged except for the adapter it compiles, its
output directory and the dma-order fault anchor, which names the copy that
main-lean removes.
"""
from pathlib import Path

from prepare_main_lean import main_lean_text
from tool_paths import ROOT


def main():
    directory = ROOT / 'build/sim/main-lean'; directory.mkdir(parents=True, exist_ok=True)
    lean = directory / 'psx_mister_main_adapter.sv'
    lean.write_text(main_lean_text((ROOT / 'rtl/pocket/psx_mister_main_adapter.sv').read_text()))
    runner = Path(__file__).with_name('run_mister_main_adapter.py')
    text = runner.read_text()
    for old, new in ((
        "adapter=ROOT/'rtl/pocket/psx_mister_main_adapter.sv'", f"adapter=Path({str(lean)!r})"), (
        "directory=ROOT/'build/sim/mister-main-adapter'", "directory=ROOT/'build/sim/main-lean'"), (
        "('assign dma_data=return_data[return_index*32+:32]','assign dma_data=return_data[(return_count-return_index)*32+:32]')",
        "(\"assign dma_data=return_zero ? 32'b0:read_data[return_index*32+:32]\",\"assign dma_data=return_zero ? 32'b0:read_data[(return_count-return_index)*32+:32]\")")):
        if text.count(old) != 1: raise SystemExit('run_mister_main_adapter.py changed near: ' + old[:50])
        text = text.replace(old, new)
    namespace = {'__name__': 'run_main_lean_adapter', '__file__': str(runner)}
    exec(compile(text, str(runner), 'exec'), namespace)
    namespace['main']()


if __name__ == '__main__': main()
