; cd_subcpu firmware: drive side (old cd_top drive process 2305-3155, motor process 1093-1120).

; ------------------------------------------------------------------ small helpers
ds_set:                                 ; driveState <= r15
    mov r10, r15
    out P_driveState, r15
    ret
dbusy:                                  ; driveBusy <= r15
    if r15 == 0
        bclr r11, r11, B_DBUSY
    else
        bset r11, r11, B_DBUSY
    endif
    out P_driveBusy, r15
    ret
clear_active:                           ; internalStatus(7 downto 5) <= "000"
    andi r15, r8, 0x1F
    mov r8, r15
    ret
; r15 = driveREADSPEED (FORCECDSPEED only with allow_speedhack, as the old speed process)
readspeed:                                ; driveREADSPEED: FORCECDSPEED only while the glue allows the speed hack
    in r15, IN_allow_speedhack
    if r15 != 0
        in r15, IN_FORCECDSPEED
    endif
    if r15 == 2
        li r15, 225792
    elif r15 == 3
        li r15, 112896
    elif r15 == 4
        li r15, 75264
    elif r15 == 5
        li r15, 56448
    else
        li r15, 451584
        if bit r9, 7
            li r15, 225792
        endif
    endif
    ret
; driveDelayNext for the stored dnext code -> r15
dnext_value:
    ld r15, [r0+M_DNEXT]
    if r15 == 1
        li r15, 9997
    elif r15 == 2
        li r15, 13230000
    elif r15 == 0
    else
        call readspeed
        addi r15, r15, -2
    endif
    ret
; DRIVE timer load r15 less the clocks since the event that caused it (ev_age)
drive_load_live:                         ; added to the running timer, or an aged load
    btst r12, B2_LIVE
    beq drive_load_aged
    in r1, IN_TDRIVE                     ; 0 when idle: then an aged load
    if r1 == 0
        b drive_load_aged
    endif
    add r15, r15, r1
    addi r15, r15, LIVE_K                ; the timer counted while these instructions ran
    out T_DRIVE, r15
    ret
drive_load_aged:                         ; less the clocks since the event that caused the load (hardware)
    if bit r11, B_TAIL
    else
        in r1, IN_TMOTOR                 ; the motor start holds driveDelay: those clocks did not count
        if r1 != 0
            addi r15, r15, -2            ; the old drive resumes two clocks sooner after the motor
            out T_DRIVE, r15
            ret
        endif
    endif
    in r1, IN_AGE                        ; less the clocks since the event (sampled 4 instructions before the write)
    sub r15, r15, r1
    addi r15, r15, AGED_K
    bmi .dla_floor
    out T_DRIVE, r15
    ret
.dla_floor:
    out T_DRIVE, r0
    ret
drive_load:
    out T_DRIVE, r15
    ret

; sectors per track for r15 = LBA (rama table), -> r15
spt_of:
    li r1, 4500
    li r2, 8
    li r3, spt_tab
.spt_lp:
    ldt r4, [r3+0]                     ; boundary in minutes
    if r4 == 0
        mov r15, r2
        ret
    endif
    ; minutes * 4500
    mov r5, r4
    li r6, 0
.spt_mul:
    add r6, r6, r1
    addi r5, r5, -1
    if r5 != 0
        b .spt_mul
    endif
    if r15 < r6
        mov r15, r2
        ret
    endif
    addi r2, r2, 1
    addi r3, r3, 1
    b .spt_lp
spt_tab:
    .word 1, 5, 8, 12, 17, 24, 28, 33, 40, 45, 53, 61, 68, 75, 0

; ------------------------------------------------------------------ seek (old seekOnDisk* block, calcSeekTime, near rotation, addSeekTime)
; r15 = 0 seekOnDiskCmd, 1 seekOnDiskDrive, 2 seekOnDiskPlay
drv_seek:
    st [r0+M_TMP0], r15
    ld r1, [r0+M_SEEKLBA]
    if r15 == 2
        ld r1, [r0+M_PLAYLBA]
    endif
    st [r0+M_READLBA], r1
    out P_readLBA, r1
    ld r2, [r0+M_CURLBA]
    if r1 > r2
        sub r3, r1, r2
    else
        sub r3, r2, r1
    endif
    st [r0+M_DIFFLBA], r3
    bclr r11, r11, B_NEARDATA
    ; near data seek: ReadN while reading, forward, before the next sector
    if r15 == 1
        if r13 == 6
            if r10 == D_READING
                if bit r11, B_READAFTER
                    if bit r11, B_HDRVALID
                        in r4, IN_fastCD
                        in r5, IN_isAudio
                        call readspeed
                        mov r6, r15
                        in r3, IN_AGE               ; driveDelay at the old seekOnDiskDrive clock:
                        in r7, IN_TDRIVE            ; TDRIVE + age (age sampled 4 clocks earlier) + 3
                        if r7 != 0
                            add r7, r7, r3
                            addi r7, r7, 3
                        endif
                        if r4 == 0
                            if r5 == 0
                                if r1 > r2
                                    li r4, 451584
                                    li r5, 225792
                                    if r6 == r4
                                        b .sk_near
                                    elif r6 == r5
                                        b .sk_near
                                    endif
                                endif
                            endif
                        endif
                    endif
                endif
            endif
        endif
    endif
    b .sk_near_done
.sk_near:
    if r7 > r6
        b .sk_near_done
    endif
    bset r11, r11, B_NEARDATA
    sub r4, r6, r7                      ; nearRotPhase (19 bits)
    li r5, 0x7FFFF
    and r4, r4, r5
    st [r0+M_ROTPHASE], r4
    bclr r11, r11, B_ROTBEFORE
    if r7 != 0
        bset r11, r11, B_ROTBEFORE
    endif
    mov r15, r1
    call rotslots
    st [r0+M_ROTSLOTS], r15
    bclr r11, r11, B_ROTDOUBLE
    li r5, 225792
    if r6 == r5
        bset r11, r11, B_ROTDOUBLE
    endif
.sk_near_done:
    li r15, S_readOnDisk
    out NOW_STROBE, r15
    bclr r11, r11, B_SEEKMUTE
    bclr r11, r11, B_WARMINNER
    ld r1, [r0+M_READLBA]
    ld r2, [r0+M_CURLBA]
    ld r15, [r0+M_TMP0]
    if r15 == 1
        if bit r11, B_READAFTER
            if r10 == D_IDLE
                if bit r11, B_HDRVALID
                    li r4, 4096
                    if r2 < r4
                        if r1 < r4
                            bset r11, r11, B_WARMINNER
                        endif
                    endif
                endif
            endif
        endif
    endif
    if r15 == 0
        bclr r11, r11, B_READAFTER
        bclr r11, r11, B_PLAYAFTER
    endif
    bclr r11, r11, B_HDRVALID
    call clear_active
    bset r8, r8, 1
    bset r8, r8, 6
    call status_out
    ; driveDelay: speed - 2 (code 4); dnext <= speed code
    call readspeed
    addi r6, r15, -2
    li r15, 0
    st [r0+M_DNEXT], r15
    call dnext_code_speed
    bclr r11, r11, B_LIMITDELAY
    bclr r12, r12, B2_LIVE
    in r4, IN_INSTANTSEEK
    if r4 == 0
        in r7, IN_TDRIVE
        ld r15, [r0+M_TMP0]
        if r10 == D_SEEKLOG
            b .sk_add_next
        elif r10 == D_SEEKPHYS
            b .sk_add_next
        elif r10 == D_SEEKIMPL
            b .sk_add_next
        elif r10 == D_SPEEDTOC
            if r15 != 2
                if bit r11, B_PLAYAFTER
                else
                    b .sk_add_next2
                endif
            endif
        endif
        b .sk_calc
.sk_add_next:
        bset r11, r11, B_LIMITDELAY
.sk_add_next2:
        bset r12, r12, B2_LIVE            ; cse_driveDelayNext: + the running driveDelay (added in hardware)
.sk_calc:
        st [r0+M_TMP1], r6
        call seek_time                     ; r15 = seekCycles (near rotation included); clobbers r1-r7
        ld r6, [r0+M_TMP1]
        add r6, r6, r15
        if bit r11, B_LIMITDELAY
            li r4, 22880000
            if r6 > r4
                mov r6, r4
            endif
        endif
    endif
    mov r15, r6
    call drive_load_live
    li r15, 1
    call dbusy
    li r15, D_SEEKPHYS
    if r13 == 0x15
        li r15, D_SEEKLOG
    endif
    call ds_set
    bclr r11, r11, B_SETLOC
    out P_setLocActive, r0
    b drv_stage

dnext_code_speed:                        ; dnext_code <= dnext_speed (3 1x, 4 2x, 5 4x, 6 6x, 7 8x)
    call readspeed
    li r1, 3
    li r2, 225792
    if r15 == r2
        li r1, 4
    endif
    li r2, 112896
    if r15 == r2
        li r1, 5
    endif
    li r2, 75264
    if r15 == r2
        li r1, 6
    endif
    li r2, 56448
    if r15 == r2
        li r1, 7
    endif
    st [r0+M_DNEXT], r1
    out P_dnext_code, r1
    ret

; zone map: sectors per revolution for LBA r15 (the reference CD4), -> r15
rotslots:
    shr4 r15, r15
    shr4 r15, r15
    shr4 r15, r15                        ; LBA / 4096
    andi r15, r15, 127
    li r1, rot_tab
    li r2, 10
.rs_lp:
    ldt r3, [r1+0]
    if r3 == 0
        mov r15, r2
        ret
    endif
    if r15 < r3
        mov r15, r2
        ret
    endif
    addi r1, r1, 1
    addi r2, r2, 1
    b .rs_lp
rot_tab:
    .word 3, 7, 12, 18, 23, 30, 36, 43, 51, 59, 67, 76, 85, 0

; seek time in clocks (old calcSeekTime / near rotation / ROT_SUBTRACT) -> r15
seek_time:
    ld r1, [r0+M_DIFFLBA]
    if bit r11, B_NEARDATA
        if r1 < 64
            b .st_near
        endif
    endif
    call seek_units                        ; r2 = units
    b .st_scale
.st_near:                                  ; near data seek: whole sectors to the target angle
    st [r0+M_ROTDIST], r1
    ld r3, [r0+M_ROTSLOTS]
    addi r4, r3, 2
    if r1 > r4
        addi r4, r3, 3
        if r1 == r4
            if bit r11, B_ROTBEFORE
            else
                b .st_scale_near
            endif
        endif
        ; whole sectors: diff - 8 (late quarter) or diff - 7
        ld r5, [r0+M_ROTPHASE]
        li r6, 112896
        if bit r11, B_ROTDOUBLE
            li r6, 56448
        endif
        addi r7, r1, -7
        if r5 > r6
            addi r7, r1, -8
        endif
        ; reduce by a revolution while whole >= slots
        ld r2, [r0+M_ROTDIST]
.st_red:
        if r7 >= r3
            sub r7, r7, r3
            sub r2, r2, r3
            b .st_red
        endif
        ; delta = whole - slots >= -4 -> distance += slots
        addi r4, r7, 4
        if r4 >= r3
            add r2, r2, r3
        endif
        st [r0+M_ROTDIST], r2
    endif
.st_scale_near:
    ld r1, [r0+M_ROTDIST]
    li r2, 0
    if r1 > 2
        addi r1, r1, -2
        shl1 r1, r1
        shl1 r1, r1
        shl1 r1, r1                        ; * 8
        if bit r11, B_ROTDOUBLE
        else
            shl1 r1, r1                    ; * 16
        endif
        mov r2, r1
    endif
    call units_to_clocks
    ; ROT_SUBTRACT: seekCycles - nearRotPhase, floor 0
    ld r1, [r0+M_ROTPHASE]
    if r15 > r1
        sub r15, r15, r1
    else
        li r15, 0
    endif
    st [r0+M_SEEKCYC], r15
    ret
.st_scale:
    call units_to_clocks
    st [r0+M_SEEKCYC], r15
    ret
units_to_clocks:                           ; r15 = r2 * 28224 (shift-add)
    li r15, 0
    li r3, 28224
.uc_lp:
    if r2 != 0
        btst r2, 0
        beq .uc_s
        add r15, r15, r3
.uc_s:
        shl1 r3, r3
        shr1 r2, r2
        b .uc_lp
    endif
    ret
; the reference CD2 v2 seek ladder: r1 = distance -> r2 = units (28,224 clocks each)
seek_units:
    li r3, 507664
    if bit r9, 7
    elif r1 >= r3
        li r2, 4700
        ret
    endif
    li r3, 1000
    li r4, 10000
    if r1 <= r3
        if bit r9, 7
            li r2, 50
            if bit r11, B_WARMINNER
                if r1 < 16
                    li r2, 48
                endif
            endif
            mov r5, r1
            shr4 r5, r5                    ; d / 16
        else
            li r2, 96
            mov r5, r1
            shr1 r5, r5
            shr1 r5, r5
            shr1 r5, r5                    ; d / 8
        endif
        add r2, r2, r5
        ret
    elif r1 <= r4
        sub r5, r1, r3                     ; d - 1000
        shl1 r6, r5
        add r5, r5, r6                     ; * 3
        if bit r9, 7
            li r2, 112
            shr4 r5, r5
            shr1 r5, r5
            shr1 r5, r5
            shr1 r5, r5                    ; * 3 / 128
        else
            li r2, 221
            shr4 r5, r5
            shr4 r5, r5                    ; * 3 / 256
        endif
        add r2, r2, r5
        ret
    endif
    sub r5, r1, r4                         ; d - 10000
    if bit r9, 7
        li r2, 322
        shr4 r5, r5
        shr4 r5, r5                        ; / 256
    else
        shl1 r6, r5
        shl1 r6, r6
        shl1 r6, r6
        add r5, r5, r6                     ; * 9
        li r2, 326
        shr4 r5, r5
        shr4 r5, r5
        shr1 r5, r5
        shr1 r5, r5                        ; * 9 / 1024
    endif
    add r2, r2, r5
    ret

; ------------------------------------------------------------------ command-driven drive actions
; readSN (ReadN/ReadS)
drv_readsn:
    if bit r11, B_EARLYWARM
        if r10 == D_SEEKIMPL
            if bit r11, B_DBUSY
                if bit r11, B_SETLOC
                else
                    in r1, IN_hasCD
                    in r2, IN_LIDopen
                    in r3, IN_TDRIVE
                    li r4, WARM_INIT_ACQUISITION
                    if r1 != 0
                        if r2 == 0
                            if r3 > r4
                                mov r15, r4
                                call drive_load_aged
                            endif
                        endif
                    endif
                endif
            endif
        endif
    endif
    bclr r11, r11, B_EARLYWARM
    if bit r11, B_SETLOC
        bset r11, r11, B_READAFTER
        bclr r11, r11, B_PLAYAFTER
        li r15, 1
        b drv_seek
    endif
    call clear_active
    call status_out
    bclr r11, r11, B_AFTERSEEK
    b drv_start_reading

; play
drv_play:
    ld r1, [r0+M_NEXTTRACK]
    st [r0+M_TRACKBCD], r1
    if bit r11, B_PLAYTRACK
        bclr r11, r11, B_READAFTER
        bset r11, r11, B_PLAYAFTER
        li r15, 2
        b drv_seek
    endif
    if bit r11, B_SETLOC
        bclr r11, r11, B_READAFTER
        bset r11, r11, B_PLAYAFTER
        li r15, 1
        b drv_seek
    endif
    bclr r11, r11, B_AFTERSEEK
    b drv_start_playing

; startReading (old drive process, one clock after the trigger)
drv_start_reading:
    out P_clearSectorBuffers, r0
    if bit r11, B_AFTERSEEK
    else
        bset r8, r8, 6
    endif
    if r10 == D_SEEKLOG
        b .sr_seek
    elif r10 == D_SEEKPHYS
        b .sr_seek
    elif r10 == D_SEEKIMPL
        b .sr_seek
    endif
    bset r8, r8, 1
    call status_out
    mov r7, r10                             ; state before (for the delay rule)
    li r15, D_READING
    call ds_set
    out P_SPEEDHACK_SET, r0                 ; old allow_speedhack <= '1' (the glue clears it on XA / MDEC / not reading)
    b drv_start_common
.sr_seek:
    bset r11, r11, B_READAFTER
    bclr r11, r11, B_PLAYAFTER
    call status_out
    ret
drv_start_playing:
    out P_clearSectorBuffers, r0
    if r10 == D_SEEKLOG
        b .sp_seek
    elif r10 == D_SEEKPHYS
        b .sp_seek
    elif r10 == D_SEEKIMPL
        b .sp_seek
    endif
    call clear_active
    bset r8, r8, 1
    bset r8, r8, 7
    call status_out
    mov r7, r10
    li r15, D_PLAYING
    call ds_set
    b drv_start_common
.sp_seek:
    bclr r11, r11, B_READAFTER
    bset r11, r11, B_PLAYAFTER
    ret
drv_start_common:                          ; r7 = drive state before
    li r15, 1
    call dbusy
    out P_writeSectorPointer, r0
    out P_readSectorPointer, r0
    ld r1, [r0+M_CURLBA]
    st [r0+M_READLBA], r1
    out P_readLBA, r1                      ; before the strobe: the fetch latches readLBA
    li r15, S_readOnDisk
    out NOW_STROBE, r15
    in r2, IN_fastCD
    if r2 != 0
        li r6, 9997
        li r1, 1
        st [r0+M_DNEXT], r1
        out P_dnext_code, r1
    else
        call readspeed
        addi r6, r15, -2
        call dnext_code_speed
    endif
    call readspeed
    st [r0+M_SEEKCYC], r15                 ; seekCycles <= driveREADSPEED
    bclr r12, r12, B2_LIVE
    in r3, IN_INSTANTSEEK
    if r3 == 0
        if bit r11, B_AFTERSEEK
        else
            if r7 == D_SEEKLOG
                b .sc_next
            elif r7 == D_SEEKPHYS
                b .sc_next
            elif r7 == D_SEEKIMPL
                b .sc_next
            elif r7 == D_SPEEDTOC
                if r10 == D_READING
                    b .sc_next
                endif
            endif
            b .sc_add
.sc_next:
            bset r12, r12, B2_LIVE          ; speed - 2 + the running driveDelay (added in hardware)
.sc_add:
            ld r4, [r0+M_SEEKCYC]
            add r6, r6, r4                  ; addSeekTime (limitDelayTime 0)
            bclr r11, r11, B_LIMITDELAY
        endif
    endif
    addi r15, r6, 2                       ; the old startReading loaded driveDelay two clocks after its trigger
    call drive_load_live
    b drv_stage

; Stop
drv_cmdstop:
    bclr r11, r11, B_HDRVALID
    li r15, D_IDLE
    call ds_set
    li r15, 0
    call dbusy
    call clear_active
    bclr r8, r8, 1
    call status_out
    st [r0+M_CURLBA], r0
    out P_currentLBA, r0
    out T_DRIVE_OFF, r0
    ret

; Pause (drive_stop)
drv_drivestop:
    if r10 == D_READING
        in r1, IN_isAudio
        if r1 == 0
            ; report the next position after a 1x data Pause (pause hold)
            in r2, IN_INSTANTSEEK
            in r3, IN_hasCD
            in r4, IN_LIDopen
            in r5, IN_libcryptOn
            in r6, IN_CDROM_IRQFLAG
            in r7, IN_pendingDriveIRQ
            if bit r9, 7
            elif r2 != 0
            elif r3 == 0
            elif r4 != 0
            elif r5 != 0
            elif r6 == 1
            elif r7 == 1
            else
                if bit r11, B_HDRVALID
                    bset r11, r11, B_RPTPAUSE
                endif
            endif
            ld r15, [r0+M_CURLBA]
            call spt_of
            ld r1, [r0+M_CURLBA]
            if r1 >= r15
                sub r1, r1, r15
            else
                li r1, 0
            endif
            st [r0+M_PHYLBA], r1
        endif
    endif
    li r15, D_IDLE
    call ds_set
    li r15, 0
    call dbusy
    call clear_active
    call status_out
    out T_DRIVE_OFF, r0
    ret

; motor start (startMotor / cmdStartMotor); r15 = 1 for the command
drv_startmotor:
    if r10 == D_SPINUP
        ret
    elif r10 == D_MOTORON
        ret
    endif
    mov r1, r15
    if r1 != 0
        if bit r12, B_WORKARMED
        else
            li r15, D_MOTORON
            call ds_set
            li r15, 76549576
            if bit r9, 7
                li r15, 93655984
            endif
            b .sm_load
        endif
    endif
    li r15, D_SPINUP
    call ds_set
    li r15, 13230000
.sm_load:
    call drive_load_aged
    li r15, 2
    st [r0+M_DNEXT], r15
    out P_dnext_code, r15
    li r15, 1
    call dbusy
    b drv_stage

; Setmode (drive process setMode); r3 = new mode
drv_setmode:
    mov r1, r9
    mov r9, r3
    out P_modeReg, r9
    xor r1, r1, r3
    btst r1, 7
    beq .smd_done
    if r10 == D_SPEEDTOC
    elif r10 == D_SEEKIMPL
    elif r10 == D_IDLE
        li r15, 17350000
        call drive_load_aged
        li r15, D_SPEEDTOC
        call ds_set
        li r15, 1
        call dbusy
        b drv_stage
    else
        li r15, 400000
        call drive_load_aged
    endif
.smd_done:
    ret

; SetSession
drv_setsession:
    li r15, D_SESSION
    call ds_set
    li r15, 1
    call dbusy
    li r15, 16934400
    call drive_load_aged
    b drv_stage

; soft reset (Init): old drive process softReset branch
drv_softreset:
    bclr r11, r11, B_RPTSEEK
    bclr r11, r11, B_RPTPAUSE
    bclr r12, r12, B_PAUSELOOK
    bclr r11, r11, B_NEARDATA
    mov r1, r9                              ; old mode for implicitInit1x
    li r9, 0x20
    out P_modeReg, r9
    li r8, 0
    out P_SHELL_CLR, r0                     ; internalStatus <= 0 includes the shell bit
    in r2, IN_hasCD
    if r2 == 0
        call status_out
        li r15, D_IDLE
        call ds_set
        li r15, 0
        call dbusy
        out T_DRIVE_OFF, r0
        ret
    endif
    bset r8, r8, 1
    call status_out
    ld r3, [r0+M_CURLBA]
    if r3 != 0
        li r15, D_SEEKIMPL
        call ds_set
        bclr r11, r11, B_IMPL1X
        btst r1, 7
        bne .srs_2x
        bset r11, r11, B_IMPL1X
.srs_2x:
        st [r0+M_READLBA], r0
        out P_readLBA, r0                   ; before the strobe: the fetch latches readLBA
        li r15, S_readOnDisk
        out NOW_STROBE, r15
    else
        li r15, D_SPEEDTOC
        call ds_set
    endif
    li r15, 1
    call dbusy
    ; driveDelay: 2x 475999 + 33868800 + 19999, 1x 476408 + 19999
    li r6, 34364798
    btst r1, 7
    bne .srs_d
    li r6, 496407
.srs_d:
    in r2, IN_INSTANTSEEK
    if r2 == 0
        bclr r11, r11, B_LIMITDELAY
        st [r0+M_DIFFLBA], r3
        if bit r11, B_WARMINIT
            li r4, PREGAP
            li r5, 225               ; PREGAP + 75
            if r3 >= r4
                if r3 < r5
                    li r6, WARM_INIT_ACQUISITION
                    b .srs_load
                endif
            endif
        endif
        bclr r11, r11, B_NEARDATA
        st [r0+M_TMP1], r6
        call seek_time
        ld r6, [r0+M_TMP1]
        add r6, r6, r15               ; calcSeekTime + addSeekTime
    endif
.srs_load:
    mov r15, r6
    call drive_load_aged
    b drv_stage

; ------------------------------------------------------------------ the next drive event: stage its commit
; Seek completion and sector arm commit their strobes and the status/LBA writes on the expiry clock.
drv_stage:
    out WQ_CLR, r0
    li r5, 0                                ; strobes
    if r10 == D_SEEKLOG
        b .dg_seek
    elif r10 == D_SEEKPHYS
        b .dg_seek
    elif r10 == D_SEEKIMPL
        b .dg_seek
    elif r10 == D_READING
        b .dg_sector
    elif r10 == D_PLAYING
        b .dg_sector
    elif r10 == D_SESSION
        ld r1, [r0+M_SESSION]
        if r1 == 1
            li r5, S_ackDrive
        endif
    endif
    out C_DRIVE_STROBE, r5
    out C_DRIVE_FLAG, r0
    ret
.dg_seek:                                   ; completeSeek + handleDrive seek arm
    call seek_ok                            ; r1 = seekOK
    li r5, S_copySubdata
    mov r2, r8
    andi r2, r2, 0x1F                       ; clear active bits
    if r1 != 0
        if bit r11, B_READAFTER
            bset r5, r5, SB_startReading
            bset r2, r2, 6                  ; keep seek on until the read is active
        elif bit r11, B_PLAYAFTER
            bset r5, r5, SB_startPlaying
        elif r10 == D_SEEKIMPL
        elif bit r11, B_SEEKMUTE
        else
            bset r5, r5, SB_ackDrive
        endif
    elif r10 != D_SEEKIMPL
        bset r5, r5, SB_errDrive
        li r3, 4
        out P_cdrw_err_drv, r3
        out P_cdrw_rsn_drv, r3
    endif
    mov r15, r2
    li r1, P_internalStatus
    call wq_put
    in r15, IN_lastReadSector
    li r1, P_currentLBA
    call wq_put
    li r15, D_IDLE
    li r1, P_driveState
    call wq_put
    li r15, 0
    li r1, P_driveBusy
    call wq_put
    ; data seek: processSeekHeader + header valid
    if r10 == D_SEEKLOG
        call nsub_isdata
        if r15 != 0
            li r15, 1
            li r1, P_processSeekHeader
            call wq_put
        endif
    endif
    out C_DRIVE_STROBE, r5
    out C_DRIVE_FLAG, r0
    ret
.dg_sector:                                 ; reading/playing: the sector arm
    in r1, IN_fetchErr
    if r1 != 0
        li r5, S_errDrive
        li r3, 4
        out P_cdrw_err_drv, r3
        out P_cdrw_rsn_drv, r3
        b .dg_end_state
    endif
    li r1, M_NSUB
    ld r1, [r1+1]                            ; nextSubdata(1): track
    li r2, 0xAA                              ; lead-out
    if r1 == r2
        li r5, S_ackDriveEnd
        b .dg_end_state
    endif
    in r3, IN_isAudio
    if r3 != 0
        ld r2, [r0+M_TRACKBCD]
        if r2 != 0
            if r1 != r2
                if bit r9, 1                 ; auto pause on a track switch
                    li r5, S_ackDriveEnd
                    b .dg_end_state
                endif
            endif
        endif
    endif
    ; staged in this order: status, readLBA, write pointer, readOnDisk (pulse: the fetch latches readLBA),
    ; the next sector period, currentLBA
    li r5, S_copySubdata
    mov r2, r8
    if r3 == 0
        if r10 == D_READING
            bset r5, r5, SB_processDataSector
            bset r2, r2, 5
            bclr r2, r2, 6
            mov r15, r2
            li r1, P_internalStatus
            call wq_put
        endif
    elif r10 == D_PLAYING
        bset r5, r5, SB_processCDDASector
    elif bit r9, 0
        bset r5, r5, SB_processCDDASector
    endif
    in r4, IN_lastReadSector
    addi r15, r4, 1
    ld r6, [r0+M_FFRATE]
    if r6 != 0
        btst r5, SB_processCDDASector       ; fast forward on audio
        beq .dg_ff
        add r15, r4, r6
        li r6, 0x7FFFF
        and r15, r15, r6
    endif
.dg_ff:
    li r1, P_readLBA
    call wq_put
    ; ackRead and the write slot: the glue decides them on the commit clock from the fetched sector's header
    li r15, 1
    li r1, P_PULSE_readOnDisk
    call wq_put
    in r1, IN_fastCD
    li r15, 9997
    if r1 == 0
        call readspeed
        addi r15, r15, -2
    endif
    ; the reload's queue position after the first two entries: one clock each
    in r1, IN_fastCD
    li r2, 2
    btst r5, SB_processDataSector
    bne .dg_q2
    addi r2, r2, -1                          ; no status entry
.dg_q2:
    sub r15, r15, r2
    li r1, 98                                ; DRIVE reload
    call wq_put
    in r15, IN_lastReadSector
    li r1, P_currentLBA
    call wq_put
    out C_DRIVE_STROBE, r5
    out C_DRIVE_FLAG, r0
    ret
.dg_end_state:                               ; stop: idle, active bits clear
    mov r15, r8
    andi r15, r15, 0x1F
    li r1, P_internalStatus
    call wq_put
    li r15, D_IDLE
    li r1, P_driveState
    call wq_put
    li r15, 0
    li r1, P_driveBusy
    call wq_put
    out C_DRIVE_STROBE, r5
    out C_DRIVE_FLAG, r0
    ret

wq_put:                                      ; staged write r15 -> port r1 on the DRIVE slot
    out WQ_DATA, r15
    li r15, 0x100                            ; slot 2 << 7
    or r15, r15, r1
    out WQ_TAG, r15
    ret

; seekOK at completion: data sector, or CD-DA allowed; not lead-out, no fetch error -> r1
seek_ok:
    li r1, 1
    call nsub_isdata
    if r15 == 0
        if r10 == D_SEEKLOG
            btst r9, 0
            bne .so_1
            li r1, 0
        endif
    endif
.so_1:
    li r2, M_NSUB
    ld r2, [r2+1]
    li r3, 0xAA
    if r2 == r3
        li r1, 0
    endif
    in r2, IN_fetchErr
    if r2 != 0
        li r1, 0
    endif
    ret
nsub_isdata:                                 ; r15 = nextSubdata(0)(6)
    li r15, M_NSUB
    ld r15, [r15+0]
    andi r15, r15, 0x40
    ret

