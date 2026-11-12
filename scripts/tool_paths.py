"""Resolve tools from PATH or this project's optional local installs."""
from pathlib import Path
import os
import shutil

ROOT = Path(__file__).resolve().parents[1]


def find_tool(name):
    found = shutil.which(name)
    if found:
        return found
    locations = {
        'iverilog': 'build/tools/iverilog/bin/iverilog',
        'vvp': 'build/tools/iverilog/bin/vvp',
        'ghdl': 'build/tools/ghdl/ghdl-mcode-6.0.0-ubuntu24.04-x86_64/bin/ghdl',
        # Quartus Standard 25.1 in its default per-user place (ROOT / an absolute path is that path).
        'quartus_sh': str(Path.home() / 'altera/25.1std/quartus/bin/quartus_sh'),
        'tclsh': str(Path.home() / 'altera/25.1std/quartus/bin/tclsh'),
        'verilator': 'build/tools/oss-cad-suite/bin/verilator',
    }
    if name in locations:
        candidate = ROOT / locations[name]
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None
