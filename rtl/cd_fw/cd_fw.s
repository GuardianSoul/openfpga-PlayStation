; cd_subcpu firmware (cd-rewrite-061). Assemble: scripts/cd_fw_asm.py rtl/cd_fw/cd_fw.s --hex OUT --depth 1536
.include "defs.s"
.include "main.s"
.include "cmd.s"
.include "drive.s"
.include "subq.s"
