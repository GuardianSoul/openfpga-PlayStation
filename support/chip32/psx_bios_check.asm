// SPDX-License-Identifier: MIT
// APF Chip32 loader: finds and checks the BIOS before the core starts.
//
// The Pocket runs this program (core.json "chip32_vm") instead of its own
// file loading when a game is picked. It
//   1. selects bitstream 0;
//   2. tries the BIOS slots in order (one fixed file name each) and loads
//      the first file that is 512 KB and carries a PlayStation ROM version
//      line ("System ROM Version 3.0 11/18/96 A"), whose last letter is
//      its region: A (USA), J (Japan) or E (Europe);
//   3. sets the CD controller's region and the BIOS region to match, as
//      MiSTer does per BIOS, so the BIOS accepts every disc;
//   4. otherwise stops with a one-line message on the Pocket's error
//      screen: "Missing BIOS scph5501.bin", or "Bad BIOS <name>" for the
//      first file it found that is not 512 KB or has no version line;
//   5. loads the memory card if its file exists, and starts the core.
// Before step 2 it writes the core's slot ID/size table (bridge 0xF8002000),
// which the firmware leaves empty under Chip32; opening a slot only works
// before the BIOS is loaded and faults for a slot with no file.
// The disc and track slots are deferred; the core reads them itself and
// finds their sizes in that table.
// A later run (a slot reload) exits at once: r13 survives between runs.
//
// scripts/build_chip32_loader.py appends the constants memcard_slot
// (0xffff: none) and send_complete (1: also raise the core's "all slots
// complete" as the firmware's command 0x0082 does), the 0-terminated word
// list bios_slots, the 0xffff-terminated list table_slots (every data.json
// slot in order) and the string msg_names (the first file name, for the
// missing message).

constant core_id = 0
constant host_init = 0x4002
constant bios_bytes = 524288
constant version_seek = 0x7ff32
constant version_bytes = 33
constant region_char = 32           // A (USA), J (Japan) or E (Europe)
constant region_reg = 0x1100009c    // core_top region: 0 USA, 1 Japan, 2 Europe
constant biosregion_reg = 0x11000020
constant flag_started = 1
constant slot_table = 0xf8002000
constant bridge_host = 0xf8000000
constant cmd_allcomplete = 0x434d0082
constant buf = 0x1c00
constant name_buf = 0x1d00

// check codes (r5); a usable file's region is in r6
constant ok = 0
constant absent = 1
constant bad_size = 2
constant not_bios = 3

// Error vector (0x0000): an exception in this program.
                jp internal_error

// Start vector (0x0002).
start:
                bit r13,#flag_started
                jp z,first_run
                exit 0

first_run:
                ld r0,#core_id
                core r0
                ld r11,#0

                // The ID/size table: one entry (ID, then size) per data.json
                // slot, size 0 for an empty slot.
                ld r9,#table_slots
                ld r8,#slot_table
table_next:
                ld.w r1,(r9)
                cmp r1,#0xffff
                jp z,table_done
                add r9,#2
                pmpw r8,r1
                add r8,#4
                ld r2,#0
                queryslot r1
                jp nz,table_size
                ld r11,#1                   // an empty slot's open faults
                open r1,r2
                jp nz,table_empty
                ld r11,#0
                close
                jp table_size
table_empty:
                ld r11,#0
                ld r2,#0
table_size:
                pmpw r8,r2
                add r8,#4
                jp table_next
table_done:
                ld r1,#send_complete
                cmp r1,#0
                jp z,table_run
                ld r1,#bridge_host
                ld r2,#cmd_allcomplete
                pmpw r1,r2
table_run:

                ld r12,#0                   // first unusable file's slot
                ld r9,#bios_slots
next_slot:
                ld.w r1,(r9)
                cmp r1,#0
                jp z,none_usable
                add r9,#2
                call check
                cmp r5,#ok
                jp z,load_bios
                cmp r5,#bad_size
                jp c,next_slot              // absent
                cmp r12,#0
                jp nz,next_slot
                ld r12,r1
                jp next_slot

load_bios:
                loadf r1
                jp nz,load_error
                ld r1,#region_reg
                pmpw r1,r6
                ld r1,#biosregion_reg
                pmpw r1,r6

                // The memory card: loaded when its file exists.
                ld r1,#memcard_slot
                cmp r1,#0xffff
                jp z,start_core
                open r1,r2
                jp nz,start_core
                close
                loadf r1
                jp nz,card_error

start_core:
                or r13,#flag_started
                ld r0,#host_init
                host r0,r0
                exit 0

// check: r1 = slot. Returns r5 (codes above); buf holds the version line.
check:
                ld r5,#absent
                open r1,r2
                ret nz
                ld r5,#bad_size
                cmp r2,#bios_bytes
                jp nz,check_close
                ld r5,#not_bios
                ld r3,#version_seek
                seek r3
                jp nz,check_close
                ld r3,#buf
                ld r4,#version_bytes
                read r3,r4
                jp nz,check_close
                ld r3,#buf+version_bytes
                ld r4,#0
                ld.b (r3),r4                // end the version line
                ld r3,#buf
                ld r4,#version_prefix
                test r3,r4                  // carry: buf starts with the prefix
                jp nc,check_close
                ld r3,#buf+region_char
                ld.b r4,(r3)
                ld r5,#ok
                ld r6,#0
                cmp r4,#0x41                // 'A'
                jp z,check_close
                ld r6,#1
                cmp r4,#0x4a                // 'J'
                jp z,check_close
                ld r6,#2
                cmp r4,#0x45                // 'E'
                jp z,check_close
                ld r5,#not_bios
check_close:
                close
                ret

none_usable:
                cmp r12,#0
                jp nz,report
                ld r14,#msg_missing
                printf r14
                ld r14,#msg_names
                jp fail

// report: what is wrong with the file in slot r12.
report:
                ld r1,r12
                ld r14,#msg_bad
                printf r14
                ld r14,#name_buf
                getname r1,r14
                jp fail

load_error:
                ld r14,#msg_load
                printf r14
                ld r14,#name_buf
                getname r1,r14
                jp fail

card_error:
                ld r14,#msg_card

fail:
                printf r14
                exit 1

internal_error:
                cmp r11,#0
                jp nz,table_empty           // the slot has no file
                ld r14,#msg_internal
                printf r14
                err r0,r1
                hex.w r1
                exit 1

version_prefix:
                db "System ROM Version ",0
// One short line each: the Pocket shows a single line in large text for
// about three seconds.
msg_missing:
                db "Missing BIOS ",0
msg_bad:
                db "Bad BIOS ",0
msg_load:
                db "Cannot load ",0
msg_card:
                db "Memory card load failed",0
msg_internal:
                db "Loader error at 0x",0
