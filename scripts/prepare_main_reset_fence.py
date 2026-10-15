"""Native main-port reset fence wiring in private copies of MiSTer's top.

The pinned controllers/CPU are unchanged. The AXI cache in PS-FPGA and
Laxer's graphics memories do not implement this 1x/3x posted-memory port.
The fence repairs the measured old-fetch/new-reset-fetch ownership overlap.
"""
import re


DECLARATIONS = """
   signal main_reset_pending : std_logic := '0';
   signal main_reset_data : std_logic_vector(63 downto 0) := (others => '0');
   signal main_reset_meta : std_logic := '0';
   signal main_reset_sync : std_logic := '0';
   signal main_reset_guard : std_logic;
   signal main_reset_ready : std_logic;
   signal main_reset_read : std_logic_vector(63 downto 0);
"""

GUARD = """
   main_reset_request <= reset_intern;
   -- Assert the reply guard immediately; retain it through two 2x edges
   -- after the 1x fence releases. Only the reset controller uses this path.
   main_reset_guard <= main_reset_hold or main_reset_sync;
   main_reset_ready <= (main_reset_pending or ddr3_DOUT_READY) and not main_reset_guard;
   main_reset_read <= main_reset_data when main_reset_pending = '1' else ddr3_DOUT;
   process(clk2x)
   begin
      if rising_edge(clk2x) then
         main_reset_meta <= main_reset_hold;
         main_reset_sync <= main_reset_meta;
         if reset = '1' then
            main_reset_pending <= '0';
         else
            if main_reset_pending = '1' and main_reset_guard = '0' then
               main_reset_pending <= '0';
            end if;
            if ddr3_savestate = '1' and ddr3_DOUT_READY = '1' and main_reset_guard = '1' then
               main_reset_data <= ddr3_DOUT;
               main_reset_pending <= '1';
            end if;
         end if;
      end if;
   end process;
"""


def replace_once(text, before, after):
    if text.count(before) != 1:
        raise ValueError('Main reset fence anchor changed: ' + before[:100])
    return text.replace(before, after)


def fenced_top(text, basename):
    if basename == 'memorymux.vhd':
        # The adapter consumes old responses while the consumer is reset.
        # Stock memorymux only clears these flags on ram_done, so discarding
        # that response otherwise prevents isIdle and the reset pause grant.
        # This does not cancel the immutable job already owned by the adapter.
        return replace_once(text,
            "            state            <= IDLE;\n            region           <= region_in;",
            "            readram          <= '0';\n            writeram         <= '0';\n"
            "            state            <= IDLE;\n            region           <= region_in;")
    text, count = re.subn(r'(isPaused\s*:\s*out std_logic;)',
        r'\1\n      main_reset_hold : in std_logic;\n      main_reset_request : out std_logic;', text)
    if count != 1:
        raise ValueError('Main reset fence observation port changed')
    if basename == 'psx_mister_psx_top_resource.vhd':
        text, count = re.subn(r'(isPaused\s*=>\s*isPaused,)',
            r'\1\n        main_reset_hold => main_reset_hold,\n        main_reset_request => main_reset_request,', text)
        if count != 1:
            raise ValueError('Main reset fence wrapper map changed')
        return text
    if basename != 'psx_top.vhd':
        raise ValueError('Unsupported main reset fence top')
    marker="   signal reset_intern           : std_logic := '0';"
    text=replace_once(text, marker, marker+DECLARATIONS)
    text=replace_once(text, '   isPaused <= pausing;', '   isPaused <= pausing;\n'+GUARD)
    for name, entity in (('icpu','cpu'),('imemorymux','memorymux'),('isavestates','savestates')):
        matches=list(re.finditer(r'   '+name+r' : entity work\.'+entity+r'\n.*?\n   \);', text, re.S))
        if len(matches)!=1:
            raise ValueError('Main reset fence consumer changed: '+name)
        match=matches[0]; block=match[0]
        if name=='isavestates':
            for signal, value in (('ddr3_DOUT_READY','main_reset_ready'),('ddr3_DOUT','main_reset_read')):
                block, count=re.subn(r'('+signal+r'\s*=>\s*)'+signal+r'(\s*,)',
                    r'\1'+value+r'\2', block)
                if count!=1:
                    raise ValueError('Main reset fence header port changed: '+signal)
        else:
            block, count=re.subn(r'(reset\s*=>\s*)reset_intern(\s*,)',
                r'\1(reset_intern or main_reset_hold)\2', block)
            if count!=1:
                raise ValueError('Main reset fence reset map changed: '+name)
        text=text[:match.start()]+block+text[match.end():]
    return text
