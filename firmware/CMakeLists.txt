cmake_minimum_required(VERSION 3.16)

include($ENV{IDF_PATH}/tools/cmake/project.cmake)
# 只编译 main 及其依赖，缩短构建时间
idf_build_set_property(MINIMAL_BUILD ON)
project(tdisplay_pcmon)
