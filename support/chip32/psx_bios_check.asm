// SPDX-License-Identifier: MIT
// APF Chip32 loader: finds and checks the BIOS before the core starts.
//
// The Pocket runs this program (core.json "chip32_vm") instead of its own
// file loading when a game is picked. It
//   1. selects bitstream 0;
//   2. reads the disc's region from its licence text (sector 4 of the
//      disc image: "Sony Computer Entertainment Amer", "Inc." or "Euro");
//   3. tries the BIOS slots (one fixed file name each) and loads the first
//      file that is 512 KB and carries a PlayStation ROM version line
//      ("System ROM Version 3.0 11/18/96 A"), whose last letter is its
//      region: A (USA), J (Japan) or E (Europe). A BIOS of the disc's
//      region goes first (its names are tried first); without one, the
//      first usable BIOS in slot order is loaded;
//   4. sets the CD controller's region to match, as MiSTer does per BIOS,
//      so the BIOS accepts every disc; the BIOS bank select stays 0, since
//      the BIOS writer always fills bank 0 (0x800000) whatever the region;
//      and gives the GPU its PAL video mode (isPal, bridge 0x11000154) so a
//      European BIOS drives its own 50 Hz screen with all its lines;
//   5. otherwise stops with a one-line message on the Pocket's error
//      screen: "Missing BIOS scph5501.bin", or "Bad BIOS <name>" for the
//      first file in slot order that is not 512 KB or has no version line;
//   6. loads the memory card if its file exists, and starts the core.
// Step 2 is part of writing the core's slot ID/size table (bridge
// 0xF8002000), which the firmware leaves empty under Chip32; opening a slot
// only works before the BIOS is loaded and faults for a slot with no file.
// The disc and track slots are deferred; the core reads them itself and
// finds their sizes in that table.
// The Pocket draws its loading bar only while loadf runs, and every SD card
// open before the BIOS load delays it, so each file is opened at most once:
// the table pass skips the BIOS slots (the BIOS passes write the sizes of
// those they open), reads the licence while the disc is open and keeps the
// card's size for step 6. It also hands the cue reader (psx_cue_mount) the
// sizes of the launch, cue, .sbi, disc, track table and cheat slots, which
// its firmware would otherwise measure with dozens of APF reads.
// A later run (a slot reload) exits at once: r13 survives between runs.
//
// scripts/build_chip32_loader.py appends the constants memcard_slot
// (0xffff: none), disc_slot (the disc image, 0xffff: none), cue_slot (a game file's cue, 0xffff: none) and send_complete (1: also raise the core's "all slots
// complete" as the firmware's command 0x0082 does), the 0-terminated word
// list bios_slots, the word list bios_expect (each BIOS name's region from
// its last digit, 0xff for another name), the 0xffff-terminated list table_slots (every data.json
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
constant ispal_reg = 0x11000154    // core_top isPal: MiSTer's GPU PAL video mode at reset
constant flag_started = 1
constant slot_table = 0xf8002000
constant bridge_host = 0xf8000000
constant cmd_allcomplete = 0x434d0082
constant buf = 0x1c00
constant name_buf = 0x1d00
constant licence_seek = 9492        // sector 4 (2352-byte raw) data + 60
constant no_region = 0xff
constant bios_first = 0x20          // BIOS slots 0x20-0x2f (BIOS_SLOT_MASK)
constant result = 0x1e00            // one byte per BIOS slot: 0 untried, else its check code, 0x10 | region when usable
constant entry = 0x1e10             // one word per BIOS slot: its table size word (from slot_table), 0 for none
constant card_size = 0x1e30         // the memory card's size from the table pass, 0xffffffff when not in it
constant size_block = 0x2c0001c0    // psx_cue_mount RAM 0x21C0: "SIZE", slots 0, 99, 100, 101, 102, 98
constant size_magic = 0x53495a45
constant size_slot0 = 0x2c0001c4    // (named: bass encodes #size_block+n as a 16-bit immediate)
constant size_slot99 = 0x2c0001c8
constant size_slot100 = 0x2c0001cc
constant size_slot101 = 0x2c0001d0
constant size_slot102 = 0x2c0001d4
constant size_slot98 = 0x2c0001d8

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
                // Clear the per-BIOS results and table offsets; the card's
                // size is unknown until the table pass meets its slot.
                ld r3,#result
                ld r4,#0
clear_next:
                ld.b (r3),r4
                add r3,#1
                cmp r3,#card_size
                jp nz,clear_next
                ld r4,#0xffffffff
                ld.l (card_size),r4
                ld r7,#no_region

                // The ID/size table: one entry (ID, then size) per data.json
                // slot, size 0 for an empty slot. A BIOS slot is not opened
                // here: the BIOS pass opens the ones it tries and writes
                // their sizes (r10: this entry's size word, from slot_table).
                // The disc image's licence is read while it is open.
                ld r9,#table_slots
                ld r8,#slot_table
                ld r10,#4
table_next:
                ld.w r1,(r9)
                cmp r1,#0xffff
                jp z,table_done
                add r9,#2
                pmpw r8,r1
                add r8,#4
                ld r2,#0
                ld r3,r1
                sub r3,#bios_first
                cmp r3,#16
                jp nc,table_open
                asl r3,#1
                add r3,#entry
                ld.w (r3),r10
                jp table_size
table_open:
                queryslot r1
                jp nz,table_size
                ld r11,#1                   // an empty slot's open faults
                open r1,r2
                jp nz,table_empty
                ld r11,#0
                cmp r1,#disc_slot
                jp nz,table_close
                call licence
table_close:
                close
                jp table_size
table_empty:
                ld r11,#0
                ld r2,#0
table_size:
                pmpw r8,r2
                call fw_size
                add r8,#4
                add r10,#8
                cmp r1,#memcard_slot
                jp nz,table_next
                ld.l (card_size),r2
                jp table_next
table_done:
                ld r1,#size_block
                ld r2,#size_magic
                pmpw r1,r2

                // No licence text read (a game file's disc is its cue, whose
                // track files only the core opens): take the region from the
                // cue's or the disc's file name instead ("(Europe)", "(Japan)").
                cmp r7,#no_region
                jp nz,region_done
                ld r1,#cue_slot
                call name_region
                cmp r7,#no_region
                jp nz,region_done
                ld r1,#disc_slot
                call name_region
region_done:

                // BIOS passes. Every BIOS slot is opened at most once (its
                // result kept in result), so the BIOS load, and the Pocket's
                // loading bar with it, starts after as few SD card opens as
                // possible. With the disc's region known, the first pass
                // tries only the names of that region (bios_expect) and
                // loads the first one whose version line agrees; the second
                // tries the rest in slot order, loading one of the disc's
                // region, or with no region the first usable file.
                ld r10,#0
                cmp r7,#no_region
                jp z,bios_pass
                ld r10,#1
bios_pass:
                ld r9,#bios_slots
                ld r14,#bios_expect
bios_next:
                ld.w r1,(r9)
                cmp r1,#0
                jp z,bios_done
                add r9,#2
                ld.w r4,(r14)
                add r14,#2
                cmp r10,#0
                jp z,bios_try
                cmp r4,r7
                jp nz,bios_next
bios_try:
                ld r3,r1
                sub r3,#bios_first
                add r3,#result
                ld.b r4,(r3)
                cmp r4,#0
                jp nz,bios_next             // tried in the first pass
                ld r5,#absent
                queryslot r1
                jp nz,bios_store
                ld r11,#3                   // an empty BIOS slot's open faults
                open r1,r2
                jp nz,bios_none
                ld r11,#0
                ld r3,r1                    // its size into the table
                sub r3,#bios_first
                asl r3,#1
                add r3,#entry
                ld.w r4,(r3)
                cmp r4,#0
                jp z,bios_check
                ld r3,#slot_table
                add r3,r4
                pmpw r3,r2
bios_check:
                call check
                jp bios_store
bios_none:
                ld r11,#0
                ld r5,#absent
bios_store:
                ld r4,r5
                cmp r5,#ok
                jp nz,bios_keep
                ld r4,r6
                or r4,#0x10
bios_keep:
                ld r3,r1
                sub r3,#bios_first
                add r3,#result
                ld.b (r3),r4
                cmp r5,#ok
                jp nz,bios_next
                cmp r7,#no_region
                jp z,load_bios
                cmp r6,r7
                jp z,load_bios
                jp bios_next
bios_done:
                cmp r10,#0
                jp z,any_region
                ld r10,#0
                jp bios_pass

                // Otherwise the first usable BIOS in slot order; r12 the
                // first unusable file's slot.
any_region:
                ld r12,#0
                ld r9,#bios_slots
any_next:
                ld.w r1,(r9)
                cmp r1,#0
                jp z,none_usable
                add r9,#2
                ld r3,r1
                sub r3,#bios_first
                add r3,#result
                ld.b r4,(r3)
                cmp r4,#0x10
                jp nc,any_usable
                cmp r4,#bad_size
                jp c,any_next               // absent
                cmp r12,#0
                jp nz,any_next
                ld r12,r1
                jp any_next
any_usable:
                ld r6,r4
                and r6,#3

load_bios:
                loadf r1
                jp nz,load_error
                // pmpw reaches the core byte-swapped (hardware readback
                // 2026-10-18: Europe arrived as 0), so the region goes in both
                // the low and the high byte; either order gives bits 1:0.
                ld r1,#region_reg
                ld r2,r6
                asl r2,#16
                asl r2,#8
                or r2,r6
                pmpw r1,r2
                // The GPU's PAL video mode at reset comes from this pin
                // (references/PSX_MiSTer/rtl/gpu.vhd: GPUSTAT_PalVideoMode <=
                // isPal), so a PAL BIOS left with isPal 0 gets NTSC timing:
                // vtotal 262 instead of 312/313 and vDisplayMax 240 instead of
                // 288, cutting the bottom of its own screens (0.5.5 hardware:
                // the SCPH-5502 Sony Computer Entertainment screen and the
                // PlayStation logo after it). Only Europe drives a 50 Hz
                // screen; games set the bit themselves with GP1(08). The core
                // takes bit 0, so the 1 goes in both byte lanes like the region.
                ld r1,#ispal_reg
                ld r2,#0                    // USA and Japan: NTSC
                cmp r6,#2
                jp nz,ispal_done
                ld r2,#0x01000001           // Europe: PAL
ispal_done:
                pmpw r1,r2
                ld r1,#biosregion_reg       // the BIOS writer always fills bank 0
                ld r6,#0
                pmpw r1,r6

                // The memory card: loaded when its file exists. The table
                // pass has its size; a card slot outside the table is opened
                // to find out.
                ld r1,#memcard_slot
                cmp r1,#0xffff
                jp z,start_core
                ld.l r2,(card_size)
                cmp r2,#0
                jp z,start_core
                cmp r2,#0xffffffff
                jp nz,card_load
                open r1,r2
                jp nz,start_core
                close
card_load:
                loadf r1
                jp nz,card_error

start_core:
                // After every table write (BIOS sizes come from the passes).
                ld r1,#send_complete
                cmp r1,#0
                jp z,host_start
                ld r1,#bridge_host
                ld r2,#cmd_allcomplete
                pmpw r1,r2
host_start:
                or r13,#flag_started
                ld r0,#host_init
                host r0,r0
                exit 0

// fw_size: slot r1's size r2 into the cue reader's size block when it is one
// the boot mount would otherwise measure with ~19 APF reads (r3).
fw_size:
                ld r3,#size_slot0
                cmp r1,#0
                jp z,fw_put
                ld r3,#size_slot99
                cmp r1,#99
                jp z,fw_put
                ld r3,#size_slot100
                cmp r1,#100
                jp z,fw_put
                ld r3,#size_slot101
                cmp r1,#101
                jp z,fw_put
                ld r3,#size_slot102
                cmp r1,#102
                jp z,fw_put
                ld r3,#size_slot98
                cmp r1,#98
                jp nz,fw_none
fw_put:
                pmpw r3,r2
fw_none:
                ret

// licence: the open disc image's region (sector 4 licence text) into r7,
// left at no_region when unreadable (r3, r4).
licence:
                ld r3,#licence_seek
                seek r3
                ret nz
                ld r3,#buf
                ld r4,#4
                read r3,r4
                ret nz
                ld r3,#buf+4
                ld r4,#0
                ld.b (r3),r4
                ld r3,#buf
                ld r4,#licence_usa
                test r3,r4
                jp z,licence_usa_hit
                ld r4,#licence_japan
                test r3,r4
                jp z,licence_japan_hit
                ld r4,#licence_europe
                test r3,r4
                ret nz
                ld r7,#2
                ret
licence_usa_hit:
                ld r7,#0
                ret
licence_japan_hit:
                ld r7,#1
                ret

// check: slot r1 open, r2 its size. Returns r5 (codes above), a usable
// file's region in r6, and closes it; buf holds the version line.
check:
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
                cmp r11,#3
                jp z,bios_none              // the BIOS slot has no file
                cmp r11,#0
                jp nz,table_empty           // the slot has no file
                ld r14,#msg_internal
                printf r14
                err r0,r1
                hex.w r1
                exit 1

// r7 <- the region named in slot r1's file name, if any (r3, r4, r9, r14)
name_region:
                cmp r1,#0xffff
                ret z
                queryslot r1
                ret nz
                ld r14,#name_buf
                getname r1,r14
                ld r3,#name_buf
name_pos:
                ld.b r4,(r3)
                cmp r4,#0
                ret z
                ld r9,#region_names
name_pat:
                ld.w r4,(r9)
                cmp r4,#0
                jp z,name_next
                test r3,r4                  // carry: the name continues with it
                jp c,name_hit
                add r9,#4
                jp name_pat
name_hit:
                add r9,#2
                ld.w r7,(r9)
                ret
name_next:
                add r3,#1
                jp name_pos

region_names:                               // pattern, region (0 USA, 1 Japan, 2 Europe)
                dw name_usa, 0, name_japan, 1, name_europe, 2, name_uk, 2, name_france, 2
                dw name_germany, 2, name_italy, 2, name_spain, 2, name_australia, 2, 0
name_usa:
                db "(USA",0
name_japan:
                db "(Japan",0
name_europe:
                db "(Europe",0
name_uk:
                db "(UK",0
name_france:
                db "(France",0
name_germany:
                db "(Germany",0
name_italy:
                db "(Italy",0
name_spain:
                db "(Spain",0
name_australia:
                db "(Australia",0

version_prefix:
                db "System ROM Version ",0
licence_usa:
                db "Amer",0
licence_japan:
                db "Inc.",0
licence_europe:
                db "Euro",0
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
