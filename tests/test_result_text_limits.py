"""Host regression test for the native result queue's text byte limit.

Run with: python3 -m unittest discover -s tests -p test_result_text_limits.py
Requires a host C compiler; no firmware or MicroPython runtime needed.
"""

from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


class ResultTextLimitsTest(unittest.TestCase):
    def test_text_is_preserved_or_explicitly_rejected(self):
        compiler = shutil.which("cc")
        if compiler is None:
            self.skipTest("host C compiler unavailable")
        source = (Path(__file__).resolve().parents[1] /
                  "bindings/modseedsigner_bindings.c").read_text()
        queue = source[source.index("#define SEEDSIGNER_RESULT_QUEUE_CAP"):
                       source.index("void seedsigner_lvgl_on_button_selected")]
        poll = source[source.index("static mp_obj_t mp_seedsigner_lvgl_poll_for_result(void)"):
                      source.index("static MP_DEFINE_CONST_FUN_OBJ_0(seedsigner_lvgl_poll_for_result_obj")]
        harness = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <string.h>
#include <setjmp.h>
#include <stddef.h>
typedef uintptr_t mp_obj_t;
typedef unsigned int qstr;
#define mp_const_none 0
#define MP_QSTR_text_entered 1
#define MP_QSTR_qr_brightness 2
#define MP_QSTR_qr_density 3
#define MP_QSTR_button_selected 4
#define MP_OBJ_NEW_QSTR(x) (x)
#define MP_ERROR_TEXT(x) (x)
static jmp_buf raised;
static char returned[2048];
static mp_obj_t mp_obj_new_int_from_uint(uint32_t x) { return x; }
static mp_obj_t mp_obj_new_str(const char *s, size_t n) {
    memcpy(returned, s, n);
    returned[n] = 0;
    return 1;
}
static mp_obj_t mp_obj_new_tuple(size_t n, const mp_obj_t *items) {
    assert(n == 3);
    return items[0];
}
static void mp_raise_ValueError(const char *message) {
    assert(strstr(message, "255") != NULL);
    longjmp(raised, 1);
}
'''
        cases = r'''
static void check(const char *input, bool reject) {
    s_result_head = s_result_tail = s_result_count = 0;
    strcpy(returned, "unchanged");
    seedsigner_result_enqueue(SEEDSIGNER_EVENT_TEXT_ENTERED, 0, input);
    if (setjmp(raised) == 0) {
        assert(mp_seedsigner_lvgl_poll_for_result() == MP_QSTR_text_entered);
        assert(!reject);
        assert(strcmp(returned, input ? input : "") == 0);
    } else {
        assert(reject);
        assert(strcmp(returned, "unchanged") == 0);
    }
    assert(s_result_count == 0);
    assert(mp_seedsigner_lvgl_poll_for_result() == mp_const_none);
}
int main(void) {
    char input[1025];
    check(NULL, false);
    check("", false);
    check("passphrase", false);
    memset(input, 'a', sizeof(input));
    input[255] = 0;
    check(input, false);
    input[255] = 'a'; input[256] = 0;
    check(input, true);
    memset(input, 'a', 1024); input[1024] = 0;
    check(input, true);
    for (size_t i = 0; i < 256; i += 2) {
        input[i] = (char)0xc3; input[i + 1] = (char)0xa9;
    }
    input[254] = 0;
    check(input, false);
    input[254] = (char)0xc3; input[256] = 0;
    check(input, true);
    /* Existing button-label truncation remains unchanged. */
    seedsigner_result_enqueue(SEEDSIGNER_EVENT_BUTTON_SELECTED, 7, input);
    assert(mp_seedsigner_lvgl_poll_for_result() == MP_QSTR_button_selected);
    assert(strlen(returned) == 255);
    return 0;
}
'''
        with tempfile.TemporaryDirectory() as directory:
            c_file = Path(directory) / "result_text_limits.c"
            executable = Path(directory) / "result_text_limits"
            c_file.write_text(harness + queue + poll + cases)
            subprocess.run([compiler, "-std=c99", "-Wall", "-Wextra", "-Werror",
                            str(c_file), "-o", str(executable)], check=True)
            subprocess.run([str(executable)], check=True)


if __name__ == "__main__":
    unittest.main()
