library ieee;
use ieee.std_logic_1164.all;

library work;
use work.vhsnunzip_utils_pkg.all;

-- Harness adapter around vhsnunzip_unbuffered. Exposes the boolean/string
-- generics of the core as naturals, because those map reliably from
-- SystemVerilog parameters in both Vivado synthesis and xsim. The port list is
-- identical to vhsnunzip_unbuffered; the widths follow C_BYTES.
--
-- The core is instantiated as core_inst. The report scripts locate it as
-- */inst_vhsnunzip/core_inst, so keep both instance names stable.
entity vhsnunzip_harness_core is
  generic (
    DUAL_ISSUE  : natural := 0;  -- 0: vhsnunzip_pipeline, 1: vhsnunzip_pipeline_dual
    LONG_CHUNKS : natural := 1;  -- 1: chunks > 64 KiB supported
    RAM_BLOCK   : natural := 0   -- 0: UltraRAM, 1: block RAM
  );
  port (
    clk       : in  std_logic;
    reset     : in  std_logic;

    co_valid  : in  std_logic;
    co_ready  : out std_logic;
    co_data   : in  std_logic_vector(C_BYTES*8-1 downto 0);
    co_cnt    : in  std_logic_vector(C_IDX-1 downto 0);
    co_last   : in  std_logic;

    de_valid  : out std_logic;
    de_ready  : in  std_logic;
    de_dvalid : out std_logic;
    de_data   : out std_logic_vector(C_BYTES*8-1 downto 0);
    de_cnt    : out std_logic_vector(C_CNT-1 downto 0);
    de_last   : out std_logic
  );
end vhsnunzip_harness_core;

architecture rtl of vhsnunzip_harness_core is

  function ram_style(block_ram : natural) return string is
  begin
    if block_ram /= 0 then
      return "block";
    end if;
    return "ultra";
  end function;

begin

  core_inst: entity work.vhsnunzip_unbuffered
    generic map (
      LONG_CHUNKS => LONG_CHUNKS /= 0,
      DUAL_ISSUE  => DUAL_ISSUE /= 0,
      RAM_STYLE   => ram_style(RAM_BLOCK)
    )
    port map (
      clk       => clk,
      reset     => reset,
      co_valid  => co_valid,
      co_ready  => co_ready,
      co_data   => co_data,
      co_cnt    => co_cnt,
      co_last   => co_last,
      de_valid  => de_valid,
      de_ready  => de_ready,
      de_dvalid => de_dvalid,
      de_data   => de_data,
      de_cnt    => de_cnt,
      de_last   => de_last
    );

end rtl;
