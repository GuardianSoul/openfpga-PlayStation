; cd_subcpu firmware: subchannel Q (old data-process readSubchannel FSM 3496-3592, LibCrypt skip 3411-3430)
; and the GetLocP physical position model (old drive process 3046-3130).

; sector fetched: Q for lastReadSector + 2, unless LibCrypt hides it
subq_sector:
    in r4, IN_lastReadSector
    addi r4, r4, 2
    in r5, IN_libcryptKey
    if r5 != 0
        li r6, lc_tab
        li r7, 15
.lc_lp:
        btst r5, 15
        beq .lc_next
        ldt r1, [r6+0]
        if r4 == r1
            ret
        endif
        addi r1, r1, 5                      ; pairs are 5 sectors apart
        if r4 == r1
            ret
        endif
.lc_next:
        shl1 r5, r5
        addi r6, r6, 1
        addi r7, r7, -1
        btst r7, SIGNB
        beq .lc_lp
    endif
    b subq_calc
lc_tab:                                      ; key bit 15 .. 0: first sector of each pair (+2 already applied)
    .word 14105, 14231, 14485, 14579, 14649, 14899, 15056, 15130
    .word 15242, 15312, 15378, 15628, 15919, 16031, 16101, 16167

; Q for sector r4 -> nextSubdata(0..11) (ports and shadow)
subq_calc:
    li r7, M_NSUB
    in r1, IN_isAudio
    li r15, 0
    if r1 == 0
        li r15, 0x40
    endif
    st [r7+0], r15
    out P_nextSubdata0, r15
    in r15, IN_trackNumberBCD
    st [r7+1], r15
    out P_nextSubdata1, r15
    ; relative time: t = sector - start; index 01 after the 2 s pregap, else index 00 counting down
    in r2, IN_startLBA
    sub r1, r4, r2
    li r3, PREGAP
    sub r1, r1, r3
    li r15, 1
    btst r1, SIGNB
    beq .sq_pos
    li r15, 0
    li r3, 0xFFFFFFFF
    xor r1, r1, r3                           ; PREGAP - t - 1
.sq_pos:
    st [r7+2], r15
    out P_nextSubdata2, r15
    call msf_bcd
    st [r7+3], r1
    out P_nextSubdata3, r1
    st [r7+4], r2
    out P_nextSubdata4, r2
    st [r7+5], r3
    out P_nextSubdata5, r3
    mov r1, r4
    call msf_bcd
    st [r7+7], r1
    out P_nextSubdata7, r1
    st [r7+8], r2
    out P_nextSubdata8, r2
    st [r7+9], r3
    out P_nextSubdata9, r3
    ret

; r1 = frames -> r1 minutes, r2 seconds, r3 frames (BCD)
msf_bcd:
    li r5, 4500
    li r6, 0
.mb_m:
    if r1 >= r5
        sub r1, r1, r5
        addi r6, r6, 1
        b .mb_m
    endif
    li r5, 75
    li r2, 0
.mb_s:
    if r1 >= r5
        sub r1, r1, r5
        addi r2, r2, 1
        b .mb_s
    endif
    mov r3, r1
    mov r15, r6
    call bcd8
    mov r1, r15
    mov r15, r2
    call bcd8
    mov r2, r15
    mov r15, r3
    call bcd8
    mov r3, r15
    ret
bcd8:                                        ; r15 (0..99) -> BCD
    li r5, 0
.b8:
    if r15 >= 10
        addi r15, r15, -10
        addi r5, r5, 16
        b .b8
    endif
    add r15, r15, r5
    ret

; ------------------------------------------------------------------ GetLocP physical position (updatePhysicalPosition)
phys_update:
    bclr r11, r11, B_PHYUPD
    in r1, IN_hasCD
    in r2, IN_LIDopen
    ; 1x seek target still parked: Q at the target
    if bit r11, B_RPTSEEK
        if r10 == D_IDLE
            if r1 != 0
                if r2 == 0
                    if bit r9, 7
                    else
                        bclr r11, r11, B_RPTSEEK
                        ld r4, [r0+M_PHYLBA]
                        b .pu_q
                    endif
                endif
            endif
        endif
    endif
    ; 1x data Pause: the buffered next LBA supplies the report
    if bit r11, B_RPTPAUSE
        bclr r11, r11, B_RPTPAUSE
        call pause_report_ok
        if r15 != 0
            in r4, IN_lastReadSector
            b .pu_q
        endif
    endif
    ; rotate the physical position one sector within its track (rama SPT table)
    ld r15, [r0+M_CURLBA]
    call spt_of
    mov r3, r15                               ; spt
    ld r1, [r0+M_CURLBA]
    mov r2, r1                                ; phy_base
    if r1 >= r3
        sub r2, r1, r3
        addi r2, r2, 1
    endif
    ld r4, [r0+M_PHYLBA]
    if r4 < r2
        mov r4, r2
    endif
    sub r5, r4, r2
    andi r5, r5, 31                            ; phy_oldOffset
    addi r5, r5, 1                             ; wrapSectorOffset
    li r6, 4
.pu_w:
    if r5 >= r3
        sub r5, r5, r3
    endif
    addi r6, r6, -1
    if r6 != 0
        b .pu_w
    endif
    add r5, r2, r5                             ; new physical LBA
    st [r0+M_PHYLBA], r5
    if r5 == r4
        ret
    endif
    mov r4, r5
.pu_q:
    call subq_calc
    call copy_subdata
    li r15, S_copySubdata
    out NOW_STROBE, r15
    bset r12, r12, B2_STREAM               ; GetLocP's bytes changed
    ret
pause_report_ok:                              ; old reportPauseNext conditions -> r15
    li r15, 0
    if r14 != 9
        ret
    elif bit r12, B_WORKARMED
        ret
    elif r10 != D_IDLE
        ret
    elif bit r9, 7
        ret
    endif
    in r1, IN_hasCD
    in r2, IN_LIDopen
    in r3, IN_CDROM_IRQFLAG
    in r4, IN_pendingDriveIRQ
    in r5, IN_fetchErr
    in r6, IN_sectorFetchState
    in r7, IN_readOnDisk_buffer
    if r1 == 0
        ret
    elif r2 != 0
        ret
    elif r3 != 0
        ret
    elif r4 != 0
        ret
    elif r5 != 0
        ret
    elif r6 != SF_IDLE
        ret
    elif r7 != 0
        ret
    endif
    in r1, IN_isAudio
    in r2, IN_startLBA
    in r3, IN_endLBA
    in r4, IN_lastReadSector
    ld r5, [r0+M_CURLBA]
    if r1 != 0
        ret
    elif r5 < r2
        ret
    elif r4 > r3
        ret
    endif
    addi r5, r5, 1
    if r4 == r5
        li r15, 1
    endif
    ret
