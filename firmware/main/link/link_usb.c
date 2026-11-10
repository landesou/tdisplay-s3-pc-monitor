/* USB CDC（USB-Serial/JTAG）行读取 + JSON 解析。
 * 由固件链路同学实现，接口由 link.c 调用。
 * 参考：docs/design/architecture.md §2 帧格式 */

#include "link_internal.h"

esp_err_t link_usb_start(void) { return ESP_OK; }
