#pragma once

/* 强制包含的兼容层：MinGW 8.1 的 string.h 没有 strlcpy */
#include <stddef.h>

size_t pcm_strlcpy(char *dst, const char *src, size_t size);
#define strlcpy pcm_strlcpy
