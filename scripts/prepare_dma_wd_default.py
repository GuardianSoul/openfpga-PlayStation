"""dma.vhd: channel-table write data defaults to the CPU's word (post-prepare step).

The DMA process starts each clock with madr/bcr/chcr write enables off and
their write data at zero; the tables are written only when an enable is set,
and every site that sets one also sets the data. So the zero is never written:
defaulting the data to the CPU write path's value instead leaves every table
write unchanged and removes one source from each data selection.
make test-dma-wd-default compares the DMA against the one without it.
Usage: prepare_dma_wd_default.py PROJECT_OR_dma.vhd
"""
import sys
from pathlib import Path

OLD = ("         madr_we := '0'; madr_wa := 0; madr_wd := (others => '0');\n"
       "         bcr_we  := '0'; bcr_wa  := 0; bcr_wd  := (others => '0');\n")
NEW = ("         -- dma-wd-default: data only matters with its enable; default to the CPU word\n"
       "         madr_we := '0'; madr_wa := 0; madr_wd := unsigned(bus_dataWrite(23 downto 0));\n"
       "         bcr_we  := '0'; bcr_wa  := 0; bcr_wd  := unsigned(bus_dataWrite);\n")
OLD2 = "         chcr_we := '0'; chcr_wa := 0; chcr_wd := (others => '0');\n"
NEW2 = "         chcr_we := '0'; chcr_wa := 0; chcr_wd := chcrPack(unsigned(bus_dataWrite));\n"


def wd_default_text(text):
    for old, new in ((OLD, NEW), (OLD2, NEW2)):
        if text.count(old) != 1: raise ValueError('dma-wd-default: anchor changed: ' + old.strip().splitlines()[0])
        text = text.replace(old, new)
    return text


def target(path):
    path = Path(path)
    if path.is_file(): return path
    hits = {l.split('"')[1] for l in (path/'apf_top.qsf').read_text().splitlines() if '/dma.vhd"' in l}
    if len(hits) != 1: raise SystemExit(f'{path}: expected one assigned dma.vhd, found {sorted(hits)}')
    found = Path(hits.pop())
    if not found.resolve().is_relative_to(path.resolve()): raise SystemExit(f'{found}: not a project copy')
    return found


if __name__ == '__main__':
    p = target(sys.argv[1]); p.write_text(wd_default_text(p.read_text())); print(p)
