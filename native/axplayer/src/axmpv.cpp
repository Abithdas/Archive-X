// axmpv: the libmpv implementation of include/axplayer.h. It is a drop-in for axplayer.cpp (the LAV Filters + DirectShow one): the same C functions, the same
// window layers (a child behind or in front of the page, or the borderless window owned by the app window), so crates/native-player, the Tauri commands and the
// page do not change. Build it instead of axplayer.cpp with `cmake -DAX_ENGINE=mpv`; the "lav_dir" argument is then the folder with libmpv-2.dll.
//
// libmpv is loaded from that folder at run time (LoadLibrary), never linked, so nothing on the PC changes and the build needs only mpv's client.h.
// One worker thread owns the mpv handle and the video window and runs the window's message loop; the C functions hand it commands (see axplayer.h).
#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>

#include <algorithm>
#include <atomic>
#include <chrono>
#include <climits>
#include <condition_variable>
#include <cstdio>
#include <cstring>
#include <deque>
#include <functional>
#include <future>
#include <memory>
#include <mutex>
#include <sstream>
#include <string>
#include <thread>
#include <vector>

#include "axplayer.h"
#include "mpv/client.h"

namespace {

const wchar_t kVideoClass[] = L"AxMpvVideo";

LRESULT CALLBACK video_proc(HWND h, UINT m, WPARAM w, LPARAM l) {
    if (m == WM_ERASEBKGND) {
        RECT rc;
        GetClientRect(h, &rc);
        FillRect(reinterpret_cast<HDC>(w), &rc, reinterpret_cast<HBRUSH>(GetStockObject(BLACK_BRUSH)));
        return 1;
    }
    if (m == WM_NCHITTEST) return HTTRANSPARENT;   // clicks go to whatever is behind: the page decides what a click means
    return DefWindowProcW(h, m, w, l);
}

// The mpv window lives inside ours and is made by mpv's own thread: it too lets clicks through (best effort; see ax_state / docs).
WNDPROC g_mpv_proc = nullptr;
LRESULT CALLBACK mpv_child_proc(HWND h, UINT m, WPARAM w, LPARAM l) {
    if (m == WM_NCHITTEST) return HTTRANSPARENT;
    return CallWindowProcW(g_mpv_proc, h, m, w, l);
}

std::string utf8(const std::wstring &w) {
    if (w.empty()) return std::string();
    int n = WideCharToMultiByte(CP_UTF8, 0, w.data(), static_cast<int>(w.size()), nullptr, 0, nullptr, nullptr);
    std::string s(static_cast<size_t>(std::max(n, 0)), '\0');
    if (n > 0) WideCharToMultiByte(CP_UTF8, 0, w.data(), static_cast<int>(w.size()), &s[0], n, nullptr, nullptr);
    return s;
}

std::wstring wide(const std::string &s) {
    if (s.empty()) return std::wstring();
    int n = MultiByteToWideChar(CP_UTF8, 0, s.data(), static_cast<int>(s.size()), nullptr, 0);
    std::wstring w(static_cast<size_t>(std::max(n, 0)), L'\0');
    if (n > 0) MultiByteToWideChar(CP_UTF8, 0, s.data(), static_cast<int>(s.size()), &w[0], n);
    return w;
}

// The libmpv functions this engine uses.
struct Api {
    HMODULE dll = nullptr;
    decltype(&mpv_create) create = nullptr;
    decltype(&mpv_initialize) initialize = nullptr;
    decltype(&mpv_terminate_destroy) destroy = nullptr;
    decltype(&mpv_set_option_string) set_option = nullptr;
    decltype(&mpv_set_property_string) set_property = nullptr;
    decltype(&mpv_command) command = nullptr;
    decltype(&mpv_get_property) get_property = nullptr;
    decltype(&mpv_free) free_ = nullptr;
    decltype(&mpv_wait_event) wait_event = nullptr;
    decltype(&mpv_error_string) error_string = nullptr;

    bool load(const std::wstring &dir, std::string &why) {
        if (dll) return true;
        std::wstring d = dir;
        if (!d.empty() && d.back() != L'\\' && d.back() != L'/') d += L'\\';
        for (const wchar_t *name : {L"libmpv-2.dll", L"mpv-2.dll", L"libmpv.dll"}) {
            dll = LoadLibraryExW((d + name).c_str(), nullptr, LOAD_WITH_ALTERED_SEARCH_PATH);   // its own DLLs are looked up next to it
            if (dll) break;
        }
        if (!dll) { why = "libmpv-2.dll could not be loaded from " + utf8(dir) + " (error " + std::to_string(GetLastError()) + ")"; return false; }
        auto sym = [&](const char *n) { return reinterpret_cast<void *>(GetProcAddress(dll, n)); };
#define AX_SYM(field, name) field = reinterpret_cast<decltype(field)>(sym(name))
        AX_SYM(create, "mpv_create"); AX_SYM(initialize, "mpv_initialize"); AX_SYM(destroy, "mpv_terminate_destroy");
        AX_SYM(set_option, "mpv_set_option_string"); AX_SYM(set_property, "mpv_set_property_string"); AX_SYM(command, "mpv_command");
        AX_SYM(get_property, "mpv_get_property"); AX_SYM(free_, "mpv_free"); AX_SYM(wait_event, "mpv_wait_event"); AX_SYM(error_string, "mpv_error_string");
#undef AX_SYM
        if (!create || !initialize || !destroy || !set_option || !set_property || !command || !get_property || !free_ || !wait_event || !error_string) {
            why = "libmpv-2.dll does not have the functions this player needs";
            FreeLibrary(dll);
            dll = nullptr;
            return false;
        }
        return true;
    }
};

const char *hwdec_for(int hw) {
    switch (hw) {
        case AX_HW_NONE: return "no";
        case AX_HW_CUDA: return "nvdec";
        case AX_HW_QUICKSYNC: return "qsv";
        case AX_HW_DXVA2_COPYBACK: return "dxva2-copy";
        case AX_HW_DXVA2_NATIVE: return "dxva2";
        case AX_HW_D3D11: return "d3d11va";
        default: return "auto-safe";
    }
}

}  // namespace

struct AxPlayer {
    std::wstring lib_dir;
    AxOptions opt{};

    // ---- worker thread ----
    std::thread thread;
    std::mutex jobs_mu;
    std::condition_variable jobs_cv;
    std::deque<std::function<void()>> jobs;
    bool quit = false;

    // ---- state shared with callers ----
    std::mutex state_mu;
    AxState state{};
    std::string error;
    std::wstring decoder;

    // ---- owned by the worker thread only ----
    Api api;
    mpv_handle *h = nullptr;
    bool loaded = false;        // MPV_EVENT_FILE_LOADED arrived
    bool failed = false;        // the file could not be opened or played
    std::wstring url, user_agent;
    HWND window = nullptr;
    bool own_window = false;
    HWND parent_hwnd = nullptr;
    bool class_registered = false;
    bool child_hooked = false;
    std::atomic<bool> page_hid{false};
    std::atomic<HWND> hide_target{nullptr};
    int last_x = 0, last_y = 0, last_w = 1, last_h = 1;
    int popup_x = INT_MIN, popup_y = INT_MIN;
    int volume = 100;
    bool muted = false;
    double rate = 1.0;

    // ----------------------------------------------------------------------------------------------------------------
    void note(const std::string &s) { std::lock_guard<std::mutex> g(state_mu); error = s; }
    void fail_with(const std::string &s, int code) {
        std::lock_guard<std::mutex> g(state_mu);
        error = s;
        state.error = code ? code : -1;
    }

    void start() { thread = std::thread([this] { run(); }); }

    void run() {
        for (;;) {
            std::function<void()> job;
            {
                std::unique_lock<std::mutex> lk(jobs_mu);
                jobs_cv.wait_for(lk, std::chrono::milliseconds(20), [this] { return quit || !jobs.empty(); });
                if (quit) break;
                if (!jobs.empty()) { job = std::move(jobs.front()); jobs.pop_front(); }
            }
            if (job) job();
            tick();
            pump();
        }
        close_player();
        if (api.dll) FreeLibrary(api.dll);
    }

    static void pump() {
        MSG msg;
        while (PeekMessageW(&msg, nullptr, 0, 0, PM_REMOVE)) { TranslateMessage(&msg); DispatchMessageW(&msg); }
    }

    int call(std::function<int()> fn) {
        auto task = std::make_shared<std::packaged_task<int()>>(std::move(fn));
        auto fut = task->get_future();
        {
            std::lock_guard<std::mutex> lk(jobs_mu);
            jobs.emplace_back([task] { (*task)(); });
        }
        jobs_cv.notify_one();
        if (fut.wait_for(std::chrono::seconds(45)) != std::future_status::ready) return HRESULT_FROM_WIN32(ERROR_TIMEOUT);
        return fut.get();
    }

    // ---- mpv properties -------------------------------------------------------------------------------------------
    bool get_double(const char *name, double &v) { return h && api.get_property(h, name, MPV_FORMAT_DOUBLE, &v) >= 0; }
    bool get_int(const char *name, int64_t &v) { return h && api.get_property(h, name, MPV_FORMAT_INT64, &v) >= 0; }
    bool get_flag(const char *name, bool &v) {
        int f = 0;
        if (!h || api.get_property(h, name, MPV_FORMAT_FLAG, &f) < 0) return false;
        v = f != 0;
        return true;
    }
    std::string get_string(const std::string &name) {
        char *s = nullptr;
        if (!h || api.get_property(h, name.c_str(), MPV_FORMAT_STRING, &s) < 0 || !s) return std::string();
        std::string out = s;
        api.free_(s);
        return out;
    }
    int set_prop(const char *name, const std::string &value) { return h ? api.set_property(h, name, value.c_str()) : MPV_ERROR_UNINITIALIZED; }
    int cmd(std::initializer_list<const char *> args) {
        if (!h) return MPV_ERROR_UNINITIALIZED;
        std::vector<const char *> v(args);
        v.push_back(nullptr);
        return api.command(h, v.data());
    }

    // ---- the video window (the same layers as axplayer.cpp) ---------------------------------------------------------
    bool popup() const { return opt.video_layer == AX_LAYER_POPUP && parent_hwnd != nullptr; }
    bool under() const { return opt.video_layer == AX_LAYER_UNDER && parent_hwnd != nullptr; }
    bool toplevel() const { return popup() || under(); }       // its own window, not a child of the page
    // the under layer: the app window is owned by the video window while it plays (an owned window is always above its owner), and what it was before is put back
    bool owner_set = false;
    LONG_PTR prev_owner = 0;
    LONG_PTR prev_exstyle = 0;
    HWND owned_app = nullptr;

    void own_the_app() {
        if (!under() || !window || owner_set) return;
        owned_app = parent_hwnd;
        prev_owner = GetWindowLongPtrW(owned_app, GWLP_HWNDPARENT);
        prev_exstyle = GetWindowLongPtrW(owned_app, GWL_EXSTYLE);
        SetWindowLongPtrW(owned_app, GWL_EXSTYLE, prev_exstyle | WS_EX_APPWINDOW);       // an owned window has no taskbar button otherwise
        SetWindowLongPtrW(owned_app, GWLP_HWNDPARENT, reinterpret_cast<LONG_PTR>(window));
        owner_set = true;
        SetWindowPos(owned_app, HWND_TOP, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE);
    }

    // Must run before the video window is destroyed: destroying an owner destroys what it owns, and that is the app window.
    void release_the_app() {
        if (!owner_set) return;
        if (owned_app && IsWindow(owned_app)) {
            SetWindowLongPtrW(owned_app, GWLP_HWNDPARENT, prev_owner);
            SetWindowLongPtrW(owned_app, GWL_EXSTYLE, prev_exstyle);
            SetWindowPos(owned_app, nullptr, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE | SWP_FRAMECHANGED);
        }
        owner_set = false;
        owned_app = nullptr;
    }

    void round_corners() {
        if (!window) return;
        double scale = 1.0;
        if (auto fn = reinterpret_cast<UINT (WINAPI *)(HWND)>(reinterpret_cast<void *>(GetProcAddress(GetModuleHandleW(L"user32.dll"), "GetDpiForWindow"))))
            if (parent_hwnd) { UINT dpi = fn(parent_hwnd); if (dpi) scale = dpi / 96.0; }
        int radius = static_cast<int>(22.0 * scale);
        if (parent_hwnd) { RECT pc{}; if (GetClientRect(parent_hwnd, &pc) && last_w >= (pc.right - pc.left) - 2) radius = 0; }
        radius = std::min(radius, std::min(last_w, last_h) / 2);
        if (radius < 2) { SetWindowRgn(window, nullptr, TRUE); return; }
        SetWindowRgn(window, CreateRoundRectRgn(0, 0, last_w + 1, last_h + 1, radius * 2, radius * 2), TRUE);
    }

    void place(int x, int y, int w, int h_) {
        last_x = x; last_y = y; last_w = std::max(w, 1); last_h = std::max(h_, 1);
        if (!window) return;
        page_hid.store(false);
        if (toplevel()) {
            POINT pt{x, y};
            ClientToScreen(parent_hwnd, &pt);
            popup_x = pt.x; popup_y = pt.y;
            SetWindowPos(window, nullptr, pt.x, pt.y, last_w, last_h, SWP_NOZORDER | SWP_NOACTIVATE | SWP_SHOWWINDOW);
            round_corners();
        } else {
            SetWindowPos(window, opt.video_layer ? HWND_TOP : HWND_BOTTOM, x, y, last_w, last_h, SWP_NOACTIVATE | SWP_SHOWWINDOW);
            SetWindowRgn(window, nullptr, TRUE);
        }
        hook_child();
    }

    void follow_parent() {
        if (!window || !toplevel()) return;
        bool want = IsWindowVisible(parent_hwnd) && !IsIconic(parent_hwnd);
        if (want && page_hid.load()) return;
        if (want != (IsWindowVisible(window) != 0)) ShowWindow(window, want ? SW_SHOWNOACTIVATE : SW_HIDE);
        if (!want) return;
        POINT pt{last_x, last_y};
        ClientToScreen(parent_hwnd, &pt);
        if (pt.x != popup_x || pt.y != popup_y) {
            popup_x = pt.x; popup_y = pt.y;
            SetWindowPos(window, nullptr, pt.x, pt.y, last_w, last_h, SWP_NOZORDER | SWP_NOACTIVATE);
        }
    }

    // mpv puts its own window inside ours once it has a picture: let clicks fall through it too, and keep it the size of ours.
    void hook_child() {
        if (!window) return;
        HWND child = FindWindowExW(window, nullptr, nullptr, nullptr);
        if (!child) return;
        if (!child_hooked) {
            WNDPROC old = reinterpret_cast<WNDPROC>(SetWindowLongPtrW(child, GWLP_WNDPROC, reinterpret_cast<LONG_PTR>(mpv_child_proc)));
            if (old && old != mpv_child_proc) { g_mpv_proc = old; child_hooked = true; }
        }
        RECT rc{};
        GetClientRect(window, &rc);
        SetWindowPos(child, nullptr, 0, 0, rc.right - rc.left, rc.bottom - rc.top, SWP_NOZORDER | SWP_NOACTIVATE);
    }

    HRESULT make_window(HWND parent, int x, int y, int w, int h_) {
        if (!class_registered) {
            WNDCLASSW wc{};
            wc.lpfnWndProc = video_proc;
            wc.hInstance = GetModuleHandleW(nullptr);
            wc.lpszClassName = kVideoClass;
            wc.hbrBackground = static_cast<HBRUSH>(GetStockObject(BLACK_BRUSH));
            RegisterClassW(&wc);
            class_registered = true;
        }
        parent_hwnd = parent;
        HINSTANCE inst = GetModuleHandleW(nullptr);
        if (under()) {
            POINT pt{x, y};
            ClientToScreen(parent, &pt);
            window = CreateWindowExW(WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW, kVideoClass, L"", WS_POPUP | WS_CLIPCHILDREN, pt.x, pt.y, std::max(w, 1), std::max(h_, 1), nullptr, nullptr, inst, nullptr);   // no owner: the app window is the owned one
        } else if (popup()) {
            POINT pt{x, y};
            ClientToScreen(parent, &pt);
            window = CreateWindowExW(WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW, kVideoClass, L"", WS_POPUP | WS_CLIPCHILDREN, pt.x, pt.y, std::max(w, 1), std::max(h_, 1), parent, nullptr, inst, nullptr);
        } else {
            DWORD style = parent ? (WS_CHILD | WS_VISIBLE | WS_CLIPSIBLINGS | WS_CLIPCHILDREN) : (WS_POPUP | WS_CLIPCHILDREN);
            window = CreateWindowExW(0, kVideoClass, L"", style, x, y, std::max(w, 1), std::max(h_, 1), parent, nullptr, inst, nullptr);
        }
        if (!window) return HRESULT_FROM_WIN32(GetLastError());
        own_window = true;
        child_hooked = false;
        hide_target.store(window);
        place(x, y, w, h_);
        own_the_app();
        return S_OK;
    }

    // ---- the player -------------------------------------------------------------------------------------------------
    void close_player() {
        hide_target.store(nullptr);
        release_the_app();                                          // before anything is hidden or destroyed: the app window must not go with the video window
        if (window) {
            ShowWindow(window, SW_HIDE);
            if (popup()) SetWindowLongPtrW(window, GWLP_HWNDPARENT, 0);
        }
        if (h) { api.destroy(h); h = nullptr; }
        if (window && own_window) DestroyWindow(window);
        window = nullptr;
        own_window = false;
        child_hooked = false;
        loaded = false;
        failed = false;
        std::lock_guard<std::mutex> g(state_mu);
        state = AxState{};
        decoder.clear();
    }

    // Reads what mpv says has happened. Returns false when the file could not be opened.
    void drain_events() {
        while (h) {
            mpv_event *e = api.wait_event(h, 0);
            if (!e || e->event_id == MPV_EVENT_NONE) break;
            if (e->event_id == MPV_EVENT_FILE_LOADED) loaded = true;
            else if (e->event_id == MPV_EVENT_END_FILE) {
                auto *ef = static_cast<mpv_event_end_file *>(e->data);
                if (ef && ef->reason == MPV_END_FILE_REASON_ERROR) {
                    failed = true;
                    fail_with(std::string("playing the video failed: ") + api.error_string(ef->error), ef->error ? ef->error : -1);
                }
            } else if (e->event_id == MPV_EVENT_SHUTDOWN) { failed = true; }
        }
    }

    void tick() {
        if (!h) return;
        follow_parent();
        drain_events();
        double pos = 0, dur = 0;
        bool paused = true, eof = false, cache = false;
        int64_t w = 0, hh = 0, dropped = 0, dec_dropped = 0, frame_no = -1;
        bool have_pos = get_double("time-pos", pos);
        bool have_dur = get_double("duration", dur);
        get_flag("pause", paused);
        get_flag("eof-reached", eof);
        get_flag("paused-for-cache", cache);
        get_int("width", w);
        get_int("height", hh);
        get_int("frame-drop-count", dropped);
        get_int("decoder-frame-drop-count", dec_dropped);
        bool have_frame = get_int("estimated-frame-number", frame_no);
        std::string hw = loaded ? get_string("hwdec-current") : std::string();
        if (loaded && w > 0) hook_child();
        std::lock_guard<std::mutex> g(state_mu);
        if (have_pos) state.position = pos;
        if (have_dur) state.duration = dur;
        state.opened = loaded ? 1 : 0;
        state.ended = eof ? 1 : 0;
        state.playing = (loaded && !paused && !eof && !failed) ? 1 : 0;
        state.buffering = cache ? 1 : 0;
        state.video_width = static_cast<int>(w);
        state.video_height = static_cast<int>(hh);
        state.layer = opt.video_layer;
        state.win_visible = (window && IsWindowVisible(window)) ? 1 : 0;
        state.win_width = last_w;
        state.win_height = last_h;
        state.frames_drawn = have_frame ? static_cast<int>(frame_no) : -1;
        state.frames_dropped = static_cast<int>(dropped + dec_dropped);
        if (loaded) decoder = (hw.empty() || hw == "no") ? L"software" : wide(hw);
    }

    int open(const std::wstring &address, const std::wstring &ua, HWND parent, int x, int y, int w, int h_) {
        close_player();
        { std::lock_guard<std::mutex> g(state_mu); error.clear(); state = AxState{}; }
        std::string why;
        if (!api.load(lib_dir, why)) { fail_with(why, static_cast<int>(HRESULT_FROM_WIN32(ERROR_MOD_NOT_FOUND))); return HRESULT_FROM_WIN32(ERROR_MOD_NOT_FOUND); }
        url = address;
        user_agent = ua;
        if (!opt.headless) {
            if (!parent) { note("no window to put the video in"); return E_POINTER; }
            HRESULT hr = make_window(parent, x, y, w, h_);
            if (FAILED(hr)) { fail_with("creating the video window failed", static_cast<int>(hr)); return hr; }
        }
        h = api.create();
        if (!h) { fail_with("libmpv could not start", -1); return E_FAIL; }
        auto opt_set = [&](const char *name, const std::string &value) { api.set_option(h, name, value.c_str()); };
        // A player inside someone else's window: no controls, no keys, no config files, no scripts, no subtitles of its own (the page draws captions).
        opt_set("config", "no"); opt_set("load-scripts", "no"); opt_set("ytdl", "no"); opt_set("terminal", "no");
        opt_set("input-default-bindings", "no"); opt_set("input-vo-keyboard", "no"); opt_set("input-cursor", "no"); opt_set("cursor-autohide", "no");
        opt_set("osc", "no"); opt_set("osd-level", "0"); opt_set("sub-auto", "no"); opt_set("sid", "no"); opt_set("audio-display", "no");
        opt_set("keep-open", "yes"); opt_set("pause", "yes"); opt_set("hr-seek", "yes"); opt_set("volume-max", "100");
        opt_set("network-timeout", "20");
        opt_set("stream-lavf-o", "reconnect=1,reconnect_streamed=1,reconnect_delay_max=4");
        opt_set("cache", "yes"); opt_set("demuxer-max-bytes", "200MiB"); opt_set("demuxer-max-back-bytes", "60MiB");
        if (!user_agent.empty()) opt_set("user-agent", utf8(user_agent));
        opt_set("hwdec", hwdec_for(opt.hw_accel));
        if (opt.headless) {
            opt_set("vo", "null"); opt_set("ao", "null");
        } else {
            opt_set("wid", std::to_string(static_cast<long long>(reinterpret_cast<intptr_t>(window))));
            opt_set("gpu-api", "d3d11"); opt_set("gpu-context", "d3d11");
        }
        int rc = api.initialize(h);
        if (rc < 0) {
            std::string m = std::string("libmpv could not start: ") + api.error_string(rc);
            close_player();
            fail_with(m, rc);
            return rc;
        }
        set_prop("volume", std::to_string(muted ? 0 : volume));
        std::string target = utf8(url);
        rc = cmd({"loadfile", target.c_str(), "replace"});
        if (rc < 0) {
            std::string m = std::string("opening the video failed: ") + api.error_string(rc);
            close_player();
            fail_with(m, rc);
            return rc;
        }
        // Wait until the file is known (opened, with its length and streams) or it failed, so the caller gets the error from this call.
        DWORD end = GetTickCount() + 30000;
        while (!loaded && !failed && GetTickCount() < end) {
            mpv_event *e = api.wait_event(h, 0.05);
            if (e && e->event_id == MPV_EVENT_FILE_LOADED) loaded = true;
            else if (e && e->event_id == MPV_EVENT_END_FILE) {
                auto *ef = static_cast<mpv_event_end_file *>(e->data);
                if (ef && ef->reason == MPV_END_FILE_REASON_ERROR) { failed = true; fail_with(std::string("opening the video failed: ") + api.error_string(ef->error), ef->error ? ef->error : -1); }
            }
            pump();
        }
        if (failed) return HRESULT_FROM_WIN32(ERROR_OPEN_FAILED);
        if (!loaded) { fail_with("opening the video took too long", static_cast<int>(HRESULT_FROM_WIN32(ERROR_TIMEOUT))); return HRESULT_FROM_WIN32(ERROR_TIMEOUT); }
        if (rate != 1.0) set_prop("speed", std::to_string(rate));
        if (muted) set_prop("mute", "yes");
        tick();
        return 0;
    }

    struct Track { int64_t id; std::wstring name; bool selected; };
    std::vector<Track> audio_tracks() {
        std::vector<Track> out;
        int64_t n = 0;
        if (!get_int("track-list/count", n)) return out;
        for (int64_t i = 0; i < n; ++i) {
            std::string base = "track-list/" + std::to_string(i);
            if (get_string(base + "/type") != "audio") continue;
            int64_t id = 0, channels = 0;
            get_int((base + "/id").c_str(), id);
            get_int((base + "/demux-channel-count").c_str(), channels);
            bool sel = false;
            get_flag((base + "/selected").c_str(), sel);
            std::string title = get_string(base + "/title"), lang = get_string(base + "/lang"), codec = get_string(base + "/codec");
            std::string name = title;
            if (!lang.empty()) name += (name.empty() ? "" : " ") + std::string("[") + lang + "]";
            if (name.empty()) name = "Track " + std::to_string(out.size() + 1);
            if (!codec.empty() || channels > 0) name += " (" + codec + (channels > 0 ? (codec.empty() ? "" : ", ") + std::to_string(channels) + " ch" : std::string()) + ")";
            out.push_back({id, wide(name), sel});
        }
        return out;
    }

    std::wstring diagnostics_text() {
        std::wostringstream o;
        o << L"engine: libmpv";
        if (api.dll) {
            wchar_t path[MAX_PATH]{};
            GetModuleFileNameW(api.dll, path, MAX_PATH);
            o << L" (" << path << L")";
        }
        o << L"\nvideo window: ";
        if (!window) o << L"none";
        else {
            RECT r{};
            GetWindowRect(window, &r);
            o << (under() ? L"own window under the app (the app window is transparent)" : popup() ? L"own window above the app" : (opt.video_layer ? L"child window in front" : L"child window behind")) << L", " << (IsWindowVisible(window) ? L"shown" : L"hidden")
              << L", " << (r.right - r.left) << L"x" << (r.bottom - r.top) << L" at " << r.left << L"," << r.top << L", mpv window " << (FindWindowExW(window, nullptr, nullptr, nullptr) ? L"present" : L"absent");
        }
        o << L"\nhardware decoding requested: " << wide(hwdec_for(opt.hw_accel)) << L", in use: " << (decoder.empty() ? L"-" : decoder);
        if (h && loaded) {
            o << L"\nvideo: " << wide(get_string("video-codec")) << L", " << wide(get_string("video-params/pixelformat")) << L" " << wide(get_string("width")) << L"x" << wide(get_string("height"));
            o << L"\naudio: " << wide(get_string("audio-codec-name")) << L", " << wide(get_string("audio-params/hr-channels")) << L" " << wide(get_string("audio-params/samplerate")) << L" Hz";
            o << L"\ncontainer: " << wide(get_string("file-format")) << L", cache " << wide(get_string("demuxer-cache-duration")) << L" s";
            o << L"\nframes dropped: " << wide(get_string("frame-drop-count")) << L" (VO), " << wide(get_string("decoder-frame-drop-count")) << L" (decoder)";
        }
        { std::lock_guard<std::mutex> g(state_mu); if (!error.empty()) o << L"\nlast error: " << wide(error); }
        return o.str();
    }

    void set_layer(int layer) {
        layer = std::min<int>(AX_LAYER_UNDER, std::max<int>(AX_LAYER_BEHIND, layer));
        bool was_kind = toplevel();
        bool was_under = under();
        opt.video_layer = layer;
        if (window && own_window && (was_kind != toplevel() || was_under != under()) && h) {
            // mpv draws into one window for good: a child window and a popup are different kinds, so the video is opened again in the other kind, at the same second.
            double pos = 0;
            get_double("time-pos", pos);
            bool paused = true;
            get_flag("pause", paused);
            HWND parent = parent_hwnd;
            std::wstring address = url, ua = user_agent;
            int x = last_x, y = last_y, w = last_w, hh = last_h;
            open(address, ua, parent, x, y, w, hh);
            if (pos > 0) cmd({"seek", std::to_string(pos).c_str(), "absolute+exact"});
            if (!paused) set_prop("pause", "no");
        } else {
            place(last_x, last_y, last_w, last_h);
        }
    }
};

// ---------------------------------------------------------------------------------------------------------------------
extern "C" {

AXAPI int ax_create(const wchar_t *lav_dir, const AxOptions *options, AxPlayer **out) {
    if (!lav_dir || !out) return E_POINTER;
    auto p = std::make_unique<AxPlayer>();
    p->lib_dir = lav_dir;
    if (options) p->opt = *options;
    else p->opt.hw_accel = AX_HW_DXVA2_COPYBACK;
    std::wstring dir = p->lib_dir;
    if (!dir.empty() && dir.back() != L'\\' && dir.back() != L'/') dir += L'\\';
    bool found = false;
    for (const wchar_t *f : {L"libmpv-2.dll", L"mpv-2.dll", L"libmpv.dll"})
        if (GetFileAttributesW((dir + f).c_str()) != INVALID_FILE_ATTRIBUTES) found = true;
    if (!found) {
        p->note("missing libmpv-2.dll in " + utf8(p->lib_dir));
        *out = p.release();
        return HRESULT_FROM_WIN32(ERROR_FILE_NOT_FOUND);
    }
    p->start();
    *out = p.release();
    return 0;
}

AXAPI void ax_destroy(AxPlayer *p) {
    if (!p) return;
    {
        std::lock_guard<std::mutex> lk(p->jobs_mu);
        p->quit = true;
    }
    p->jobs_cv.notify_all();
    if (p->thread.joinable()) p->thread.join();
    delete p;
}

AXAPI int ax_open(AxPlayer *p, const wchar_t *url, const wchar_t *user_agent, void *parent, int x, int y, int w, int h) {
    if (!p || !url) return E_POINTER;
    if (!p->thread.joinable()) return HRESULT_FROM_WIN32(ERROR_FILE_NOT_FOUND);
    std::wstring u = url, ua = user_agent ? user_agent : L"";
    HWND hp = static_cast<HWND>(parent);
    return p->call([=] { return p->open(u, ua, hp, x, y, w, h); });
}

AXAPI void ax_hide(AxPlayer *p) {
    if (!p) return;
    p->page_hid.store(true);
    if (HWND w = p->hide_target.load())
        SetWindowPos(w, nullptr, 0, 0, 0, 0, SWP_HIDEWINDOW | SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE | SWP_ASYNCWINDOWPOS);
}

AXAPI void ax_close(AxPlayer *p) {
    if (p && p->thread.joinable()) p->call([=] { p->close_player(); return 0; });
}

AXAPI int ax_play(AxPlayer *p) {
    if (!p || !p->thread.joinable()) return E_POINTER;
    return p->call([=] {
        if (!p->h || !p->loaded) return static_cast<int>(E_FAIL);
        bool eof = false;
        p->get_flag("eof-reached", eof);
        if (eof) p->cmd({"seek", "0", "absolute+exact"});
        return p->set_prop("pause", "no") < 0 ? static_cast<int>(E_FAIL) : 0;
    });
}

AXAPI int ax_pause(AxPlayer *p) {
    if (!p || !p->thread.joinable()) return E_POINTER;
    return p->call([=] { return p->set_prop("pause", "yes") < 0 ? static_cast<int>(E_FAIL) : 0; });
}

AXAPI int ax_stop(AxPlayer *p) {
    if (!p || !p->thread.joinable()) return E_POINTER;
    return p->call([=] {
        p->set_prop("pause", "yes");
        return p->cmd({"seek", "0", "absolute+exact"}) < 0 ? static_cast<int>(E_FAIL) : 0;
    });
}

AXAPI int ax_seek(AxPlayer *p, double seconds) {
    if (!p || !p->thread.joinable()) return E_POINTER;
    return p->call([=] {
        if (!p->h || !p->loaded) return static_cast<int>(E_FAIL);
        std::string s = std::to_string(std::max(0.0, seconds));
        int rc = p->cmd({"seek", s.c_str(), "absolute+exact"});
        if (rc < 0) { p->fail_with(std::string("seeking failed: ") + p->api.error_string(rc), 0); return static_cast<int>(E_FAIL); }
        { std::lock_guard<std::mutex> g(p->state_mu); p->state.position = seconds; p->state.ended = 0; }
        return 0;
    });
}

AXAPI int ax_set_rate(AxPlayer *p, double rate) {
    if (!p || !p->thread.joinable()) return E_POINTER;
    return p->call([=] { p->rate = rate > 0 ? rate : 1.0; if (p->h) p->set_prop("speed", std::to_string(p->rate)); return 0; });
}

AXAPI int ax_set_volume(AxPlayer *p, int percent) {
    if (!p || !p->thread.joinable()) return E_POINTER;
    return p->call([=] { p->volume = std::min(100, std::max(0, percent)); if (p->h) p->set_prop("volume", std::to_string(p->volume)); return 0; });
}

AXAPI int ax_set_muted(AxPlayer *p, int muted) {
    if (!p || !p->thread.joinable()) return E_POINTER;
    return p->call([=] { p->muted = muted != 0; if (p->h) p->set_prop("mute", p->muted ? "yes" : "no"); return 0; });
}

AXAPI int ax_set_rect(AxPlayer *p, int x, int y, int w, int h) {
    if (!p || !p->thread.joinable()) return E_POINTER;
    return p->call([=] { p->place(x, y, w, h); return 0; });
}

AXAPI int ax_set_layer(AxPlayer *p, int layer) {
    if (!p || !p->thread.joinable()) return E_POINTER;
    return p->call([=] { p->set_layer(layer); return 0; });
}

AXAPI int ax_diagnostics(AxPlayer *p, wchar_t *buffer, int chars) {
    if (!p || !buffer || chars <= 0) return 0;
    buffer[0] = L'\0';
    if (!p->thread.joinable()) return 0;
    int written = 0;
    p->call([&] {
        std::wstring text = p->diagnostics_text();
        wcsncpy(buffer, text.c_str(), static_cast<size_t>(chars) - 1);
        buffer[chars - 1] = L'\0';
        written = static_cast<int>(wcslen(buffer));
        return 0;
    });
    return written;
}

AXAPI int ax_audio_track_count(AxPlayer *p) {
    if (!p || !p->thread.joinable()) return 0;
    int count = 0;
    p->call([&] { count = static_cast<int>(p->audio_tracks().size()); return 0; });
    return count;
}

AXAPI int ax_audio_track_name(AxPlayer *p, int index, wchar_t *buffer, int chars) {
    if (!p || !p->thread.joinable() || !buffer || chars <= 0) return E_POINTER;
    int selected = 0;
    int hr = p->call([&] {
        auto tracks = p->audio_tracks();
        if (index < 0 || index >= static_cast<int>(tracks.size())) return static_cast<int>(E_INVALIDARG);
        wcsncpy(buffer, tracks[static_cast<size_t>(index)].name.c_str(), static_cast<size_t>(chars) - 1);
        buffer[chars - 1] = L'\0';
        selected = tracks[static_cast<size_t>(index)].selected ? 1 : 0;
        return 0;
    });
    return hr < 0 ? hr : selected;
}

AXAPI int ax_select_audio_track(AxPlayer *p, int index) {
    if (!p || !p->thread.joinable()) return E_POINTER;
    return p->call([=] {
        auto tracks = p->audio_tracks();
        if (index < 0 || index >= static_cast<int>(tracks.size())) return static_cast<int>(E_INVALIDARG);
        return p->set_prop("aid", std::to_string(tracks[static_cast<size_t>(index)].id)) < 0 ? static_cast<int>(E_FAIL) : 0;
    });
}

AXAPI void ax_get_state(AxPlayer *p, AxState *out) {
    if (!out) return;
    *out = AxState{};
    if (!p) return;
    std::lock_guard<std::mutex> g(p->state_mu);
    *out = p->state;
}

AXAPI int ax_decoder_name(AxPlayer *p, wchar_t *buffer, int chars) {
    if (!p || !buffer || chars <= 0) return 0;
    std::lock_guard<std::mutex> g(p->state_mu);
    wcsncpy(buffer, p->decoder.c_str(), static_cast<size_t>(chars) - 1);
    buffer[chars - 1] = L'\0';
    return static_cast<int>(p->decoder.size());
}

AXAPI const char *ax_last_error(AxPlayer *p) {
    static thread_local std::string copy;
    if (!p) return "";
    std::lock_guard<std::mutex> g(p->state_mu);
    copy = p->error;
    return copy.c_str();
}

}  // extern "C"
