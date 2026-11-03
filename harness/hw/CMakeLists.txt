# Coyote vFPGA harness for vhsnunzip_unbuffered: throughput counters, timing and
# utilization. See harness/README.md.
#
#   cmake -S harness/hw -B build-hw -DFDEV_NAME=u55c [-DVHSNUNZIP_DUAL_ISSUE=ON] \
#         [-DEN_UCLK=1 -DUCLK_F=300]
#   make -C build-hw project bitgen     # bitstream
#   make -C build-hw sim                # simulation project for unit-tests/
cmake_minimum_required(VERSION 3.16)
project(vhsnunzip_harness)

get_filename_component(VSZ_REPO_DIR "${CMAKE_CURRENT_SOURCE_DIR}/../.." ABSOLUTE)

# -- Coyote ---------------------------------------------------------------------
set(CYT_DIR "" CACHE PATH "Coyote checkout (default: $CYT_DIR, <repo>/coyote, or a Coyote checkout next to the repo)")
if(NOT CYT_DIR)
    foreach(candidate "$ENV{CYT_DIR}" "${VSZ_REPO_DIR}/coyote" "${VSZ_REPO_DIR}/../Coyote")
        if(candidate AND EXISTS "${candidate}/cmake/FindCoyoteHW.cmake")
            get_filename_component(candidate "${candidate}" ABSOLUTE)
            set(CYT_DIR "${candidate}" CACHE PATH "Coyote checkout" FORCE)
            break()
        endif()
    endforeach()
endif()
if(NOT EXISTS "${CYT_DIR}/cmake/FindCoyoteHW.cmake")
    message(FATAL_ERROR "Coyote not found; pass -DCYT_DIR=<path to Coyote>")
endif()

set(CMAKE_MODULE_PATH ${CMAKE_MODULE_PATH} ${CYT_DIR}/cmake)
find_package(CoyoteHW REQUIRED)

if(FDEV_NAME STREQUAL "0")
    set(FDEV_NAME u55c)
endif()
set(N_REGIONS 1)
set(EN_STRM 1)
set(N_STRM_AXI 1)
set(UNIT_TEST_DIR "${CMAKE_CURRENT_SOURCE_DIR}/unit-tests")

validation_checks_hw()

# -- Core configuration ---------------------------------------------------------
set(VHSNUNZIP_SRC_DIR "${VSZ_REPO_DIR}/src/hdl" CACHE PATH "vhsnunzip RTL directory")
option(VHSNUNZIP_DUAL_ISSUE "Build the dual-issue datapath (vhsnunzip_pipeline_dual)" OFF)
option(VHSNUNZIP_LONG_CHUNKS "Support chunks larger than 64 KiB" ON)
set(VHSNUNZIP_RAM_STYLE "ultra" CACHE STRING "History RAM primitive: ultra or block")
option(VHSNUNZIP_STAGE_SYMLINKS "Stage the RTL as symlinks (OFF: copies, refreshed on reconfigure)" ON)
get_filename_component(VHSNUNZIP_SRC_DIR "${VHSNUNZIP_SRC_DIR}" ABSOLUTE)
set(VHSNUNZIP_BYTES "" CACHE STRING "Override C_BYTES of vhsnunzip_utils_pkg (empty keeps the RTL value)")

if(NOT VHSNUNZIP_RAM_STYLE MATCHES "^(ultra|block)$")
    message(FATAL_ERROR "VHSNUNZIP_RAM_STYLE must be ultra or block")
endif()

# -- Stage the core sources -----------------------------------------------------
# Coyote adds every file in <src dir>/hdl, but src/hdl also holds testbenches,
# simulation models and 8-byte-only toplevels. The build therefore uses a
# staged copy: symlinks to the RTL (so edits are picked up without
# reconfiguring), a patched vhsnunzip_utils_pkg.vhd if C_BYTES is overridden,
# and the generated harness package. Re-run cmake after adding files.
set(VSZ_STAGE_DIR "${CMAKE_BINARY_DIR}/vhsnunzip_src")
file(REMOVE_RECURSE "${VSZ_STAGE_DIR}")
file(MAKE_DIRECTORY "${VSZ_STAGE_DIR}/hdl")

file(STRINGS "${CMAKE_CURRENT_SOURCE_DIR}/sources.exclude" VSZ_EXCLUDES REGEX "^[^#]")
file(GLOB VSZ_CORE_FILES CONFIGURE_DEPENDS "${VHSNUNZIP_SRC_DIR}/*.vhd")
if(NOT VSZ_CORE_FILES)
    message(FATAL_ERROR "No VHDL files in VHSNUNZIP_SRC_DIR=${VHSNUNZIP_SRC_DIR}")
endif()

set(VSZ_BYTES "")
foreach(src ${VSZ_CORE_FILES})
    get_filename_component(name "${src}" NAME)
    set(excluded OFF)
    foreach(pattern ${VSZ_EXCLUDES})
        string(STRIP "${pattern}" pattern)
        string(REPLACE "." "\\." regex "${pattern}")
        string(REPLACE "*" ".*" regex "${regex}")
        if(name MATCHES "^${regex}$")
            set(excluded ON)
        endif()
    endforeach()
    if(excluded)
        continue()
    endif()

    if(name STREQUAL "vhsnunzip_utils_pkg.vhd")
        file(READ "${src}" pkg)
        if(NOT pkg MATCHES "constant C_BYTES[ \t]*:[ \t]*positive[ \t]*:=[ \t]*([0-9]+)")
            message(FATAL_ERROR "Cannot find C_BYTES in ${src}")
        endif()
        set(VSZ_BYTES ${CMAKE_MATCH_1})
        if(VHSNUNZIP_BYTES AND NOT VHSNUNZIP_BYTES EQUAL VSZ_BYTES)
            string(REGEX REPLACE "(constant C_BYTES[ \t]*:[ \t]*positive[ \t]*:=[ \t]*)[0-9]+"
                   "\\1${VHSNUNZIP_BYTES}" pkg "${pkg}")
            file(WRITE "${VSZ_STAGE_DIR}/hdl/${name}" "${pkg}")
            set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS "${src}")
            set(VSZ_BYTES ${VHSNUNZIP_BYTES})
            continue()
        endif()
    endif()
    if(VHSNUNZIP_STAGE_SYMLINKS)
        file(CREATE_LINK "${src}" "${VSZ_STAGE_DIR}/hdl/${name}" SYMBOLIC)
    else()
        configure_file("${src}" "${VSZ_STAGE_DIR}/hdl/${name}" COPYONLY)
    endif()
endforeach()

if(NOT VSZ_BYTES MATCHES "^(8|16|32|64)$")
    message(FATAL_ERROR "C_BYTES=${VSZ_BYTES} is not supported (8, 16, 32 or 64)")
endif()

if(VHSNUNZIP_DUAL_ISSUE)
    set(VSZ_DUAL_ISSUE 1)
else()
    set(VSZ_DUAL_ISSUE 0)
endif()
if(VHSNUNZIP_LONG_CHUNKS)
    set(VSZ_LONG_CHUNKS 1)
else()
    set(VSZ_LONG_CHUNKS 0)
endif()
if(VHSNUNZIP_RAM_STYLE STREQUAL "block")
    set(VSZ_RAM_BLOCK 1)
else()
    set(VSZ_RAM_BLOCK 0)
endif()
configure_file("${CMAKE_CURRENT_SOURCE_DIR}/cmake/vhsnunzip_harness_pkg.sv.in"
               "${VSZ_STAGE_DIR}/hdl/vhsnunzip_harness_pkg.sv" @ONLY)

# Record the configuration for scripts/summarize.py
execute_process(COMMAND git -C "${VSZ_REPO_DIR}" rev-parse --short HEAD
                OUTPUT_VARIABLE VSZ_GIT_REV OUTPUT_STRIP_TRAILING_WHITESPACE ERROR_QUIET)
execute_process(COMMAND git -C "${VSZ_REPO_DIR}" status --porcelain --untracked-files=no -- src
                OUTPUT_VARIABLE VSZ_GIT_DIRTY OUTPUT_STRIP_TRAILING_WHITESPACE ERROR_QUIET)
if(VSZ_GIT_DIRTY)
    set(VSZ_GIT_REV "${VSZ_GIT_REV}-dirty")
endif()
if(EN_UCLK)
    set(VSZ_CLK_MHZ ${UCLK_F})
else()
    set(VSZ_CLK_MHZ ${ACLK_F})
endif()
file(WRITE "${CMAKE_BINARY_DIR}/harness_config.json"
"{
  \"flow\": \"coyote\",
  \"fdev\": \"${FDEV_NAME}\",
  \"c_bytes\": ${VSZ_BYTES},
  \"dual_issue\": ${VSZ_DUAL_ISSUE},
  \"long_chunks\": ${VSZ_LONG_CHUNKS},
  \"ram_style\": \"${VHSNUNZIP_RAM_STYLE}\",
  \"clk_mhz\": ${VSZ_CLK_MHZ},
  \"git\": \"${VSZ_GIT_REV}\"
}
")

message("** vhsnunzip harness: C_BYTES=${VSZ_BYTES} DUAL_ISSUE=${VSZ_DUAL_ISSUE} "
        "LONG_CHUNKS=${VSZ_LONG_CHUNKS} RAM_STYLE=${VHSNUNZIP_RAM_STYLE} clock=${VSZ_CLK_MHZ} MHz")

# -- Coyote project -------------------------------------------------------------
# Source directories are relative to this directory; the first holds vfpga_top.svh.
file(RELATIVE_PATH VSZ_STAGE_REL "${CMAKE_CURRENT_SOURCE_DIR}" "${VSZ_STAGE_DIR}")
load_apps(
    VFPGA_C0_0 "src ${VSZ_STAGE_REL}"
)

create_hw()
