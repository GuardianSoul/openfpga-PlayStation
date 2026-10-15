library std;
use std.env.all;
use work.cd_arithmetic.all;

entity tb_cd_arithmetic is end entity;
architecture test of tb_cd_arithmetic is
begin
    process
        variable count_spt,count_negative,count_offset : natural := 0;
        variable old_result,new_result : integer;
    begin
        for lba in 0 to 524287 loop
            assert originalSPT(lba)=adaptedSPT(lba)
                report "Track sectors mismatch LBA=" & integer'image(lba) severity failure;
            count_spt:=count_spt+1;
        end loop;
        -- Also check truncation toward zero, although the controller's callers
        -- use nonnegative, bounded currentLBA. Do not silently change semantics.
        for lba in -9001 to -1 loop
            assert originalSPT(lba)=adaptedSPT(lba)
                report "Negative track sectors mismatch LBA=" & integer'image(lba) severity failure;
            count_negative:=count_negative+1;
        end loop;
        assert originalSPT(integer'low)=adaptedSPT(integer'low) severity failure;
        assert originalSPT(integer'high)=adaptedSPT(integer'high) severity failure;
        for offset in 0 to 31 loop
            for sectors in 8 to 22 loop
                old_result:=(offset+1) mod sectors;
                new_result:=wrapSectorOffset(offset,sectors);
                assert old_result=new_result and new_result>=0 and new_result<sectors
                    report "Rotation mismatch offset=" & integer'image(offset) & " sectors=" & integer'image(sectors) severity failure;
                count_offset:=count_offset+1;
            end loop;
        end loop;
        report "PASS CD arithmetic nonnegative_LBAs=" & natural'image(count_spt) &
            " negative_LBAs=" & natural'image(count_negative) & " offsets=" & natural'image(count_offset);
        finish;
    end process;
end architecture;
