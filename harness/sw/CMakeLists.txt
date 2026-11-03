# Host benchmark of the vhsnunzip Coyote harness.
#   cmake -S harness/sw -B build-sw [-DEN_SIM=ON] && cmake --build build-sw
cmake_minimum_required(VERSION 3.16)
project(vhsnunzip_bench CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
if(NOT CMAKE_BUILD_TYPE)
    set(CMAKE_BUILD_TYPE Release CACHE STRING "Build type" FORCE)
endif()

option(EN_SIM "Link against the Coyote simulation library" OFF)

get_filename_component(VSZ_REPO_DIR "${CMAKE_CURRENT_SOURCE_DIR}/../.." ABSOLUTE)
set(CYT_DIR "" CACHE PATH "Coyote checkout (default: $CYT_DIR, <repo>/coyote, or a Coyote checkout next to the repo)")
if(NOT CYT_DIR)
    foreach(candidate "$ENV{CYT_DIR}" "${VSZ_REPO_DIR}/coyote" "${VSZ_REPO_DIR}/../Coyote")
        if(candidate AND EXISTS "${candidate}/sw/CMakeLists.txt")
            get_filename_component(candidate "${candidate}" ABSOLUTE)
            set(CYT_DIR "${candidate}" CACHE PATH "Coyote checkout" FORCE)
            break()
        endif()
    endforeach()
endif()
if(NOT EXISTS "${CYT_DIR}/sw/CMakeLists.txt")
    message(FATAL_ERROR "Coyote not found; pass -DCYT_DIR=<path to Coyote>")
endif()

if(EN_SIM)
    add_subdirectory(${CYT_DIR}/sim/sw ${CMAKE_BINARY_DIR}/coyote)
else()
    add_subdirectory(${CYT_DIR}/sw ${CMAKE_BINARY_DIR}/coyote)
endif()

add_executable(vhsnunzip_bench src/main.cpp)
target_link_libraries(vhsnunzip_bench PUBLIC Coyote)
if(EN_SIM)
    target_compile_definitions(vhsnunzip_bench PRIVATE EN_SIM)
endif()
