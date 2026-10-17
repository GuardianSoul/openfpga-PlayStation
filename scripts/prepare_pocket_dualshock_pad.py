"""Edits for prepare_pocket_early --dualshock: pad 1 becomes a DualShock.

The release base fixes pad 1 as a digital pad in the PSX wrapper
(PadPortDigital='1', sticks zero). These edits make PadPortDS/PadPortDigital
follow the menu checkbox and feed the four stick axes from
rtl/pocket/psx_pocket_analog.sv. The controller protocol itself is the pinned
MiSTer joypad_pad.vhd (left in the project instead of joypad_pad_digital.vhd),
including its analog-mode switch L3+R3+Up (on) / L3+R3+Down (off) and the
games' own 0x43/0x44 configuration commands. Pads 2-4 stay as they are.
"""

WRAPPER_PORTS = ('    joypad1_KeyL3 : in std_logic;\n',
                 '    joypad1_KeyL3 : in std_logic;\n'
                 '    pad_dualshock : in std_logic;\n'
                 '    pad_sticks : in std_logic_vector(31 downto 0);\n')

WRAPPER_FIELDS = (
    ("    joypad1.PadPortDigital <= '1';\n", "    joypad1.PadPortDigital <= not pad_dualshock;\n"),
    ("    joypad1.PadPortDS <= '0';\n", "    joypad1.PadPortDS <= pad_dualshock;\n"),
    # APF bytes are 0x00..0xFF with 0x80 centred; MiSTer's axes are signed
    # (joypad_pad sends Analog+128), so flipping bit 7 is the exact conversion.
    ("    joypad1.Analog1X <= (others=>'0');\n", "    joypad1.Analog1X <= signed((not pad_sticks(7)) & pad_sticks(6 downto 0));\n"),
    ("    joypad1.Analog1Y <= (others=>'0');\n", "    joypad1.Analog1Y <= signed((not pad_sticks(15)) & pad_sticks(14 downto 8));\n"),
    ("    joypad1.Analog2X <= (others=>'0');\n", "    joypad1.Analog2X <= signed((not pad_sticks(23)) & pad_sticks(22 downto 16));\n"),
    ("    joypad1.Analog2Y <= (others=>'0');\n", "    joypad1.Analog2Y <= signed((not pad_sticks(31)) & pad_sticks(30 downto 24));\n"),
)


def dualshock_wrapper_text(text):
    """The PSX wrapper with pad 1's DualShock mode and sticks as ports."""
    for old, new in (WRAPPER_PORTS,) + WRAPPER_FIELDS:
        if text.count(old) != 1: raise ValueError('PSX wrapper anchor not found: ' + old.strip())
        text = text.replace(old, new)
    return text


DUALSHOCK_TOP = (
    ('    psx_pocket_pad pad_input (',
     '''    // "DualShock" menu checkbox (interact.json, bridge 0x50000040; on by
    // default and until the Pocket writes the saved value). Off: digital pad.
    reg dualshock_setting=1'b1;
    always @(posedge clk_74a) if (bridge_wr && bridge_addr==32'h50000040) dualshock_setting<=|bridge_wr_data;
    wire pad_dualshock; wire [31:0] pad_sticks;
    psx_pocket_analog pad_analog (.clk1x(clk1x),.reset_n(cpu_locked),.clk_74a(clk_74a),.dualshock_setting(dualshock_setting),
        .apf_type(cont1_key[31:28]),.apf_joy(cont1_joy),.dualshock(pad_dualshock),.sticks(pad_sticks));
    psx_pocket_pad pad_input ('''),
    ('.joypad1_KeyL3(joypad1_KeyL3),', '.joypad1_KeyL3(joypad1_KeyL3),\n.pad_dualshock(pad_dualshock),\n.pad_sticks(pad_sticks),'),
)

# Held payload: req/ack toggles cross through two-flop synchronizers (first
# stages cut); hold is stable from req's toggle until ack returns, so its
# paths into clk1x are bounded to one clk_74a period like psx_cd_stream's.
DUALSHOCK_SDC = ('set_false_path -to [get_registers {*|pad_analog|req_s[0] *|pad_analog|ack_s[0]}]\n'
                 'set_max_delay 13 -from [get_registers {*|pad_analog|hold[*]}]\n'
                 'set_min_delay -13 -from [get_registers {*|pad_analog|hold[*]}]\n')
