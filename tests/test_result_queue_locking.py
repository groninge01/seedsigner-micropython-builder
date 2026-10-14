"""Host tests for result-queue interleavings and concurrent access.

Run with: python3 -m unittest discover -s tests -p test_result_queue_locking.py
FreeRTOS critical sections are modeled by a pthread mutex.
"""

from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


class ResultQueueLockingTest(unittest.TestCase):
    def test_queue_interleaving_and_concurrent_access(self):
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
        # Force a producer at the exact point between the consumer's copy and updates.
        poll = poll.replace("seedsigner_result_event_t ev = s_result_queue[s_result_head];",
                            "seedsigner_result_event_t ev = s_result_queue[s_result_head];\n"
                            "    interleave_producer();")
        harness = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <string.h>
#include <stdio.h>
#include <stdlib.h>
#include <pthread.h>
#include <sched.h>
typedef pthread_mutex_t portMUX_TYPE;
#define portMUX_INITIALIZER_UNLOCKED PTHREAD_MUTEX_INITIALIZER
static _Thread_local unsigned critical_depth;
#define portENTER_CRITICAL(lock) do { assert(critical_depth == 0); \
    assert(pthread_mutex_lock(lock) == 0); critical_depth++; } while (0)
#define portEXIT_CRITICAL(lock) do { assert(critical_depth == 1); \
    critical_depth--; assert(pthread_mutex_unlock(lock) == 0); } while (0)
typedef uintptr_t mp_obj_t;
typedef unsigned int qstr;
#define mp_const_none 0
#define MP_QSTR_text_entered 1
#define MP_QSTR_qr_brightness 2
#define MP_QSTR_qr_density 3
#define MP_QSTR_button_selected 4
#define MP_OBJ_NEW_QSTR(x) (x)
#define MP_ERROR_TEXT(x) (x)
static _Thread_local uint32_t returned_index;
static _Thread_local char returned_label[256];
static bool inject, deferred;
static void interleave_producer(void);
static mp_obj_t mp_obj_new_int_from_uint(uint32_t x) {
    assert(critical_depth == 0); returned_index = x; return x;
}
static mp_obj_t mp_obj_new_str(const char *s, size_t n) {
    assert(critical_depth == 0);
    memcpy(returned_label, s, n); returned_label[n] = 0; return 1;
}
static mp_obj_t mp_obj_new_tuple(size_t n, const mp_obj_t *items) {
    assert(critical_depth == 0 && n == 3); return items[0];
}
static void mp_raise_ValueError(const char *message) {
    (void)message; assert(critical_depth == 0); abort();
}
'''
        cases = r'''
static void enqueue(uint32_t index) {
    char label[32];
    snprintf(label, sizeof(label), "%u", (unsigned)index);
    seedsigner_result_enqueue(SEEDSIGNER_EVENT_BUTTON_SELECTED, index, label);
}
static void interleave_producer(void) {
    if (!inject) { return; }
    inject = false;
    if (critical_depth) { deferred = true; } else { enqueue(16); }
}
static void check_result(void) {
    mp_obj_t result = mp_seedsigner_lvgl_poll_for_result();
    assert(critical_depth == 0);
    if (result != mp_const_none) {
        assert(result == MP_QSTR_button_selected);
        assert(strtoul(returned_label, NULL, 10) == returned_index);
    }
}
static void *producer(void *arg) {
    uint32_t offset = (uint32_t)(uintptr_t)arg;
    for (uint32_t i = 0; i < 100000; i++) {
        enqueue(offset + i);
        if (i % 32 == 0) { sched_yield(); }
    }
    return NULL;
}
static void *consumer(void *arg) {
    (void)arg;
    for (unsigned i = 0; i < 100000; i++) { check_result(); }
    return NULL;
}
static void *clearer(void *arg) {
    (void)arg;
    for (unsigned i = 0; i < 10000; i++) {
        mp_seedsigner_lvgl_clear_result_queue();
        assert(critical_depth == 0);
        sched_yield();
    }
    return NULL;
}
int main(void) {
    /* Poll event 0 while a producer adds event 16 to a full queue.
       Both operations must survive: events 1..16 remain, with no lost event 1. */
    for (uint32_t i = 0; i < 16; i++) { enqueue(i); }
    inject = true;
    check_result();
    assert(returned_index == 0);
    if (deferred) { enqueue(16); }
    assert(s_result_count == 16);
    for (uint32_t i = 1; i <= 16; i++) {
        check_result(); assert(returned_index == i);
    }
    assert(mp_seedsigner_lvgl_poll_for_result() == mp_const_none);
    /* Keep the existing drop-oldest policy when full. */
    for (uint32_t i = 0; i < 32; i++) { enqueue(i); }
    for (uint32_t i = 16; i < 32; i++) {
        check_result(); assert(returned_index == i);
    }
    pthread_t threads[4];
    assert(pthread_create(&threads[0], NULL, producer, (void *)(uintptr_t)1000000) == 0);
    assert(pthread_create(&threads[1], NULL, producer, (void *)(uintptr_t)2000000) == 0);
    assert(pthread_create(&threads[2], NULL, consumer, NULL) == 0);
    assert(pthread_create(&threads[3], NULL, clearer, NULL) == 0);
    for (unsigned i = 0; i < 4; i++) { assert(pthread_join(threads[i], NULL) == 0); }
    assert(s_result_count <= SEEDSIGNER_RESULT_QUEUE_CAP);
    while (s_result_count) { check_result(); }
    mp_seedsigner_lvgl_clear_result_queue();
    assert(mp_seedsigner_lvgl_poll_for_result() == mp_const_none);
    return 0;
}
'''
        with tempfile.TemporaryDirectory() as directory:
            c_file = Path(directory) / "result_queue_locking.c"
            executable = Path(directory) / "result_queue_locking"
            c_file.write_text(harness + queue + poll + clear + cases)
            subprocess.run([compiler, "-std=c11", "-Wall", "-Wextra", "-Werror",
                            "-pthread", str(c_file), "-o", str(executable)], check=True)
            subprocess.run([str(executable)], check=True, timeout=30)


if __name__ == "__main__":
    unittest.main()
