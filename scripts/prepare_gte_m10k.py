"""GTE compute-path tables in block RAM read on the falling clk2x edge.

gte-matrix-ram, gte-add-table and gte-vector-ram keep the rotation/light/
colour matrices (three 16 x 16 tables), the TR/BK/FC/offset vectors (16 x
32) and V0-V2 (three 4 x 16 tables) in asynchronous-read MLABs, 64 ALMs of
logic area in a full fit. The engine runs on clk2x with a clock enable that
is high on alternate edges only (clk2xIndex), and every table address
(instruction parameters, isMVMVA, the registered microcode control) and
every table write changes only on enabled edges. A block RAM that samples
the address on the falling clk2x edge after an enabled edge therefore holds
exactly the word the MLAB read showed until the next enabled edge, where the
engine registers it; a write on an enabled edge is visible to that falling-
edge read, as it was to the asynchronous read. The valid bits stay in
flip-flops. Requires the three table variants and gte-multicycle (strict
alternate-edge enable); pocket.sdc gives the RAM-to-engine paths the same
two-cycle budget as the engine's own paths. No cycle changes.
"""
import re

TABLES = (('matRam1', 'matAddr1', 15), ('matRam2', 'matAddr2', 15), ('matRam3', 'matAddr3', 15),
          ('addRam', 'addAddr', 31), ('vecRam1', 'vecSel1', 15), ('vecRam2', 'vecSel2', 15), ('vecRam3', 'vecSel3', 15))


def gte_m10k_text(text):
    for table, _, _ in TABLES:
        text, n = re.subn(r'\(\* ramstyle = "MLAB, no_rw_check" \*\) (reg \[\d+:0\] ' + table + r' )',
                          r'(* ramstyle = "M10K, no_rw_check" *) \1', text)
        if n != 1: raise ValueError('gte-m10k: table not found (needs gte-matrix-ram, gte-add-table, gte-vector-ram): ' + table)
    reads = ['// Falling-edge block RAM reads (gte-m10k): the addresses only change on\n',
             '// enabled (alternate) clk2x edges, so each read holds until the next one.\n']
    for table, address, top in TABLES:
        old = f'? {table}[{address}] :'
        if text.count(old) != 1: raise ValueError('gte-m10k: read of ' + table + ' not found')
        text = text.replace(old, f'? {table}_q :')
        reads.append(f'reg [{top}:0] {table}_q;\nalways @(negedge i_clk) {table}_q <= {table}[{address}];\n')
    anchor = 'wire signed [43:0]	outAddSel;\n'
    if text.count(anchor) != 1: raise ValueError('gte-m10k: compute-path anchor not found')
    return text.replace(anchor, ''.join(reads) + '\n' + anchor)


SDC = '''# gte-m10k: compute-path tables are read on the falling clk2x edge after an
# enabled edge and consumed at the next enabled edge (two edges after the
# address changed), the engine's own multicycle budget.
set_multicycle_path -setup 2 -from [get_keepers {*|gte:igte|psx_laxer_mister_adapter:engine|PocketGTEEngine:engine|PocketGTEComputePath:*|*ram_block*}] -to [get_registers {*|gte:igte|psx_laxer_mister_adapter:engine|PocketGTEEngine:engine|*}]
set_multicycle_path -hold 1 -from [get_keepers {*|gte:igte|psx_laxer_mister_adapter:engine|PocketGTEEngine:engine|PocketGTEComputePath:*|*ram_block*}] -to [get_registers {*|gte:igte|psx_laxer_mister_adapter:engine|PocketGTEEngine:engine|*}]
'''
