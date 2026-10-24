"""Host regression test for portrait-scan framebuffer ownership and presentation."""

from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


class DsiScanBufferingTest(unittest.TestCase):
    def test_partial_updates_never_write_the_scanning_buffer(self):
        compiler = shutil.which("cc")
        if compiler is None:
            self.skipTest("host C compiler unavailable")
        path = (Path(__file__).resolve().parents[1] /
                "ports/esp32/board_common/src/board_init.c")
        source = path.read_text()
        start = source.index("void board_display_portrait_scan_blit(")
        end = source.index("\n}", start) + 2
        function = source[start:end]
        callback_start = source.index("static bool dpi_color_trans_done_cb(")
        callback_end = source.index("\n}", callback_start) + 2
        callback = source[callback_start:callback_end]
        harness = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include <string.h>
#define BOARD_LCD_H_RES 4
#define BOARD_LCD_V_RES 6
#define portMAX_DELAY 1000
#define pdMS_TO_TICKS(x) (x)
#define ESP_CACHE_MSYNC_FLAG_DIR_M2C 1
#define ESP_OK 0
#define ESP_ERROR_CHECK(x) assert((x) == ESP_OK)
typedef int SemaphoreHandle_t;
typedef int BaseType_t;
typedef int esp_lcd_panel_handle_t;
typedef void esp_lcd_dpi_panel_event_data_t;
#define pdFALSE 0
#define pdTRUE 1
static bool in_isr;
static unsigned done_token, isr_signals;
static bool xPortInIsrContext(void) { return in_isr; }
static bool dpi_color_trans_done_cb(esp_lcd_panel_handle_t panel,
                                    esp_lcd_dpi_panel_event_data_t *edata,
                                    void *user_ctx);
static SemaphoreHandle_t s_dsi_blit_mutex = 1;
static SemaphoreHandle_t s_dsi_blit_done = 2;
static SemaphoreHandle_t dpi_flush_sem = 3;
static uint16_t storage[2][24];
static uint16_t *s_dsi_panel_fb[2] = {storage[0], storage[1]};
static unsigned s_dsi_front_idx = 0;
static int panel_handle;
static unsigned scanning = 0, pending = 0, refreshes = 0;
static unsigned draws = 0, locked = 0;
static uint16_t snapshot[24];
static int xSemaphoreTake(SemaphoreHandle_t sem, unsigned timeout) {
    if (sem == s_dsi_blit_done) {
        if (timeout) { assert(done_token); }
        done_token = 0;
    }
    if (sem == s_dsi_blit_mutex) { assert(!locked); locked = 1; }
    if (sem == dpi_flush_sem && timeout) {
        assert(locked);
        /* Model an already-in-flight refresh before the page flip takes effect. */
        if (++refreshes == 2) { scanning = pending; }
    }
    return 1;
}
static int xSemaphoreGive(SemaphoreHandle_t sem) {
    if (sem == s_dsi_blit_done) {
        assert(!in_isr); done_token = 1; return 1;
    }
    assert(sem == s_dsi_blit_mutex && locked);
    assert(refreshes >= 2 && scanning == pending);
    locked = 0;
    return 1;
}
static int xSemaphoreGiveFromISR(SemaphoreHandle_t sem, BaseType_t *woken) {
    assert(in_isr && sem == s_dsi_blit_done);
    done_token = 1; isr_signals++; *woken = pdTRUE; return 1;
}
static int esp_cache_msync(void *ptr, size_t size, unsigned flags) {
    assert(locked && ptr == storage[scanning]);
    assert(size == sizeof(storage[0]) && flags == ESP_CACHE_MSYNC_FLAG_DIR_M2C);
    return ESP_OK;
}
static int esp_lcd_panel_draw_bitmap(int panel, int x1, int y1,
                                    int x2, int y2, const void *buffer) {
    (void)panel;
    assert(locked);
    /* A partial external-buffer draw would overwrite the scanning framebuffer. */
    assert(buffer == storage[scanning ^ 1]);
    assert(x1 == 0 && y1 == 0 && x2 == 4 && y2 == 6);
    assert(memcmp(storage[scanning], snapshot, sizeof(snapshot)) == 0);
    pending = scanning ^ 1;
    refreshes = 0;
    draws++;
    assert(!dpi_color_trans_done_cb(panel, NULL, NULL));
    return ESP_OK;
}
'''
        cases = r'''
static void update(int x1, int y1, int x2, int y2, const uint16_t *pixels) {
    memcpy(snapshot, storage[scanning], sizeof(snapshot));
    board_display_portrait_scan_blit(x1, y1, x2, y2, pixels);
    assert(!locked && s_dsi_front_idx == scanning);
}
int main(void) {
    for (unsigned i = 0; i < 24; i++) { storage[0][i] = i + 1; }
    memset(storage[1], 0xaa, sizeof(storage[1]));
    const uint16_t camera[8] = {100,101,102,103,104,105,106,107};
    update(0, 2, 4, 4, camera);
    assert(memcmp(storage[scanning] + 8, camera, sizeof(camera)) == 0);
    assert(memcmp(storage[scanning], snapshot, 8 * sizeof(uint16_t)) == 0);
    assert(memcmp(storage[scanning] + 16, snapshot + 16, 8 * sizeof(uint16_t)) == 0);
    /* A letterbox dirty rectangle must preserve the live camera and other chrome. */
    const uint16_t chrome[4] = {201,202,203,204};
    update(1, 0, 3, 2, chrome);
    assert(storage[scanning][1] == 201 && storage[scanning][2] == 202);
    assert(storage[scanning][5] == 203 && storage[scanning][6] == 204);
    assert(storage[scanning][0] == snapshot[0]);
    assert(storage[scanning][3] == snapshot[3]);
    assert(memcmp(storage[scanning] + 8, camera, sizeof(camera)) == 0);
    /* Repeated updates reuse only retired buffers and never resurrect stale chrome. */
    for (unsigned i = 0; i < 20; i++) {
        update(0, 2, 4, 4, camera);
        assert(storage[scanning][1] == 201 && storage[scanning][6] == 204);
    }
    assert(draws == 22 && isr_signals == 0);
    in_isr = true;
    assert(dpi_color_trans_done_cb(panel_handle, NULL, NULL));
    assert(isr_signals == 1 && done_token == 1);
    in_isr = false;
    return 0;
}
'''
        with tempfile.TemporaryDirectory() as directory:
            c_file = Path(directory) / "dsi_scan_buffering.c"
            executable = Path(directory) / "dsi_scan_buffering"
            c_file.write_text(harness + callback + function + cases)
            subprocess.run([compiler, "-std=c99", "-Wall", "-Wextra",
                            "-Wno-unused-function", "-Wno-unused-variable",
                            str(c_file), "-o", str(executable)], check=True)
            subprocess.run([str(executable)], check=True, timeout=10)


if __name__ == "__main__":
    unittest.main()
