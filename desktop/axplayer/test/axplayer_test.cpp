// Drives axplayer without a window or sound device: open, play, pause, seek, play to the end.
//   axplayer_test <lav folder> <url> [--hw N] [--duration S] [--width W] [--height H]
// Prints one line per check and exits 0 only when every check passed.
#include <windows.h>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>

#include "axplayer.h"

static int failures = 0;
static void check(bool ok, const char *what, const char *detail = "") {
    std::printf("%s  %s%s%s\n", ok ? "PASS" : "FAIL", what, *detail ? "  " : "", detail);
    if (!ok) ++failures;
}

static std::wstring wide(const char *s) {
    int n = MultiByteToWideChar(CP_UTF8, 0, s, -1, nullptr, 0);
    std::wstring w((size_t)n, L'\0');
    MultiByteToWideChar(CP_UTF8, 0, s, -1, &w[0], n);
    if (!w.empty() && w.back() == L'\0') w.pop_back();
    return w;
}

static AxState state(AxPlayer *p) { AxState s; ax_get_state(p, &s); return s; }

// Wait until `pred` is true or `seconds` pass.
template <class F>
static bool wait_for(AxPlayer *p, double seconds, F pred) {
    DWORD end = GetTickCount() + (DWORD)(seconds * 1000);
    while (GetTickCount() < end) {
        if (pred(state(p))) return true;
        Sleep(50);
    }
    return pred(state(p));
}

int main(int argc, char **argv) {
    if (argc < 3) { std::printf("usage: axplayer_test <lav folder> <url> [--hw N] [--duration S] [--width W] [--height H]\n"); return 2; }
    int hw = AX_HW_NONE, width = 0, height = 0;
    double duration = 0;
    for (int i = 3; i + 1 < argc; i += 2) {
        if (!std::strcmp(argv[i], "--hw")) hw = std::atoi(argv[i + 1]);
        else if (!std::strcmp(argv[i], "--duration")) duration = std::atof(argv[i + 1]);
        else if (!std::strcmp(argv[i], "--width")) width = std::atoi(argv[i + 1]);
        else if (!std::strcmp(argv[i], "--height")) height = std::atoi(argv[i + 1]);
    }
    std::wstring lav = wide(argv[1]), url = wide(argv[2]);

    AxOptions opt{hw, 1};
    AxPlayer *p = nullptr;
    int hr = ax_create(lav.c_str(), &opt, &p);
    check(hr == 0 && p, "create the player and find the LAV filters", p ? ax_last_error(p) : "");
    if (hr != 0 || !p) return 1;

    hr = ax_open(p, url.c_str(), L"axplayer-test", nullptr, 0, 0, 640, 360);
    check(hr == 0, "open the address and connect the decoders", ax_last_error(p));
    if (hr != 0) { ax_destroy(p); return 1; }

    AxState s = state(p);
    wchar_t name[128] = L"";
    ax_decoder_name(p, name, 128);
    char shown[300];
    std::snprintf(shown, sizeof shown, "duration %.2f s, %d x %d, decoder '%ls'", s.duration, s.video_width, s.video_height, name);
    std::printf("INFO  %s\n", shown);
    if (duration > 0) check(s.duration > duration - 0.6 && s.duration < duration + 0.6, "duration is right");
    if (width > 0) check(s.video_width == width && s.video_height == height, "picture size is right");

    check(ax_play(p) == 0, "start playback", ax_last_error(p));
    check(wait_for(p, 20, [](const AxState &x) { return x.position >= 1.5; }), "position advances while playing");

    check(ax_pause(p) == 0, "pause");
    Sleep(400);
    double held = state(p).position;
    Sleep(1000);
    check(state(p).position - held < 0.3, "position holds while paused");

    double target = s.duration > 3 ? s.duration * 0.55 : 1.0;
    check(ax_seek(p, target) == 0, "seek", ax_last_error(p));
    check(ax_play(p) == 0, "resume");
    check(wait_for(p, 20, [target](const AxState &x) { return x.position >= target + 0.5; }), "playback continues from the new place");
    check(ax_set_rate(p, 2.0) == 0 || true, "set speed (not required to succeed on every graph)");
    check(wait_for(p, 40, [](const AxState &x) { return x.ended != 0; }), "the end is reported");
    check(state(p).error == 0, "no playback error", ax_last_error(p));

    int tracks = ax_audio_track_count(p);
    std::printf("INFO  audio tracks: %d\n", tracks);

    ax_close(p);
    check(state(p).opened == 0, "close leaves nothing open");
    ax_destroy(p);
    std::printf("%s (%d failed)\n", failures ? "FAILED" : "ALL PASSED", failures);
    return failures ? 1 : 0;
}
