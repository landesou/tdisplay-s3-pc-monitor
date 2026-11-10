#pragma once

#include <stdint.h>

typedef uint32_t TickType_t;
typedef int      BaseType_t;
typedef unsigned UBaseType_t;

#define portMAX_DELAY   0xFFFFFFFFu
#define pdTRUE          1
#define pdFALSE         0
#define pdPASS          1
#define pdFAIL          0
#define pdMS_TO_TICKS(x) ((TickType_t)(x))
