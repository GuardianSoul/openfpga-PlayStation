; cd_subcpu firmware: definitions (cd-rewrite-061). Source of truth for behaviour: s25 cd_top.vhd
; (command process 1136-2231, drive process 2305-3155, motor process 1093-1120, subchannel 3496-3592).
.include "cd_ports.inc"

; ---------------------------------------------------------------- cd_subcpu internal ports
.equ T_ACK 96
.equ T_WORK 97
.equ T_DRIVE 98
.equ T_MOTOR 99
.equ T_ACK_OFF 100
.equ T_WORK_OFF 101
.equ T_DRIVE_OFF 102
.equ T_MOTOR_OFF 103
.equ T_RELOAD 104
.equ FWFLAG 105
.equ C_ACK_STROBE 106
.equ C_WORK_STROBE 107
.equ C_DRIVE_STROBE 108
.equ C_ACK_FLAG 109
.equ C_WORK_FLAG 110
.equ C_DRIVE_FLAG 111
.equ NOW_STROBE 112
.equ FLAG_CLR 113
.equ SQ_PUSH 114
.equ SQ_ARM 115
.equ SQ_CLR 116
.equ EV_CLR 117
.equ WQ_DATA 118
.equ WQ_TAG 119
.equ WQ_CLR 120
.equ ACK_FRESH 121
.equ IN_TWORK 97
.equ IN_TDRIVE 98
.equ IN_TMOTOR 99        ; reads only bit 16 (>= 65,536) and bit 0 (/= 0)
.equ IN_EV 100
.equ IN_FLAGS 101

; flags (cd_subcpu flags vector)
.equ FL_BUSY 1          ; cmd_busy
.equ FL_PEND 2          ; cmdPending
.equ FL_WORK 4          ; working
.equ FL_RESPRST 8       ; commit only: response FIFO reset on the commit clock
; fwflag bits
.equ FW_WHOLD1 1        ; WORK holds at 1 (initseek)
.equ FW_WMOTOR 2        ; WORK holds while MOTOR /= 0 (initseek)
.equ FW_DMOTOR 4        ; DRIVE holds while MOTOR /= 0 (unless pauseReadTail)
.equ FW_ACKPAUSE 8      ; ACK expiry waits while WORK > 8448 (GetStat during a Pause reply)
.equ FW_WORKING 16      ; working (ACK guard)

; events
.equ EV_CMD 1           ; beginCommand
.equ EV_ACKREADV 2      ; ackRead_valid
.equ EV_ACKPEND 4       ; ackPendingIRQ
.equ EV_UPDDONE 8       ; UpdateSubchannelDone (unused with subq)
.equ EV_XASTART 16
.equ EV_XARESET 32
.equ EV_NEWCD 64
.equ EV_LID 128
.equ EV_READSUB 256     ; readSubchannel (sector fetched)
.equ EV_IRQCLR 512      ; IRQ flags cleared
.equ EV_FETCHERR 1024
.equ EV_HASCD 2048
.equ EV_ACK 4096
.equ EV_WORK 8192
.equ EV_DRIVE 16384
.equ EV_MOTOR 32768

; drive states (tdrivestate order)
.equ D_IDLE 0
.equ D_SEEKPHYS 1
.equ D_SEEKLOG 2
.equ D_SEEKIMPL 3
.equ D_READING 4
.equ D_PLAYING 5
.equ D_SPEEDTOC 6
.equ D_SPINUP 7
.equ D_SESSION 8
.equ D_OPENING 9
.equ D_MOTORON 10
.equ SF_IDLE 0          ; sectorFetchState
.equ SP_IDLE 0          ; sectorProcessState
.equ CP_IDLE 0          ; copyState

; ---------------------------------------------------------------- registers
; r0 = 0.  r8 internalStatus shadow, r9 modeReg, r10 driveState, r11 flags F1, r12 flags F2,
; r13 nextCmd, r14 workCommand. r1-r7 scratch, r15 scratch / argument.
; F1 bits
.equ B_SETLOC 0
.equ B_READAFTER 1
.equ B_PLAYAFTER 2
.equ B_HDRVALID 3       ; lastSectorHeaderValid
.equ B_SEEKOK 4
.equ B_SEEKMUTE 5
.equ B_AFTERSEEK 6
.equ B_IMPL1X 7         ; implicitInit1x
.equ B_RPTSEEK 8        ; reportSeekTarget
.equ B_RPTPAUSE 9       ; reportPauseNext
.equ B_COASTING 10      ; stopReadCoasting
.equ B_REJPAUSE 11      ; rejectedReadPause
.equ B_WARMINIT 12      ; warmInitSeek
.equ B_EARLYWARM 13     ; earlyWarmInitRead
.equ B_WARMINNER 14     ; warmInnerReadSeek
.equ B_NEARDATA 15      ; nearDataSeek
.equ B_ROTBEFORE 16     ; nearRotBeforeFull
.equ B_ROTDOUBLE 17     ; nearRotDouble
.equ B_LIMITDELAY 18    ; limitDelayTime
.equ B_MUTED 19
.equ B_TAIL 20          ; pauseReadTail
.equ B_PLAYTRACK 21
.equ B_DBUSY 22         ; driveBusy
.equ B_WORKING 23
.equ B_PENDING 24       ; cmdPending
.equ B_ACKARMED 25      ; ACK timer armed for nextCmd
.equ B_XAPLAY 26
.equ B_PHYUPD 27        ; updatePhysicalPosition requested
.equ B_PAUSELOOK 4  ; in r12 (F2): the 28-bit data path     ; pausePositionLookup
.equ B_UPDSUB 5  ; in r12 (F2): the 28-bit data path        ; UpdateSubchannel (physical lookup in flight)
.equ B_WORKARMED 6  ; in r12 (F2): the 28-bit data path     ; WORK timer armed (workDelay /= 0)
.equ B_NEWCD1 7  ; in r12 (F2): the 28-bit data path        ; newCD seen
; F2 bits
.equ B2_CDDAOUT 0       ; CD-DA report streaming
.equ B2_SETMODEPEND 1
.equ B2_STREAM 2        ; rebuild the long-response list at the next staging
.equ B2_LIVE 3          ; the drive load includes the live remaining time

; ---------------------------------------------------------------- data RAM (32 words)
.equ M_CURLBA 0
.equ M_PHYLBA 1
.equ M_READLBA 2
.equ M_SEEKLBA 3
.equ M_DIFFLBA 4
.equ M_SEEKCYC 5
.equ M_PLAYLBA 6
.equ M_SETLOC 7          ; minute<<16 | second<<8 | frame (binary)
.equ M_SESSION 8
.equ M_FFRATE 9          ; fastForwardRate (signed, two's complement 8 bit in low byte)
.equ M_TRACKBCD 10       ; currentTrackBCD
.equ M_NEXTTRACK 11
.equ M_XAFILE 12
.equ M_XACHAN 13
.equ M_LASTCDDA 14       ; lastreportCDDA
.equ M_ROTPHASE 15
.equ M_ROTSLOTS 16
.equ M_ROTDIST 17
.equ M_ROTWHOLE 18
.equ M_PHYBASE 19
.equ M_PHYSPT 20
.equ M_PHYOLD 21
.equ M_WSP 22            ; writeSectorPointer
.equ M_RSP 23            ; readSectorPointer
.equ M_PARAMCNT 24
.equ M_NEWMODE 25
.equ M_DNEXT 26
.equ M_SUBSEC 27         ; subchannelSector (subq)
.equ M_TMP0 28
.equ M_TMP1 29
.equ M_TMP2 30
.equ M_TMP3 31

.equ PREGAP 150
.equ RS1X 451584
.equ RS2X 225792
.equ INIT_SEEK_VISIBLE_WORK 147456
.equ WARM_INIT_ACQUISITION 4355000
.equ IN_AGE 102
.equ SIGNB 27           ; sign bit of the 28-bit data path (cd_subcpu DW = 28)
.equ TAIL_K -12          ; Pause tail: TDRIVE read to the T_MOTOR write, +1
.equ MOTOR_AGE_K -12      ; IN_AGE sample to the T_MOTOR write (calibrated against the old motorStartClocks)
.equ M_NSUB 32          ; nextSubdata shadow, 12 words (byte each); use a base register
.equ M_SUBD 44          ; subdata shadow, 12 words
.equ M_SUBQ 56          ; subchannel scratch
.equ IN_BCAGE 103       ; clocks the old cmd_delay would have counted since the command byte / last ACK
.equ ACK_K -17          ; IN_BCAGE to the T_ACK write (4 instructions) and the old load's -1
.equ AGED_K -16          ; IN_AGE to the timer write: in, sub, addi, bmi, out
.equ LIVE_K -20          ; IN_TDRIVE to the write: in, cmp, b, add, addi, out
.equ M_PCOUNT 57        ; parameter count seen at staging
.equ M_PBAD 58          ; setLocParamBad seen at staging
.equ CF_PRMRST 0x80    ; commit: parameter FIFO reset
.equ M_PARAMS 59        ; the command's parameter bytes, captured at accept
.equ M_NEWCMD 60        ; command byte being accepted
.equ PRM_DLY 126
