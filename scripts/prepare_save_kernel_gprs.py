"""Own experimental hidden-only zero-base EXP1 address alias.

Reuse ordinary SW/LW decode. JR/RFE and guest COP0 behavior are unchanged.
The return uses the R3000 load delay: JR sees the previous EPC value while
its preceding LW restores k0. Outside hidden context, ordinary addresses
are unchanged. This variant replaces the capacity-failed private COP0/JR hook.
"""
import hashlib
BASE_CPU = '636c9586ff0114a7dba5e5d74679450d387e3acb117547f2723b44786c73df83'
QUALIFIED_CPU = 'a65699418f65efc9ec6ab6b1997b8b51bfd2265e5914cf1b5e993e8bc5e06988'


def once(text, before, after):
    if text.count(before) != 1:
        raise ValueError('kernel-GPR anchor changed: ' + before.strip()[:100])
    return text.replace(before, after)


def cpu_text(text):
    if hashlib.sha256(text.encode()).hexdigest() != BASE_CPU:
        raise ValueError('kernel-GPR context requires the exact selected CPU')
    text = once(text, 'ce, execute_gte_readAddr, EXEBreakpoint)',
                'ce, execute_gte_readAddr, EXEBreakpoint, exceptionTrap)')
    anchor = '      calcMemAddr             := sharedSum(31 downto 0);\n'
    text = once(text, anchor, anchor + '''      -- Private zero-base addressing in the hidden handler only.
      if exceptionTrap = '1' and decodeSource1 = 0 then
         calcMemAddr(31 downto 16) := x"BF00";
      end if;
''')
    text = once(text, '            exception            <= unsigned(ss_in(24)(9 downto 5));\n',
                '            exception            <= unsigned(ss_in(24)(9 downto 5));\n'
                "            exceptionTrap        <= '0';\n")
    text = once(text, '               exceptionTrap       <= exceptionNew5 and trapRequest and not exceptionNew1;\n',
        '''               if exceptionNew /= 0 then
                  exceptionTrap <= exceptionNew5 and trapRequest and not exceptionNew1;
               elsif opcode1 = x"42000010" then -- RFE ends hidden context
                  exceptionTrap <= '0';
               end if;
''')
    from prepare_save_debug_context import cpu_text as isolate_debug
    return isolate_debug(text)


def firmware_text(text):
    entry = '\nentry:\n        lui     $k0, 0xBF00\n'
    text = once(text, entry, '\nentry:\n'
        '        sw      $k0, 0x1068($zero)      # private EXP1 alias, page word 26\n'
        '        sw      $k1, 0x106C($zero)      # page word 27\n'
        '        lui     $k0, 0xBF00\n')
    end = '''        lw      $k1, PAGE+4*36($k0)
        nop
        jr      $k1
        .word   0x42000010              # rfe (in the delay slot)
'''
    text = once(text, end, '''        lw      $k1, PAGE+4*27($k0)
        lw      $k0, PAGE+4*36($k0)      # return EPC
        nop
        lw      $k0, 0x1068($zero)      # restore k0; JR uses load-delay old EPC
        jr      $k0
        .word   0x42000010              # rfe (in the delay slot)
''')
    return '# Kernel-GPR variant: page words 26/27 preserved via hidden EXP1 alias.\n' + text
