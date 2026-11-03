library ieee;
use ieee.std_logic_1164.all;

library work;
use work.vhsnunzip_utils_pkg.all;

-- Out-of-context timing top: vhsnunzip_harness_core inside a ring of
-- registers, so every port path of the core is a timed register-to-register
-- path and nothing is optimized away. Not a functional design (the ring
-- breaks the handshakes); for timing and utilization only.
entity vhsnunzip_harness_ooc is
  generic (
    DUAL_ISSUE  : natural := 0;
    LONG_CHUNKS : natural := 1;
    RAM_BLOCK   : natural := 0
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
end vhsnunzip_harness_ooc;

architecture rtl of vhsnunzip_harness_ooc is
  signal reset_r, co_valid_r, co_ready_i, co_last_r, de_ready_r : std_logic;
  signal de_valid_i, de_dvalid_i, de_last_i                      : std_logic;
  signal co_data_r, de_data_i : std_logic_vector(C_BYTES*8-1 downto 0);
  signal co_cnt_r             : std_logic_vector(C_IDX-1 downto 0);
  signal de_cnt_i             : std_logic_vector(C_CNT-1 downto 0);
begin

  ring_proc: process (clk) is
  begin
    if rising_edge(clk) then
      reset_r    <= reset;
      co_valid_r <= co_valid;
      co_data_r  <= co_data;
      co_cnt_r   <= co_cnt;
      co_last_r  <= co_last;
      de_ready_r <= de_ready;
      co_ready   <= co_ready_i;
      de_valid   <= de_valid_i;
      de_dvalid  <= de_dvalid_i;
      de_data    <= de_data_i;
      de_cnt     <= de_cnt_i;
      de_last    <= de_last_i;
    end if;
  end process;

  inst_vhsnunzip: entity work.vhsnunzip_harness_core
    generic map (
      DUAL_ISSUE  => DUAL_ISSUE,
      LONG_CHUNKS => LONG_CHUNKS,
      RAM_BLOCK   => RAM_BLOCK
    )
    port map (
      clk       => clk,
      reset     => reset_r,
      co_valid  => co_valid_r,
      co_ready  => co_ready_i,
      co_data   => co_data_r,
      co_cnt    => co_cnt_r,
      co_last   => co_last_r,
      de_valid  => de_valid_i,
      de_ready  => de_ready_r,
      de_dvalid => de_dvalid_i,
      de_data   => de_data_i,
      de_cnt    => de_cnt_i,
      de_last   => de_last_i
    );

end rtl;
