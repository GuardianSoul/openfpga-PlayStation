"""PocketQuake's psram.sv as psx_pq_psram (MIRLO64's controller is also "psram").

Loads the address while idle unconditionally, adds WRITE_EXTRA (clocks
appended to each asynchronous write: only the write end state moves;
psram.sv's other states have fixed numbers, so scaling CLOCK_SPEED would
collide them) and optionally holds write data one clock past WE# rising
(psram.sv releases DQ on the same edge).
"""
from tool_paths import ROOT

SOURCE = ROOT/'references/PocketQuake/src/fpga/core/psram.sv'
EDITS = (
    ('module psram #(', 'module psx_pq_psram #('),
    ('    parameter SYNC_LATENCY = 4  // Fixed latency count; WAIT gating handles row boundary crossings only\n',
     '    parameter SYNC_LATENCY = 4,  // Fixed latency count; WAIT gating handles row boundary crossings only\n'
     '    parameter WRITE_EXTRA = 0    // psx_pq_psram: extra clocks per write (write states end below 20)\n'),
    ('  localparam STATE_WRITE_DATA_END = WRITE_INITIAL_COUNT + TOTAL_WRITE_CYCLE_COUNT;',
     '  localparam STATE_WRITE_DATA_END = WRITE_INITIAL_COUNT + TOTAL_WRITE_CYCLE_COUNT + WRITE_EXTRA;'),
    # Idle loads the address unconditionally: the bus is not driven while
    # idle (a held write's data changes on the same edge either way), and the
    # command decode leaves the I/O-cell register inputs (104 MHz timing).
    ('        cram_clk   <= 0;\n        cram_adv_n <= 1;\n',
     '        cram_clk   <= 0;\n        cram_a     <= addr[21:16];  // psx_pq_psram: unconditional\n'
     '        cram_data  <= addr[15:0];\n        cram_adv_n <= 1;\n'),
)
HOLD = (
    ('        state <= STATE_NONE;\n\n        data_out_en <= 0;\n\n        // Unlatch write enable and banks\n',
     '        state <= STATE_NONE;\n\n        // Write data stays driven until the cycle after WE# rises (hold).\n\n        // Unlatch write enable and banks\n'),
    ('      STATE_NONE: begin\n\n        cram_clk   <= 0;\n',
     '      STATE_NONE: begin\n\n        data_out_en <= 0;  // released a cycle after a write ends\n        cram_clk   <= 0;\n'),
)


def renamed_psram(hold=False):
    text = SOURCE.read_text()
    for old, new in EDITS + (HOLD if hold else ()):
        if text.count(old) != 1: raise ValueError('psram.sv anchor not found: ' + old[:60])
        text = text.replace(old, new)
    return ('// PocketQuake src/fpga/core/psram.sv as psx_pq_psram (scripts/pq_psram.py'
            + (', write data held past WE#' if hold else '') + ').\n' + text)
