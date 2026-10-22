"""Edits for prepare_pocket_early --pad2: player 2 is dock controller 2.

Pad 2 is a DualShock with all its buttons and sticks, read from the APF
cont2_* inputs. There is no combo layer (a docked controller has L2/R2/L3/R3
and sticks of its own). The port is connected only while the Pocket reports a
controller 2 (cont2_key[31:28] /= 0), so games see an empty port otherwise.
It starts in digital mode; L3+R3+Up/Down, the games' 0x43/0x44 commands and,
with --analog-setting, the same menu Digital / Analog level as pad 1 switch it.

The pad entity keeps each port's DualShock state as upstream does (make
test-joypad-dualshock-lean and test-joypad-pad-table run pad 2 as a
DualShock in lockstep with the pinned MiSTer joypad_pad).
"""

WRAPPER_PORTS = ('    pad_sticks : in std_logic_vector(31 downto 0);\n',
                 '    pad_sticks : in std_logic_vector(31 downto 0);\n'
                 '    pad2_buttons : in std_logic_vector(15 downto 0);\n'
                 '    pad2_present : in std_logic;\n'
                 '    pad2_sticks : in std_logic_vector(31 downto 0);\n')

# APF key bits: 0 up, 1 down, 2 left, 3 right, 4 A, 5 B, 6 X, 7 Y, 8 L1, 9 R1,
# 10 L2, 11 R2, 12 L3, 13 R3, 14 select, 15 start; faces by position as pad 1
# (X top = Triangle, B bottom = Cross, Y left = Square, A right = Circle).
KEYS = (('KeyUp', 0), ('KeyDown', 1), ('KeyLeft', 2), ('KeyRight', 3), ('KeyCircle', 4), ('KeyCross', 5),
        ('KeyTriangle', 6), ('KeySquare', 7), ('KeyL1', 8), ('KeyR1', 9), ('KeyL2', 10), ('KeyR2', 11),
        ('KeyL3', 12), ('KeyR3', 13), ('KeySelect', 14), ('KeyStart', 15))


def wrapper_fields(analog_setting):
    fields = [("    joypad2.PadPortEnable <= '0';\n", "    joypad2.PadPortEnable <= pad2_present;   -- --pad2: dock controller 2\n"),
              ("    joypad2.PadPortDS <= '0';\n", "    joypad2.PadPortDS <= '1';\n")]
    if analog_setting:
        fields.append(("    joypad2.ToggleDS <= '0';\n", "    joypad2.ToggleDS <= pad_dualshock;   -- --analog-setting: the menu level, as pad 1\n"))
    fields += [(f"    joypad2.{name} <= '0';\n", f"    joypad2.{name} <= pad2_buttons({bit});\n") for name, bit in KEYS]
    # APF bytes are 0x00..0xFF with 0x80 centred; MiSTer's axes are signed.
    for name, low in (('Analog1X', 0), ('Analog1Y', 8), ('Analog2X', 16), ('Analog2Y', 24)):
        fields.append((f"    joypad2.{name} <= (others=>'0');\n",
                       f"    joypad2.{name} <= signed((not pad2_sticks({low + 7})) & pad2_sticks({low + 6} downto {low}));\n"))
    return fields


def wrapper_text(text, analog_setting):
    for old, new in [WRAPPER_PORTS] + wrapper_fields(analog_setting):
        if text.count(old) != 1: raise ValueError('PSX wrapper anchor not found: ' + old.strip())
        text = text.replace(old, new)
    return text


TOP_ANCHOR = '    psx_pocket_pad pad_input ('
TOP_BLOCK = '''    // --pad2: player 2 is dock controller 2. Its presence (APF type /= 0)
    // crosses in the same held packet as its sticks.
    wire [15:0] pad2_buttons; wire pad2_present; wire [31:0] pad2_sticks;
    psx_pocket_analog pad2_analog (.clk1x(clk1x),.reset_n(cpu_locked),.clk_74a(clk_74a),.dualshock_setting(cont2_key[31:28]!=4'h0),
        .apf_type(cont2_key[31:28]),.apf_joy(cont2_joy),.config_in(13'h0),.dualshock(pad2_present),.sticks(pad2_sticks),.config_out());
    psx_pocket_pad pad2_input (.clk1x(clk1x),.reset_n(cpu_locked),.apf_keys(cont2_key[15:0]),.combo_enable(6'b0),.dpad_combo_enable(1'b0),
        .buttons(pad2_buttons),.combos(),.dpad_combos(),.docked());
'''
TOP_PORTS = ('\n.pad_dualshock(pad_dualshock),\n', '\n.pad_dualshock(pad_dualshock),\n.pad2_buttons(pad2_buttons),\n.pad2_present(pad2_present),\n.pad2_sticks(pad2_sticks),\n')


def top_text(text):
    for old, new in ((TOP_ANCHOR, TOP_BLOCK + TOP_ANCHOR), TOP_PORTS):
        if text.count(old) != 1: raise ValueError('core_top anchor not found: ' + old.strip()[:60])
        text = text.replace(old, new)
    return text


# As pad 1's key synchronizer and DUALSHOCK_SDC packet.
SDC = ('set_false_path -to [get_registers {*|pad2_input|keys_meta[*]}]\n'
       'set_false_path -to [get_registers {*|pad2_analog|req_s[0] *|pad2_analog|ack_s[0]}]\n'
       'set_max_delay 13 -from [get_registers {*|pad2_analog|hold[*]}]\n'
       'set_min_delay -13 -from [get_registers {*|pad2_analog|hold[*]}]\n')
