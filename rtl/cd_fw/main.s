; cd_subcpu firmware: reset, event loop, event handlers.

.org 0
reset:
    li r8, 0
    li r9, 0
    li r10, 0
    li r11, 0
    li r12, 0
    li r13, 0
    li r14, 0
    li r15, 11
    out T_RELOAD, r15                      ; no-op since fw13 (the WORK hold at 11 is hardwired); kept so boot timing is unchanged
    li r15, 7
    out FLAG_CLR, r15
    call fwflag_sync
main:
    li r1, 0xFFFF
    wait r2, r1
    st [r0+M_TMP3], r2
    btst r2, 6                             ; newCD (rising edge)
    beq .m1
    call ev_newcd
.m1:
    ld r2, [r0+M_TMP3]
    btst r2, 7                             ; lid
    beq .m2
    call ev_lid
.m2:
    ld r2, [r0+M_TMP3]
    btst r2, 14                            ; DRIVE event
    beq .m3
    call drv_fire
.m3:
    ld r2, [r0+M_TMP3]
    btst r2, 12                            ; ACK event (handleCommand)
    beq .m4
    call cmd_exec
    call cmd_stage
.m4:
    ld r2, [r0+M_TMP3]
    btst r2, 13                            ; WORK event
    beq .m5
    call work_fire
.m5:
    ld r2, [r0+M_TMP3]
    btst r2, 15                            ; MOTOR event: tail ends
    beq .m6
    bclr r11, r11, B_TAIL
    call fwflag_sync
.m6:
    ld r2, [r0+M_TMP3]
    btst r2, 0                             ; command written
    beq .m7
    call cmd_accept
.m7:
    ld r2, [r0+M_TMP3]
    btst r2, 8                             ; sector fetched: subchannel Q
    beq .m8
    call subq_sector
    call drv_stage
.m8:
.m9:
    ld r2, [r0+M_TMP3]
    btst r2, 4                             ; XA start / reset: no speed hack while XA plays
    beq .m10
    bset r11, r11, B_XAPLAY
.m10:
    ld r2, [r0+M_TMP3]
    btst r2, 5
    beq .m11
    bclr r11, r11, B_XAPLAY
.m11:
    if bit r11, B_PHYUPD
        call phys_update
    endif
    call cmd_stage                         ; restage the ACK with the newest state
    b main

; ------------------------------------------------------------------ disc change / lid
ev_newcd:
    bclr r11, r11, B_REJPAUSE
    bclr r11, r11, B_WARMINIT
    bclr r11, r11, B_EARLYWARM
    bclr r11, r11, B_COASTING
    bclr r11, r11, B_RPTSEEK
    bclr r11, r11, B_RPTPAUSE
    bclr r11, r11, B_TAIL
    out T_MOTOR_OFF, r0
    ; command side: response FIFO reset, pending/busy cleared, ACK dropped
    bclr r11, r11, B_PENDING
    bclr r11, r11, B_ACKARMED
    out T_ACK_OFF, r0
    li r15, 3
    out FLAG_CLR, r15
    out C_ACK_STROBE, r0
    out C_ACK_FLAG, r0
    li r15, FL_RESPRST
    ; drive side: opening, error response 01/08, lid open, motor off
    li r15, D_OPENING
    call ds_set
    li r15, 1
    call dbusy
    li r15, 25000000
    if bit r9, 7
    else
        li r15, 13000000
    endif
    call drive_load_aged
    mov r15, r8
    andi r15, r15, 0x1D                    ; active bits and motor off
    ori r15, r15, 0x10                     ; lid open
    mov r8, r15
    call status_out
    li r3, 1
    out P_cdrw_err_drv, r3
    li r3, 8
    out P_cdrw_rsn_drv, r3
    li r15, S_errDrive
    out NOW_STROBE, r15
    b drv_stage
ev_lid:
    bclr r11, r11, B_REJPAUSE
    bclr r11, r11, B_WARMINIT
    bclr r11, r11, B_EARLYWARM
    bclr r11, r11, B_COASTING
    bclr r11, r11, B_RPTSEEK
    bclr r11, r11, B_RPTPAUSE
    bclr r11, r11, B_TAIL
    out T_MOTOR_OFF, r0
    bset r8, r8, 4
    b status_out

; ------------------------------------------------------------------ DRIVE event: shadows after the committed arm
drv_fire:
    mov r7, r10
    if r7 == D_SEEKLOG
        b .df_seek
    elif r7 == D_SEEKPHYS
        b .df_seek
    elif r7 == D_SEEKIMPL
        b .df_seek
    elif r7 == D_READING
        b .df_sector
    elif r7 == D_PLAYING
        b .df_sector
    elif r7 == D_OPENING
        li r15, 0
        b drv_startmotor
    endif
    ; speed change / spin-up / motor-on / session: idle
    li r15, D_IDLE
    call ds_set
    li r15, 0
    call dbusy
    if r7 != D_SPEEDTOC
        call clear_active
        in r1, IN_hasCD
        if r1 != 0
            bset r8, r8, 1
        endif
        call status_out
    endif
    ; MotorOn's second response waits for the motor (old WORK rule)
    if bit r11, B_WORKING
        if r14 == 7
            bclr r12, r12, B_WORKARMED
            b motoron_done
        endif
    endif
    ret
.df_seek:
    call seek_ok
    mov r6, r1
    call copy_subdata
    in r2, IN_cdrw_lrs_fire            ; old currentLBA <= lastReadSector on the fire clock
    st [r0+M_CURLBA], r2
    st [r0+M_PHYLBA], r2
    if r7 == D_SEEKLOG
        call nsub_isdata
        if r15 != 0
            bset r11, r11, B_HDRVALID
        endif
    endif
    andi r8, r8, 0x1F
    li r15, D_IDLE
    mov r10, r15
    bclr r11, r11, B_DBUSY
    if r6 != 0
        if bit r11, B_READAFTER
            bset r8, r8, 6
            bset r11, r11, B_AFTERSEEK
            bclr r11, r11, B_READAFTER
            call drv_start_reading
        elif bit r11, B_PLAYAFTER
            bset r11, r11, B_AFTERSEEK
            bclr r11, r11, B_PLAYAFTER
            call drv_start_playing
        elif r7 == D_SEEKIMPL
        elif bit r11, B_SEEKMUTE
        elif r7 == D_SEEKLOG
            ; reportSeekTarget: 1x, header valid, no LibCrypt
            in r1, IN_INSTANTSEEK
            in r2, IN_libcryptOn
            if bit r9, 7
            elif r1 != 0
            elif r2 != 0
            elif bit r11, B_HDRVALID
                bset r11, r11, B_RPTSEEK
            endif
        endif
    elif r7 != D_SEEKIMPL
        bclr r11, r11, B_HDRVALID
    endif
    ret
.df_sector:
    in r1, IN_fetchErr
    li r2, M_NSUB
    ld r2, [r2+1]
    li r3, 0xAA
    if r1 != 0
        bclr r11, r11, B_HDRVALID
        b .df_end
    elif r2 == r3
        b .df_end
    endif
    in r3, IN_isAudio
    if r3 != 0
        ld r4, [r0+M_TRACKBCD]
        if r4 == 0
            st [r0+M_TRACKBCD], r2
        elif r2 != r4
            if bit r9, 1
                b .df_end
            endif
        endif
    endif
    call copy_subdata
    in r4, IN_cdrw_lrs_fire
    st [r0+M_CURLBA], r4
    st [r0+M_PHYLBA], r4
    addi r5, r4, 1
    st [r0+M_READLBA], r5
    if r3 == 0
        if r7 == D_READING
            bset r11, r11, B_HDRVALID
            bset r8, r8, 5
            bclr r8, r8, 6
        endif
    else
        call cdda_report
    endif
    ret
.df_end:
    andi r8, r8, 0x1F
    li r15, D_IDLE
    mov r10, r15
    bclr r11, r11, B_DBUSY
    ret

copy_subdata:                              ; subdata shadow <= nextSubdata shadow
    li r1, M_NSUB
    li r3, 12
.cs_lp:
    ld r2, [r1+0]
    st [r1+12], r2
    addi r1, r1, 1
    addi r3, r3, -1
    if r3 != 0
        b .cs_lp
    endif
    ret
subdata_get:                               ; r15 = subdata(r15); clobbers r2 only
    li r2, M_SUBD
    add r2, r2, r15
    ld r15, [r2+0]
    ret

; CD-DA position report (old cmd process "CDDA reporting"), streamed one byte per clock
cdda_report:
    btst r5, 13
    beq .cd_ret
    if r10 != D_PLAYING
        ret
    endif
    btst r9, 2
    beq .cd_ret
    li r1, M_NSUB
    ld r2, [r1+9]
    shr4 r2, r2
    andi r2, r2, 0x0F
    ld r3, [r0+M_LASTCDDA]
    if r2 == r3
        ret
    endif
    st [r0+M_LASTCDDA], r2
    in r3, IN_CDROM_IRQFLAG
    in r4, IN_FifoResponse_Empty
    if r3 != 0
        ret
    elif r4 == 0
        ret
    elif bit r11, B_ACKARMED
        ret
    endif
    out SQ_CLR, r0
    mov r15, r8
    call sq_lit
    li r4, M_NSUB
    ld r15, [r4+1]
    call sq_lit
    ld r15, [r4+2]
    call sq_lit
    ld r2, [r4+9]
    btst r2, 4
    beq .cd_rel
    ld r15, [r4+3]
    call sq_lit
    ld r15, [r4+4]
    bset r15, r15, 7
    call sq_lit
    ld r15, [r4+5]
    call sq_lit
    b .cd_peak
.cd_rel:
    ld r15, [r4+7]
    call sq_lit
    ld r15, [r4+8]
    call sq_lit
    ld r15, [r4+9]
    call sq_lit
.cd_peak:
    ld r2, [r4+8]
    btst r2, 0
    beq .cd_l
    li r15, SRC_PEAKR_LO
    call sq_src
    li r15, SRC_PEAKR_HI
    call sq_src
    b .cd_go
.cd_l:
    li r15, SRC_PEAKL_LO
    call sq_src
    li r15, SRC_PEAKL_HI
    call sq_src
.cd_go:
    li r15, 2                              ; mode 10: one byte per clock, with a FIFO reset first
    out SQ_ARM, r15
.cd_ret:
    ret

; ------------------------------------------------------------------ WORK event (second responses)
work_fire:
    bclr r12, r12, B_WORKARMED
    bclr r11, r11, B_COASTING
    if bit r11, B_WORKING
    else
        ret
    endif
    if r14 == 7
        if r10 == D_MOTORON
            ret                            ; waits for the motor (drv_fire)
        endif
        b motoron_done
    endif
    bclr r11, r11, B_WORKING              ; the commit cleared the flag
    if r14 == 0x1A
        in r1, IN_hasCD
        if r1 != 0
            bset r8, r8, 1                 ; startMotorCMD
            call status_out
        endif
    endif
    b fwflag_sync
motoron_done:                              ; MotorOn: working off and driveAck now
    bclr r11, r11, B_WORKING
    li r15, S_driveAck
    out NOW_STROBE, r15
    li r15, FL_WORK
    out FLAG_CLR, r15
    b fwflag_sync

; stage the WORK commit for the current workCommand (driveAck / getIDAck, working off)
work_stage:
    li r15, S_driveAck
    if r14 == 0x1A
        li r15, S_getIDAck
    endif
    out C_WORK_STROBE, r15
    li r15, 0x40                           ; clear working
    out C_WORK_FLAG, r15
    ret
work_unstage:
    out C_WORK_STROBE, r0
    out C_WORK_FLAG, r0
    ret

; ------------------------------------------------------------------ command effects after the ACK (from cmd_exec)
ce_setloc:
    li r3, M_PBAD
    ld r3, [r3+0]
    if r3 != 0
        ret
    endif
    li r7, M_PARAMS
    ld r7, [r7+0]
    mov r15, r7
    call param_bcd                         ; minute
    mov r4, r15
    mov r15, r7
    shr4 r15, r15
    shr4 r15, r15
    call param_bcd                         ; second
    mov r5, r15
    mov r15, r7
    shr4 r15, r15
    shr4 r15, r15
    shr4 r15, r15
    shr4 r15, r15
    call param_bcd                         ; frame
    mov r6, r15
    ; seekLBA = minute * 4500 + second * 75 + frame
    li r1, 4500
    li r2, 0
.sl_m:
    if r4 != 0
        add r2, r2, r1
        addi r4, r4, -1
        b .sl_m
    endif
    li r1, 75
.sl_s:
    if r5 != 0
        add r2, r2, r1
        addi r5, r5, -1
        b .sl_s
    endif
    add r2, r2, r6
    st [r0+M_SEEKLBA], r2
    bset r11, r11, B_SETLOC
    li r3, 1
    out P_setLocActive, r3
    ret
param_bcd:                                 ; r15 = BCD byte (low 8 bits) as binary
    andi r1, r15, 0x7F
    btst r15, 7
    beq .pb_1
    bset r1, r1, 7
.pb_1:
    mov r15, r1
    shr4 r1, r1
    andi r15, r15, 0x0F
    shl1 r2, r1
    shl1 r3, r2
    shl1 r3, r3
    add r2, r2, r3                         ; hi * 10
    add r15, r15, r2
    ret

ce_play:
    bclr r11, r11, B_PLAYTRACK
    st [r0+M_NEXTTRACK], r0
    st [r0+M_FFRATE], r0
    call param0
    mov r4, r3                             ; track parameter
    li r3, M_PCOUNT
    ld r3, [r3+0]
    if r3 != 0
        li r3, 0                           ; parameter present
    else
        li r3, 1                           ; FIFO was empty
    endif
    if r3 == 0
        in r5, IN_trackInfo_DataOutA
        li r6, PREGAP
        add r5, r5, r6
        st [r0+M_PLAYLBA], r5
        bset r11, r11, B_PLAYTRACK
        st [r0+M_NEXTTRACK], r4
    endif
    li r6, 0                               ; no track parameter
    if r3 != 0
        li r6, 1
    elif r4 == 0
        li r6, 1
    endif
    if r6 != 0
        ; already playing (or seeking to play) at this position: keep going
        li r7, 1
        if bit r11, B_SETLOC
            ld r1, [r0+M_SEEKLBA]
            in r2, IN_lastReadSector
            if r1 != r2
                li r7, 0
            endif
        endif
        if r7 != 0
            if r10 == D_PLAYING
                b .cp_keep
            elif bit r11, B_PLAYAFTER
                if r10 == D_SEEKLOG
                    b .cp_keep
                elif r10 == D_SEEKPHYS
                    b .cp_keep
                elif r10 == D_SEEKIMPL
                    b .cp_keep
                endif
            endif
        endif
    endif
    li r1, 0x1F
    st [r0+M_LASTCDDA], r1
    if r6 != 0
        ld r1, [r0+M_CURLBA]
        st [r0+M_PLAYLBA], r1
    endif
    b drv_play
.cp_keep:
    ld r1, [r0+M_CURLBA]
    st [r0+M_PLAYLBA], r1
    ret

ce_ffw:                                    ; fastForwardRate kept as a signed 32-bit value
    if r10 != D_PLAYING
        bset r11, r11, B_PENDING           ; error keeps the command pending
        ret
    endif
    ld r3, [r0+M_FFRATE]
    if r1 == 4
        if bit r3, SIGNB
            li r3, 4
        elif r3 < 12
            addi r3, r3, 4
        endif
    else
        if bit r3, SIGNB
            li r5, 0xFFFFFFF4              ; -12
            if r3 > r5
                addi r3, r3, -4
            endif
        else
            li r3, 0xFFFFFFFC              ; -4
        endif
    endif
    st [r0+M_FFRATE], r3
    ret

ce_read:
    if r2 == 0
        ret
    endif
    in r3, IN_isAudioCD
    if r3 != 0
        if bit r9, 0
        else
            ret
        endif
    endif
    ; already reading (or seeking to read) the requested position
    li r7, 1
    if bit r11, B_SETLOC
        ld r1, [r0+M_SEEKLBA]
        in r2, IN_lastReadSector
        if r1 != r2
            li r7, 0
        endif
    endif
    if r7 != 0
        if r10 == D_READING
            b .cr_keep
        elif bit r11, B_READAFTER
            if r10 == D_SEEKLOG
                b .cr_keep
            elif r10 == D_SEEKPHYS
                b .cr_keep
            elif r10 == D_SEEKIMPL
                b .cr_keep
            endif
        endif
    endif
    ; stopped at 1x: the motor-start tail first (old motor process)
    call cond_bits
    btst r3, 3
    beq .cr_go
    in r1, IN_INSTANTSEEK
    if r1 == 0
        li r15, 52800000
        call motor_load_aged
    endif
.cr_go:
    b drv_readsn
.cr_keep:
    bclr r11, r11, B_SETLOC
    out P_setLocActive, r0
    ret

ce_motoron:
    if bit r8, 1
        ret
    endif
    if bit r11, B_WORKING
        ret
    endif
    bset r11, r11, B_WORKING
    mov r14, r1
    li r15, 399998
    call cond_bits
    btst r3, 2
    beq .mo_1
    in r1, IN_INSTANTSEEK
    if r1 == 0
        li r15, 0
    endif
.mo_1:
    call work_load_aged
    call fwflag_sync
    in r1, IN_hasCD
    if r1 != 0
        li r15, 1
        b drv_startmotor
    endif
    ret

ce_stop:
    li r15, 7651
    if bit r8, 1
        li r15, 12999998
        if bit r9, 7
            li r15, 24999998
        endif
    elif r14 == 8
        if bit r12, B_WORKARMED
        elif r10 == D_IDLE
            in r3, IN_hasCD
            in r4, IN_LIDopen
            in r5, IN_isAudioCD
            if r3 != 0
                if r4 == 0
                    if r5 == 0
                        li r15, 14780
                    endif
                endif
            endif
        endif
    endif
    ; coasting: a 1x data read stopped by Stop keeps the motor bit visible
    if bit r9, 7
    elif bit r8, 5
        if r10 == D_READING
            in r3, IN_LIDopen
            if r3 == 0
                bclr r11, r11, B_COASTING
                if bit r8, 1
                    bset r11, r11, B_COASTING
                endif
            endif
        endif
    endif
    bset r11, r11, B_WORKING
    li r1, 8
    mov r14, r1
    call work_load_aged
    call work_stage
    ; Stop clears the motor-start tail
    bclr r11, r11, B_TAIL
    out T_MOTOR_OFF, r0
    call fwflag_sync
    b drv_cmdstop

ce_pause:
    ; rejected (same test as the staging)
    li r3, 0
    if r10 == D_SEEKLOG
        li r3, 1
    elif r10 == D_SEEKPHYS
        li r3, 1
    elif r10 == D_READING
        if bit r8, 6
            li r3, 1
        endif
    elif r10 == D_PLAYING
        if bit r8, 6
            li r3, 1
        endif
    endif
    if r3 != 0
        if bit r11, B_READAFTER
            bset r11, r11, B_REJPAUSE
        elif r10 == D_READING
            bset r11, r11, B_REJPAUSE
        endif
        li r4, 1
        if bit r11, B_REJPAUSE
            out P_rejectedReadPause, r4
        endif
        if bit r11, B_READAFTER
        elif bit r11, B_PLAYAFTER
        elif r10 == D_READING
        elif r10 == D_PLAYING
        else
            bset r11, r11, B_SEEKMUTE       ; stop_afterseek
            call drv_stage
        endif
        ret
    endif
    bset r11, r11, B_WORKING
    li r1, 9
    mov r14, r1
    out P_cmdResetXa, r0
    ; second response time
    call cond_bits
    li r15, 7770
    btst r3, 0
    beq .pa_1
    li r15, 17000
.pa_1:
    if r10 == D_READING
        b .pa_rot
    elif r10 == D_PLAYING
        b .pa_rot
    endif
    b .pa_load
.pa_rot:
    ; target and minimum: data read 3,756,000 (1x) / 1,900,000 (2x); play: one rotation, psx-spx minimums
    li r4, 3756000
    if bit r9, 7
        li r4, 1900000
    endif
    mov r6, r4
    if r10 == D_PLAYING
        call pause_rot_ticks
        li r6, 2157295
        if bit r9, 7
            li r6, 1066874
        endif
    endif
    ; less the ticks already in this sector when driveDelay < driveDelayNext
    in r1, IN_TDRIVE
    call dnext_value
    if r1 < r15
        sub r2, r15, r1
        if r4 >= r2
            sub r4, r4, r2
        else
            li r4, 0
        endif
    endif
    mov r15, r4
    if r15 < r6
        mov r15, r6
    endif
    ; drive_stop with a 1x data read: one sector interval stays visible (pauseReadTail)
    if r10 == D_READING
        if bit r9, 7
        elif bit r8, 5
            in r1, IN_isAudio
            in r2, IN_hasCD
            in r3, IN_TDRIVE
            li r5, 451584
            if r1 == 0
                if r2 != 0
                    if r3 != 0
                        if r3 <= r5
                            mov r7, r15
                            in r15, IN_TDRIVE     ; motorStartClocks <= driveDelay + 1 on the same clock
                            addi r15, r15, TAIL_K
                            call motor_load
                            bset r11, r11, B_TAIL
                            mov r15, r7
                        endif
                    endif
                endif
            endif
        endif
    endif
.pa_load:
    call work_load_aged
    call work_stage
    call fwflag_sync
    b drv_drivestop
pause_rot_ticks:                          ; r4 = sectors per track (current LBA) * read speed
    ld r15, [r0+M_CURLBA]
    call spt_of
    mov r2, r15
    call readspeed
    li r4, 0
.prt:
    if r2 != 0
        add r4, r4, r15
        addi r2, r2, -1
        b .prt
    endif
    ret

ce_init:
    if bit r11, B_WORKING
        if r14 == 0x0A
            ret                             ; second pass
        endif
    endif
    bset r11, r11, B_PENDING                ; Init answers again after 24,999 clocks
    ; warm Init after a 1x data Pause
    call cond_bits
    btst r3, 0
    beq .in_1
    if r14 == 9
        if bit r12, B_WORKARMED
        else
            in r4, IN_INSTANTSEEK
            in r5, IN_hasCD
            in r6, IN_LIDopen
            in r7, IN_isAudioCD
            ld r1, [r0+M_CURLBA]
            if r4 == 0
                if r5 != 0
                    if r6 == 0
                        if r7 == 0
                            if r1 != 0
                                bset r11, r11, B_WARMINIT
                            endif
                        endif
                    endif
                endif
            endif
        endif
    endif
.in_1:
    ; workDelay: old work kept, + 476,407 (Init's sum); 4,100,000 when idle at LBA 0 with the motor on at 1x
    in r15, IN_TWORK
    li r1, 476407
    add r15, r15, r1
    if r14 == 0x0A
        if bit r12, B_WORKARMED
        else
            ld r2, [r0+M_CURLBA]
            in r3, IN_hasCD
            if r2 == 0
                if bit r8, 1
                    if r3 != 0
                        if bit r9, 7
                        else
                            li r15, 4099999   ; table load (the +2 below is for the direct sum)
                        endif
                    endif
                endif
            endif
        endif
    endif
    bset r11, r11, B_WORKING
    li r1, 0x0A
    mov r14, r1
    addi r15, r15, 2                      ; Init's sum is assigned directly (no table load clock)
    call work_load_aged
    call work_stage
    ; stopped at 1x: motor start tail
    btst r3, 3
    beq .in_2
    li r15, 52800000
    call motor_load_aged
.in_2:
    call drv_softreset
    call fwflag_sync
    ; second ACK after 24,999 clocks
    li r15, 24997
    in r1, IN_BCAGE                       ; less the clocks the old cmd_delay counted since this ACK event
    sub r15, r15, r1
    addi r15, r15, ACK_K
    out T_ACK, r15                        ; (24,997 is never below the age here)
    bset r11, r11, B_ACKARMED
    ret

ce_setfilter:
    call param0
    st [r0+M_XAFILE], r3
    out P_XaFilterFile, r3
    li r3, M_PARAMS
    ld r3, [r3+0]
    shr4 r3, r3
    shr4 r3, r3
    andi r1, r3, 0x7F
    btst r3, 7
    beq .sf_1
    bset r1, r1, 7
.sf_1:
    st [r0+M_XACHAN], r1
    out P_XaFilterChannel, r1
    out P_ClearXACurrentSet, r0
    ret

ce_session:
    call param0
    if r2 == 0
        ret
    elif r10 == D_READING
        ret
    elif r10 == D_PLAYING
        ret
    endif
    st [r0+M_SESSION], r3
    b drv_setsession

ce_seek:
    out P_cmdResetXa, r0
    bclr r11, r11, B_SETLOC
    out P_setLocActive, r0
    li r15, 0
    b drv_seek

ce_getid:                                  ; r15 = work time; bytes at WORK counts 9..2, reset at 10
    bset r11, r11, B_WORKING
    call work_load_aged
    call work_stage
    out SQ_CLR, r0
    in r1, IN_isAudioCD
    in r2, IN_hasCD
    mov r15, r8
    if r1 != 0
        ori r15, r15, 8
    elif r2 == 0
        ori r15, r15, 8
    endif
    if r2 != 0
        ori r15, r15, 2
    endif
    call sq_lit
    li r15, 0
    if r1 != 0
        li r15, 0x90
    endif
    if r2 == 0
        ori r15, r15, 0x40
    endif
    call sq_lit
    li r15, 0x20
    call sq_lit
    li r15, 0
    call sq_lit
    li r15, 0x53
    call sq_lit
    li r15, 0x43
    call sq_lit
    li r15, 0x45
    call sq_lit
    in r3, IN_region
    li r15, 0x41
    if r3 == 1
        li r15, 0x49
    elif r3 == 2
        li r15, 0x45
    elif r3 == 3
        call sq_skip                       ; no region letter
        b .gi_end
    endif
    call sq_lit
.gi_end:
    call sq_skip                           ; count 1: nothing
    li r15, 0x25                           ; mode 01, timer WORK, reset at 10
    out SQ_ARM, r15
    b fwflag_sync

; ------------------------------------------------------------------ motor start tail (old motor process)
motor_load_aged:                           ; r15 = clocks from the event that caused the load
    in r1, IN_AGE
    sub r15, r15, r1
    addi r15, r15, MOTOR_AGE_K
motor_load:                                ; r15 = clocks
    out T_MOTOR, r15
    b fwflag_sync
