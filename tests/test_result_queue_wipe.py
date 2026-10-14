"""Host regression tests for wiping consumed and cleared result-queue storage."""

from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


class ResultQueueWipeTest(unittest.TestCase):
    def test_consumed_and_cleared_slots_are_zero(self):
        compiler = shutil.which("cc")
        if compiler is None:
            self.skipTest("host C compiler unavailable")
        source = (Path(__file__).resolve().parents[1] /
                  "bindings/modseedsigner_bindings.c").read_text()
        queue = source[source.index("#define SEEDSIGNER_RESULT_QUEUE_CAP"):
                       source.index("void seedsigner_lvgl_on_button_selected")]
        poll = source[source.index("static mp_obj_t mp_seedsigner_lvgl_poll_for_result(void)"):
                      source.index("static MP_DEFINE_CONST_FUN_OBJ_0(seedsigner_lvgl_poll_for_result_obj")]
        clear = source[source.index("static mp_obj_t mp_seedsigner_lvgl_clear_result_queue(void)"):
                       source.index("static MP_DEFINE_CONST_FUN_OBJ_0(seedsigner_lvgl_clear_result_queue_obj")]
        harness = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include <string.h>
#include <stdlib.h>
typedef unsigned portMUX_TYPE;
#define portMUX_INITIALIZER_UNLOCKED 0
#define portENTER_CRITICAL(lock) do { assert(*(lock) == 0); *(lock) = 1; } while (0)
#define portEXIT_CRITICAL(lock) do { assert(*(lock) == 1); *(lock) = 0; } while (0)
typedef uintptr_t mp_obj_t;
typedef unsigned qstr;
#define mp_const_none 0
#define MP_QSTR_text_entered 1
#define MP_QSTR_qr_brightness 2
#define MP_QSTR_qr_density 3
#define MP_QSTR_button_selected 4
#define MP_OBJ_NEW_QSTR(x) (x)
#define MP_ERROR_TEXT(x) (x)
static char returned[256];
static mp_obj_t mp_obj_new_int_from_uint(uint32_t x) { return x; }
static mp_obj_t mp_obj_new_str(const char *s, size_t n) {
    memcpy(returned, s, n); returned[n] = 0; return 1;
}
static mp_obj_t mp_obj_new_tuple(size_t n, const mp_obj_t *items) {
    assert(n == 3); return items[0];
}
static void mp_raise_ValueError(const char *message) { (void)message; abort(); }
static bool all_zero(const void *memory, size_t size) {
    const unsigned char *bytes = memory;
    for (size_t i = 0; i < size; i++) { if (bytes[i]) { return false; } }
    return true;
}
'''
        cases = r'''
int main(int argc, char **argv) {
    assert(argc == 2);
    if (strcmp(argv[1], "poll") == 0) {
        /* A consumed secret is wiped without destroying the next queued result. */
        seedsigner_result_enqueue(SEEDSIGNER_EVENT_TEXT_ENTERED, 0, "secret passphrase");
        seedsigner_result_enqueue(SEEDSIGNER_EVENT_TEXT_ENTERED, 0, "mnemonic word");
        assert(mp_seedsigner_lvgl_poll_for_result() == MP_QSTR_text_entered);
        assert(strcmp(returned, "secret passphrase") == 0);
        assert(all_zero(&s_result_queue[0], sizeof(s_result_queue[0])));
        assert(strcmp(s_result_queue[1].label, "mnemonic word") == 0);
        assert(mp_seedsigner_lvgl_poll_for_result() == MP_QSTR_text_entered);
        assert(strcmp(returned, "mnemonic word") == 0);
        assert(all_zero(s_result_queue, sizeof(s_result_queue)));
        /* Continue through wraparound. */
        for (unsigned i = 0; i < 64; i++) {
            uint32_t slot = s_result_tail;
            seedsigner_result_enqueue(SEEDSIGNER_EVENT_TEXT_ENTERED, 0, "secret");
            assert(mp_seedsigner_lvgl_poll_for_result() == MP_QSTR_text_entered);
            assert(all_zero(&s_result_queue[slot], sizeof(s_result_queue[slot])));
        }
    } else {
        /* Clear all slots, including inactive storage left by earlier activity. */
        memset(s_result_queue, 0x5a, sizeof(s_result_queue));
        s_result_head = 2; s_result_tail = 5; s_result_count = 3;
        assert(mp_seedsigner_lvgl_clear_result_queue() == mp_const_none);
        assert(all_zero(s_result_queue, sizeof(s_result_queue)));
        assert(s_result_head == 0 && s_result_tail == 0 && s_result_count == 0);
        /* Clearing an apparently empty queue must also scrub stale memory. */
        memset(s_result_queue, 0x5a, sizeof(s_result_queue));
        mp_seedsigner_lvgl_clear_result_queue();
        assert(all_zero(s_result_queue, sizeof(s_result_queue)));
        seedsigner_result_enqueue(SEEDSIGNER_EVENT_TEXT_ENTERED, 0, "fresh");
        assert(mp_seedsigner_lvgl_poll_for_result() == MP_QSTR_text_entered);
        assert(strcmp(returned, "fresh") == 0);
    }
    assert(s_result_lock == 0);
    return 0;
}
'''
        with tempfile.TemporaryDirectory() as directory:
            c_file = Path(directory) / "result_queue_wipe.c"
            executable = Path(directory) / "result_queue_wipe"
            c_file.write_text(harness + queue + poll + clear + cases)
            subprocess.run([compiler, "-std=c99", "-O2", "-flto", "-Wall", "-Wextra",
                            "-Werror", str(c_file), "-o", str(executable)], check=True)
            for operation in ("poll", "clear"):
                with self.subTest(operation=operation):
                    subprocess.run([str(executable), operation], check=True)


if __name__ == "__main__":
    unittest.main()
