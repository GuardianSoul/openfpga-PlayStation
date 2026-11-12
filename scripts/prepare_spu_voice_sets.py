#!/usr/bin/env python3
"""SPU RAM cache: a voice's reads use its voice number as the cache set.

The slim cache (--spu-cache --spu-cache-slim) picks a set by hashing the
address. spu.vhd reads every voice's block header and data each tick, so two
voices whose blocks hash to one set evict each other every tick; with several
such pairs the SPU passes its 768-clk1x budget and drops ticks (music stutter,
issue #24; sim/reference/tb_spu_budget.vhd). With VOICE_SETS=1
(rtl/pocket/psx_spu_ch3_cache_slim.sv) voice v uses set v, other reads set 31,
and every write clears the cache. Data are unchanged: the cache returns what
SPU RAM holds (make test-spu-ch3-cache), only the timing moves.
This step carries the voice number from spu.vhd (sdram_voice/sdram_isvoice)
through psx_top, the wrapper, core_top and psx_core_memory to the cache.
--write-owners installs VOICE_SETS=2 instead: the same sets, but a write
clears only the sets that may hold its block (an owner table in one M10K), so
a game that writes SPU RAM while it plays keeps its cache (docs/spu-voice-sets.md).
It also upgrades a project that already has this step.
--post-write (implies --write-owners) adds POST_WRITE=1: a write is answered
one clock after it is taken and ch3 finishes it while the SPU goes on.
Usage: prepare_spu_voice_sets.py PROJECT [--write-owners] [--post-write]   (after the release recipe's steps)
"""
from pathlib import Path
import re
import subprocess
import sys

MARK = 'spu-voice-sets'
ROOT = Path(__file__).resolve().parents[1]


def once(text, old, new, what):
    if text.count(old) != 1: raise ValueError(f'{MARK}: {what} anchor found {text.count(old)} times')
    return text.replace(old, new)


def spu_text(t):
    t = once(t, "      sdram_ena            : out std_logic;\n",
             "      sdram_ena            : out std_logic;\n"
             f"      sdram_voice          : out std_logic_vector(4 downto 0);   -- {MARK}: the request's voice\n"
             f"      sdram_isvoice        : out std_logic;                      -- {MARK}: a voice read, with sdram_ena\n", 'spu port')
    t = once(t, "\nbegin\n\n   -- spu-cd-queue\n",
             "\nbegin\n\n"
             f"   sdram_voice   <= std_logic_vector(to_unsigned(index, 5));   -- {MARK}\n"
             f"   sdram_isvoice <= ram_isVoice;                               -- {MARK}\n"
             "   -- spu-cd-queue\n", 'spu body')
    return t


def psx_top_text(t):
    t = once(t, "      spuram_ena            : out std_logic;\n",
             "      spuram_ena            : out std_logic;\n"
             f"      spuram_voice          : out std_logic_vector(4 downto 0);   -- {MARK}\n"
             f"      spuram_isvoice        : out std_logic;                      -- {MARK}\n", 'psx_top port')
    t = once(t, "      sdram_ena            => spuram_ena,           \n",
             "      sdram_ena            => spuram_ena,           \n"
             f"      sdram_voice          => spuram_voice,   -- {MARK}\n"
             f"      sdram_isvoice        => spuram_isvoice,   -- {MARK}\n", 'spu map')
    return t


def wrapper_text(t):
    t = once(t, "    spuram_ena : out std_logic;\n",
             "    spuram_ena : out std_logic;\n"
             f"    spuram_voice : out std_logic_vector(4 downto 0);   -- {MARK}\n"
             f"    spuram_isvoice : out std_logic;   -- {MARK}\n", 'wrapper port')
    t = once(t, "        spuram_ena => spuram_ena,\n",
             "        spuram_ena => spuram_ena,\n"
             f"        spuram_voice => spuram_voice,   -- {MARK}\n"
             f"        spuram_isvoice => spuram_isvoice,   -- {MARK}\n", 'wrapper map')
    return t


def core_text(t):
    t = once(t, "    wire spuram_ena;\n",
             f"    wire spuram_ena;\n    wire [4:0] spuram_voice; wire spuram_isvoice;   // {MARK}\n", 'core wires')
    t = once(t, "\n.spuram_ena(spuram_ena),\n",
             f"\n.spuram_ena(spuram_ena),\n.spuram_voice(spuram_voice),.spuram_isvoice(spuram_isvoice),   // {MARK}\n", 'psx map')
    t = once(t, ".spuram_ena(spuram_ena),.spuram_rnw(spuram_rnw),",
             ".spuram_ena(spuram_ena),.spuram_voice(spuram_voice),.spuram_isvoice(spuram_isvoice),.spuram_rnw(spuram_rnw),", 'memory map')
    return t


def memory_text(t):
    t = once(t, "    input wire spuram_ena, spuram_rnw,\n",
             f"    input wire spuram_ena, spuram_rnw,\n    input wire [4:0] spuram_voice, input wire spuram_isvoice,   // {MARK}\n", 'memory port')
    t = once(t, "    psx_spu_ch3_cache spu_cache (", f"    psx_spu_ch3_cache #(.VOICE_SETS(1)) spu_cache (   // {MARK}\n       ", 'cache instance')
    t = once(t, ".spu_req(spuram_ena && !cpu_reset), .spu_rnw(spuram_rnw),",
             ".spu_req(spuram_ena && !cpu_reset), .spu_rnw(spuram_rnw), .spu_voice(spuram_voice), .spu_isvoice(spuram_isvoice),", 'cache ports')
    return t


# rtl/pocket/psx_spu_ch3_cache_slim.sv before VOICE_SETS, with VOICE_SETS 0/1 only (the 0.5.7 and 0.5.8 projects),
# and with VOICE_SETS=2 before POST_WRITE
SLIM_BEFORE = ('e09c92f', '533c49b1', '8a6e9deb')


def cache_text(t):
    old = [subprocess.run(['git', '-C', str(ROOT), 'show', f'{rev}:rtl/pocket/psx_spu_ch3_cache_slim.sv'],
                          capture_output=True, text=True, check=True).stdout for rev in SLIM_BEFORE]
    new = (ROOT/'rtl/pocket/psx_spu_ch3_cache_slim.sv').read_text()
    if t not in (*old, new): raise ValueError(f'{MARK}: the project cache is not the slim cache of {SLIM_BEFORE} or this checkout')
    return new


FILES = (('spu.vhd', spu_text), ('psx_top.vhd', psx_top_text), ('psx_mister_psx_top_resource.vhd', wrapper_text),
         ('core_top.sv', core_text), ('psx_core_memory.sv', memory_text), ('psx_spu_ch3_cache.sv', cache_text))


def assigned(q, name):
    hits = sorted(set(re.findall(rf'"([^"]*/{re.escape(name)})"', q)))
    if len(hits) != 1: raise ValueError(f'{MARK}: expected one {name} in the project, found {hits}')
    return Path(hits[0])


def prepare_project(project, write_owners=False, post_write=False):
    project = Path(project).resolve(); qsf = project/'apf_top.qsf'; q = qsf.read_text()
    out = project/MARK; out.mkdir(exist_ok=True)
    if MARK not in assigned(q, 'psx_core_memory.sv').read_text():
        for name, edit in FILES:
            hit = assigned(q, name)
            (out/name).write_text(edit(hit.read_text()))
            q = q.replace(f'"{hit}"', f'"{out/name}"')
        qsf.write_text(q)
    elif not (write_owners or post_write): raise ValueError(f'{MARK}: the project already has this step')
    if write_owners or post_write:
        # in place: after the step above these are the project's own copies
        memory, cache = assigned(q, 'psx_core_memory.sv'), assigned(q, 'psx_spu_ch3_cache.sv')
        if project not in memory.parents or project not in cache.parents: raise ValueError(f'{MARK}: the cache files are not the project\'s own')
        text = memory.read_text()
        hits = re.findall(r'psx_spu_ch3_cache #\(\.VOICE_SETS\([12]\)(?:, \.POST_WRITE\(1\))?\) spu_cache \(', text)
        if len(hits) != 1: raise ValueError(f'{MARK}: cache parameter anchor found {len(hits)} times')
        memory.write_text(text.replace(hits[0], 'psx_spu_ch3_cache #(.VOICE_SETS(2)' + (', .POST_WRITE(1)' if post_write else '') + ') spu_cache ('))
        cache.write_text(cache_text(cache.read_text()))
    return out


if __name__ == '__main__':
    flags = ('--write-owners', '--post-write')
    args = [a for a in sys.argv[1:] if a not in flags]
    if len(args) != 1: raise SystemExit(__doc__.split('Usage: ')[1])
    print(prepare_project(args[0], write_owners='--write-owners' in sys.argv, post_write='--post-write' in sys.argv))
