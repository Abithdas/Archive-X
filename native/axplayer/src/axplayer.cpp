// axplayer: see include/axplayer.h. One worker thread owns the DirectShow graph; the C functions only hand it commands.
#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include <objbase.h>
#include <initguid.h>
#include <climits>
#include <dshow.h>
#include <control.h>
#include <evcode.h>
#include <dvdmedia.h>   // VIDEOINFOHEADER2
#include <mfidl.h>
#include <evr.h>

#include <algorithm>
#include <chrono>
#include <cmath>
#include <condition_variable>
#include <cstdio>
#include <cstring>
#include <deque>
#include <functional>
#include <future>
#include <atomic>
#include <memory>
#include <mutex>
#include <set>
#include <sstream>
#include <string>
#include <thread>
#include <vector>

#include "axplayer.h"
#include "LAVSplitterSettings.h"   // ILAVFSettings
#include "LAVVideoSettings.h"   // from the LAV Filters source (the build fetches it); interface ILAVVideoSettings / ILAVVideoStatus

// LAV Splitter Source opens a web address itself; its header (IURLSourceFilterLAV.h) is one method, declared here to keep the build small.
struct IAxUrlSource : public IUnknown {
    virtual HRESULT STDMETHODCALLTYPE LoadURL(LPCOLESTR url, LPCOLESTR userAgent, LPCOLESTR referrer) = 0;
};
static const GUID kIidUrlSource = {0xC8FF17F9, 0x5365, 0x4F32, {0x8A, 0xD5, 0x6C, 0x55, 0x03, 0x42, 0xC2, 0xF7}};
// Windows' own filters, by class id (the same numbers as CLSID_NullRenderer and CLSID_EnhancedVideoRenderer in the SDK).
static const GUID kClsidNullRenderer = {0xC1F400A4, 0x3F08, 0x11D3, {0x9F, 0x0B, 0x00, 0x60, 0x08, 0x03, 0x9E, 0x37}};
static const GUID kClsidEvr = {0xFA10746C, 0x9B63, 0x4B6C, {0xBC, 0x49, 0xFC, 0x30, 0x0E, 0xA5, 0xF2, 0x56}};
// Class ids of the three LAV filters (from LAV's own sources).
static const GUID kClsidLavSplitterSource = {0xB98D13E7, 0x55DB, 0x4385, {0xA3, 0x3D, 0x09, 0xFD, 0x1B, 0xA2, 0x63, 0x38}};
static const GUID kClsidLavVideo = {0xEE30215D, 0x164F, 0x4A92, {0xA4, 0xEB, 0x9D, 0x4C, 0x13, 0x39, 0x0F, 0x9F}};
static const GUID kClsidLavAudio = {0xE8E73B6B, 0x4CB3, 0x44A4, {0xBE, 0x99, 0x4F, 0x7B, 0xCB, 0x96, 0xE4, 0x91}};

namespace {

template <class T>
class Com {
public:
    Com() = default;
    explicit Com(T *p) : p_(p) {}
    Com(const Com &) = delete;
    Com &operator=(const Com &) = delete;
    Com(Com &&o) noexcept : p_(o.p_) { o.p_ = nullptr; }
    Com &operator=(Com &&o) noexcept { if (this != &o) { reset(); p_ = o.p_; o.p_ = nullptr; } return *this; }
    ~Com() { reset(); }
    void reset() { if (p_) { p_->Release(); p_ = nullptr; } }
    T **put() { reset(); return &p_; }
    void **put_void() { reset(); return reinterpret_cast<void **>(&p_); }
    T *get() const { return p_; }
    T *operator->() const { return p_; }
    explicit operator bool() const { return p_ != nullptr; }
private:
    T *p_ = nullptr;
};

void free_media_type(AM_MEDIA_TYPE *mt) {
    if (!mt) return;
    if (mt->cbFormat && mt->pbFormat) CoTaskMemFree(mt->pbFormat);
    if (mt->pUnk) mt->pUnk->Release();
    CoTaskMemFree(mt);
}

// DirectShow reports success with S_OK (0) or S_FALSE (1, for example "still starting"); the API promises 0 for every success.
int rc(HRESULT hr) { return SUCCEEDED(hr) ? 0 : static_cast<int>(hr); }

std::string narrow(const std::wstring &w) {
    if (w.empty()) return {};
    int n = WideCharToMultiByte(CP_UTF8, 0, w.c_str(), (int)w.size(), nullptr, 0, nullptr, nullptr);
    std::string s((size_t)n, '\0');
    WideCharToMultiByte(CP_UTF8, 0, w.c_str(), (int)w.size(), &s[0], n, nullptr, nullptr);
    return s;
}

// First pin of a filter in a direction that is not connected yet and (optionally) offers a media type of the given major type.
HRESULT find_pin(IBaseFilter *f, PIN_DIRECTION want, const GUID *major, IPin **out) {
    Com<IEnumPins> en;
    HRESULT hr = f->EnumPins(en.put());
    if (FAILED(hr)) return hr;
    Com<IPin> pin;
    while (en->Next(1, pin.put(), nullptr) == S_OK) {
        PIN_DIRECTION dir;
        pin->QueryDirection(&dir);
        if (dir != want) continue;
        Com<IPin> peer;
        if (pin->ConnectedTo(peer.put()) == S_OK) continue;   // already used
        if (major) {
            Com<IEnumMediaTypes> types;
            if (FAILED(pin->EnumMediaTypes(types.put()))) continue;
            AM_MEDIA_TYPE *mt = nullptr;
            bool match = false;
            while (!match && types->Next(1, &mt, nullptr) == S_OK) {
                match = IsEqualGUID(mt->majortype, *major) != 0;
                free_media_type(mt);
            }
            if (!match) continue;
        }
        *out = pin.get();
        pin.get()->AddRef();
        return S_OK;
    }
    return VFW_E_NOT_FOUND;
}

HRESULT create_from_dll(HMODULE dll, const GUID &clsid, IBaseFilter **out) {
    using GetClassObject = HRESULT(STDAPICALLTYPE *)(REFCLSID, REFIID, LPVOID *);
    auto fn = reinterpret_cast<GetClassObject>(reinterpret_cast<void *>(GetProcAddress(dll, "DllGetClassObject")));
    if (!fn) return HRESULT_FROM_WIN32(ERROR_PROC_NOT_FOUND);
    Com<IClassFactory> cf;
    HRESULT hr = fn(clsid, IID_IClassFactory, cf.put_void());
    if (FAILED(hr)) return hr;
    return cf->CreateInstance(nullptr, IID_IBaseFilter, reinterpret_cast<void **>(out));
}

const wchar_t kVideoClass[] = L"AxPlayerVideo";

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

}  // namespace

struct AxPlayer {
    std::wstring lav_dir;
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
    HMODULE dll_splitter = nullptr, dll_video = nullptr, dll_audio = nullptr;
    Com<IGraphBuilder> graph;
    Com<IMediaControl> control;
    Com<IMediaSeeking> seeking;
    Com<IMediaEvent> events;
    Com<IBasicAudio> audio;
    Com<IBaseFilter> splitter, video_dec, audio_dec, video_out, audio_out;
    Com<IMFVideoDisplayControl> display;
    HWND window = nullptr;
    bool own_window = false;
    HWND parent_hwnd = nullptr;
    std::atomic<bool> page_hid{false};                       // the page asked for the window to be hidden (ax_hide); follow_parent must not bring it back until it is placed again
    std::atomic<HWND> hide_target{nullptr};                  // the video window, readable from any thread (see ax_hide)
    int last_x = 0, last_y = 0, last_w = 1, last_h = 1;   // where the video window was last put (in the parent window)
    int popup_x = INT_MIN, popup_y = INT_MIN;               // where the popup window is on screen
    ULONGLONG next_sample = 0;                              // when the renderer counters and the on-screen check run next
    int frames_drawn = -1, frames_dropped = -1, screen_distinct = 0, screen_luma_range = 0;
    int volume = 100;
    bool muted = false;
    bool class_registered = false;

    // ----------------------------------------------------------------------------------------------------------------
    void set_error(const char *step, HRESULT hr) {
        char buf[200];
        std::snprintf(buf, sizeof buf, "%s failed (0x%08lX)", step, static_cast<unsigned long>(hr));
        std::lock_guard<std::mutex> g(state_mu);
        error = buf;
        if (FAILED(hr)) state.error = static_cast<int>(hr);
    }
    void note(const std::string &s) { std::lock_guard<std::mutex> g(state_mu); error = s; }

    void start() { thread = std::thread([this] { run(); }); }

    void run() {
        CoInitializeEx(nullptr, COINIT_APARTMENTTHREADED);
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
            MSG msg;
            while (PeekMessageW(&msg, nullptr, 0, 0, PM_REMOVE)) { TranslateMessage(&msg); DispatchMessageW(&msg); }
        }
        close_graph();
        if (dll_splitter) FreeLibrary(dll_splitter);
        if (dll_video) FreeLibrary(dll_video);
        if (dll_audio) FreeLibrary(dll_audio);
        CoUninitialize();
    }

    int call(std::function<int()> fn) {
        auto task = std::make_shared<std::packaged_task<int()>>(std::move(fn));
        auto fut = task->get_future();
        {
            std::lock_guard<std::mutex> lk(jobs_mu);
            jobs.emplace_back([task] { (*task)(); });
        }
        jobs_cv.notify_one();
        if (fut.wait_for(std::chrono::seconds(30)) != std::future_status::ready) return HRESULT_FROM_WIN32(ERROR_TIMEOUT);
        return fut.get();
    }

    // ---- on the worker thread ------------------------------------------------------------------------------------
    HRESULT load_filters() {
        if (dll_splitter) return S_OK;
        std::wstring dir = lav_dir;
        if (!dir.empty() && dir.back() != L'\\' && dir.back() != L'/') dir += L'\\';
        auto load = [&](const wchar_t *name, HMODULE &out) {
            out = LoadLibraryExW((dir + name).c_str(), nullptr, LOAD_WITH_ALTERED_SEARCH_PATH);   // its DLLs are looked up next to it
            return out ? S_OK : HRESULT_FROM_WIN32(GetLastError());
        };
        HRESULT hr = load(L"LAVSplitter.ax", dll_splitter);
        if (FAILED(hr)) { set_error("loading LAVSplitter.ax", hr); return hr; }
        hr = load(L"LAVVideo.ax", dll_video);
        if (FAILED(hr)) { set_error("loading LAVVideo.ax", hr); return hr; }
        hr = load(L"LAVAudio.ax", dll_audio);
        if (FAILED(hr)) { set_error("loading LAVAudio.ax", hr); return hr; }
        return S_OK;
    }

    void close_graph() {
        // The window goes first. It is tied to the app's window (as a child, or as an owned window), and the two threads then share an input
        // queue: while this thread waits for a stream to let go in Stop(), the app's own window must not wait on it.
        hide_target.store(nullptr);
        if (window) {
            ShowWindow(window, SW_HIDE);
            if (popup()) SetWindowLongPtrW(window, GWLP_HWNDPARENT, 0);
        }
        if (control) control->Stop();
        display.reset();
        audio.reset(); events.reset(); seeking.reset(); control.reset();
        if (graph) {
            // Take every filter out of the graph before it is released (they live in our loaded DLLs).
            Com<IEnumFilters> en;
            if (SUCCEEDED(graph->EnumFilters(en.put()))) {
                std::vector<Com<IBaseFilter>> all;
                Com<IBaseFilter> f;
                while (en->Next(1, f.put(), nullptr) == S_OK) { f.get()->AddRef(); all.emplace_back(f.get()); }
                for (auto &x : all) graph->RemoveFilter(x.get());
            }
        }
        splitter.reset(); video_dec.reset(); audio_dec.reset(); video_out.reset(); audio_out.reset();
        graph.reset();
        if (window && own_window) DestroyWindow(window);
        window = nullptr;
        own_window = false;
        std::lock_guard<std::mutex> g(state_mu);
        state = AxState{};
        decoder.clear();
    }

    bool popup() const { return opt.video_layer == AX_LAYER_POPUP && parent_hwnd != nullptr; }

    // The page's player frame has rounded corners (22 CSS px); the popup is a plain rectangle above it, so it is clipped to the same shape.
    void round_corners() {
        if (!window) return;
        double scale = 1.0;
        if (auto fn = reinterpret_cast<UINT (WINAPI *)(HWND)>(reinterpret_cast<void *>(GetProcAddress(GetModuleHandleW(L"user32.dll"), "GetDpiForWindow"))))
            if (parent_hwnd) { UINT dpi = fn(parent_hwnd); if (dpi) scale = dpi / 96.0; }
        int radius = static_cast<int>(22.0 * scale);
        // Full screen: the window spans the whole width of the app, so it is a plain rectangle (rounded corners there showed the page behind).
        if (parent_hwnd) { RECT pc{}; if (GetClientRect(parent_hwnd, &pc) && last_w >= (pc.right - pc.left) - 2) radius = 0; }
        radius = std::min(radius, std::min(last_w, last_h) / 2);
        if (radius < 2) { SetWindowRgn(window, nullptr, TRUE); return; }
        // SetWindowRgn takes ownership of the region.
        SetWindowRgn(window, CreateRoundRectRgn(0, 0, last_w + 1, last_h + 1, radius * 2, radius * 2), TRUE);
    }

    // Position the video window. A child window goes to the front or the back of its siblings; the popup is placed over the same area on screen.
    void place(int x, int y, int w, int h) {
        last_x = x; last_y = y; last_w = std::max(w, 1); last_h = std::max(h, 1);
        if (!window) return;
        page_hid.store(false);   // placing the window shows it (SWP_SHOWWINDOW below)
        if (popup()) {
            POINT pt{x, y};
            ClientToScreen(parent_hwnd, &pt);
            popup_x = pt.x; popup_y = pt.y;
            SetWindowPos(window, nullptr, pt.x, pt.y, last_w, last_h, SWP_NOZORDER | SWP_NOACTIVATE | SWP_SHOWWINDOW);
            round_corners();
        } else {
            SetWindowPos(window, opt.video_layer ? HWND_TOP : HWND_BOTTOM, x, y, last_w, last_h, SWP_NOACTIVATE | SWP_SHOWWINDOW);
            SetWindowRgn(window, nullptr, TRUE);
        }
        if (opt.video_layer) RedrawWindow(window, nullptr, nullptr, RDW_INVALIDATE | RDW_UPDATENOW);
    }

    // The popup follows its parent: moved with it, hidden while it is minimised or hidden.
    void follow_parent() {
        if (!window || !popup()) return;
        bool want = IsWindowVisible(parent_hwnd) && !IsIconic(parent_hwnd);
        if (want && page_hid.load()) return;   // hidden by the page (scrolled out of view, a menu open): only place() shows it again
        if (want != (IsWindowVisible(window) != 0)) ShowWindow(window, want ? SW_SHOWNOACTIVATE : SW_HIDE);
        if (!want) return;
        POINT pt{last_x, last_y};
        ClientToScreen(parent_hwnd, &pt);
        if (pt.x != popup_x || pt.y != popup_y) {
            popup_x = pt.x; popup_y = pt.y;
            SetWindowPos(window, nullptr, pt.x, pt.y, last_w, last_h, SWP_NOZORDER | SWP_NOACTIVATE);
        }
    }

    void set_layer(int layer) {
        layer = std::min<int>(AX_LAYER_POPUP, std::max<int>(AX_LAYER_BEHIND, layer));
        bool was_popup = popup();
        opt.video_layer = layer;
        if (window && own_window && was_popup != popup()) {
            // A child window and a popup are different kinds of window: make the other kind and point the renderer at it.
            HWND old = window;
            window = nullptr;
            if (SUCCEEDED(make_window(parent_hwnd, last_x, last_y, last_w, last_h)) && display) display->SetVideoWindow(window);
            DestroyWindow(old);
        } else {
            place(last_x, last_y, last_w, last_h);
        }
        if (display) {
            RECT rc{0, 0, last_w, last_h};
            display->SetVideoPosition(nullptr, &rc);
            display->RepaintVideo();
        }
    }

    HRESULT make_window(HWND parent, int x, int y, int w, int h) {
        if (!class_registered) {
            WNDCLASSW wc{};
            wc.lpfnWndProc = video_proc;
            wc.hInstance = GetModuleHandleW(nullptr);
            wc.lpszClassName = kVideoClass;
            wc.hbrBackground = static_cast<HBRUSH>(GetStockObject(BLACK_BRUSH));
            RegisterClassW(&wc);   // already registered is fine
            class_registered = true;
        }
        parent_hwnd = parent;
        HINSTANCE inst = GetModuleHandleW(nullptr);
        if (popup()) {
            POINT pt{x, y};
            ClientToScreen(parent, &pt);
            // Borderless, never takes focus, not in the taskbar; owned by the app window so it always stays above it.
            window = CreateWindowExW(WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW, kVideoClass, L"", WS_POPUP | WS_CLIPCHILDREN, pt.x, pt.y, std::max(w, 1), std::max(h, 1), parent, nullptr, inst, nullptr);
        } else {
            DWORD style = parent ? (WS_CHILD | WS_VISIBLE | WS_CLIPSIBLINGS | WS_CLIPCHILDREN) : (WS_POPUP | WS_CLIPCHILDREN);
            window = CreateWindowExW(0, kVideoClass, L"", style, x, y, std::max(w, 1), std::max(h, 1), parent, nullptr, inst, nullptr);
        }
        if (!window) return HRESULT_FROM_WIN32(GetLastError());
        own_window = true;
        hide_target.store(window);
        place(x, y, w, h);
        return S_OK;
    }

    HRESULT connect_decoder(IPin *from, IBaseFilter *decoder, IBaseFilter *renderer) {
        Com<IPin> dec_in, dec_out, ren_in;
        HRESULT hr = find_pin(decoder, PINDIR_INPUT, nullptr, dec_in.put());
        if (FAILED(hr)) return hr;
        hr = graph->ConnectDirect(from, dec_in.get(), nullptr);
        if (FAILED(hr)) return hr;
        hr = find_pin(decoder, PINDIR_OUTPUT, nullptr, dec_out.put());
        if (FAILED(hr)) return hr;
        hr = find_pin(renderer, PINDIR_INPUT, nullptr, ren_in.put());
        if (FAILED(hr)) return hr;
        return graph->Connect(dec_out.get(), ren_in.get());   // lets Windows add a colour converter if the renderer needs one
    }

    int open(const std::wstring &url, const std::wstring &user_agent, HWND parent, int x, int y, int w, int h) {
        close_graph();
        { std::lock_guard<std::mutex> g(state_mu); error.clear(); state = AxState{}; }
        HRESULT hr = load_filters();
        if (FAILED(hr)) return hr;

        hr = CoCreateInstance(CLSID_FilterGraph, nullptr, CLSCTX_INPROC_SERVER, IID_IGraphBuilder, graph.put_void());
        if (FAILED(hr)) { set_error("creating the filter graph", hr); return hr; }
        graph->QueryInterface(IID_IMediaControl, control.put_void());
        graph->QueryInterface(IID_IMediaSeeking, seeking.put_void());
        graph->QueryInterface(IID_IMediaEvent, events.put_void());

        hr = create_from_dll(dll_splitter, kClsidLavSplitterSource, splitter.put());
        if (FAILED(hr)) { set_error("creating LAV Splitter", hr); return hr; }
        hr = create_from_dll(dll_video, kClsidLavVideo, video_dec.put());
        if (FAILED(hr)) { set_error("creating LAV Video", hr); return hr; }
        hr = create_from_dll(dll_audio, kClsidLavAudio, audio_dec.put());
        if (FAILED(hr)) { set_error("creating LAV Audio", hr); return hr; }

        // LAV Video: our own settings, nothing read from or written to the registry. Must be set before it is connected.
        Com<ILAVVideoSettings> settings;
        if (SUCCEEDED(video_dec->QueryInterface(IID_ILAVVideoSettings, settings.put_void()))) {
            settings->SetRuntimeConfig(TRUE);
            settings->SetHWAccel(static_cast<LAVHWAccel>(opt.hw_accel));
            for (int c = 0; c < HWCodec_NB; ++c) settings->SetHWAccelCodec(static_cast<LAVVideoHWCodec>(c), TRUE);
        }

        // Renderers: the real ones, or null renderers when there is neither a window nor a sound device (automated tests).
        if (opt.headless) {
            hr = CoCreateInstance(kClsidNullRenderer, nullptr, CLSCTX_INPROC_SERVER, IID_IBaseFilter, video_out.put_void());
            if (SUCCEEDED(hr)) hr = CoCreateInstance(kClsidNullRenderer, nullptr, CLSCTX_INPROC_SERVER, IID_IBaseFilter, audio_out.put_void());
            if (FAILED(hr)) { set_error("creating the null renderers", hr); return hr; }
        } else {
            hr = make_window(parent, x, y, w, h);
            if (FAILED(hr)) { set_error("creating the video window", hr); return hr; }
            hr = CoCreateInstance(kClsidEvr, nullptr, CLSCTX_INPROC_SERVER, IID_IBaseFilter, video_out.put_void());
            if (FAILED(hr)) { set_error("creating the video renderer (EVR)", hr); return hr; }
            hr = CoCreateInstance(CLSID_DSoundRender, nullptr, CLSCTX_INPROC_SERVER, IID_IBaseFilter, audio_out.put_void());
            if (FAILED(hr)) { set_error("creating the audio renderer", hr); return hr; }
        }
        graph->AddFilter(splitter.get(), L"LAV Splitter Source");
        graph->AddFilter(video_dec.get(), L"LAV Video Decoder");
        graph->AddFilter(audio_dec.get(), L"LAV Audio Decoder");
        graph->AddFilter(video_out.get(), L"Video Renderer");
        graph->AddFilter(audio_out.get(), L"Audio Renderer");

        if (!opt.headless) {
            Com<IMFGetService> svc;
            if (SUCCEEDED(video_out->QueryInterface(IID_IMFGetService, svc.put_void()))) {
                hr = svc->GetService(MR_VIDEO_RENDER_SERVICE, IID_IMFVideoDisplayControl, display.put_void());
                if (SUCCEEDED(hr)) {
                    display->SetVideoWindow(window);
                    display->SetAspectRatioMode(MFVideoARMode_PreservePicture);
                    RECT rc{0, 0, std::max(w, 1), std::max(h, 1)};
                    display->SetVideoPosition(nullptr, &rc);
                }
            }
        }

        // LAV Splitter: our own settings too (nothing read from the registry), and a shorter look at the start of a network stream before it
        // is shown (its default is a full second; the container's own header already says which streams there are).
        Com<ILAVFSettings> splitter_settings;
        if (SUCCEEDED(splitter->QueryInterface(IID_ILAVFSettings, splitter_settings.put_void()))) {
            splitter_settings->SetRuntimeConfig(TRUE);
            splitter_settings->SetNetworkStreamAnalysisDuration(400);
        }

        // Open the address (LAV Splitter does the web request itself).
        Com<IAxUrlSource> src;
        if (SUCCEEDED(splitter->QueryInterface(kIidUrlSource, src.put_void()))) {
            hr = src->LoadURL(url.c_str(), user_agent.empty() ? nullptr : user_agent.c_str(), nullptr);
        } else {
            Com<IFileSourceFilter> fsf;
            hr = splitter->QueryInterface(IID_IFileSourceFilter, fsf.put_void());
            if (SUCCEEDED(hr)) hr = fsf->Load(url.c_str(), nullptr);
        }
        if (FAILED(hr)) { set_error("opening the address", hr); return hr; }

        // Wire it up by hand (the filters are not registered, so Windows could not find them by itself):
        // splitter video pin -> LAV Video -> video renderer, splitter audio pin -> LAV Audio -> audio renderer.
        Com<IPin> vpin, apin;
        bool has_video = SUCCEEDED(find_pin(splitter.get(), PINDIR_OUTPUT, &MEDIATYPE_Video, vpin.put()));
        bool has_audio = SUCCEEDED(find_pin(splitter.get(), PINDIR_OUTPUT, &MEDIATYPE_Audio, apin.put()));
        if (!has_video && !has_audio) { set_error("finding a video or audio stream", VFW_E_NOT_FOUND); return VFW_E_NOT_FOUND; }
        if (has_video) {
            hr = connect_decoder(vpin.get(), video_dec.get(), video_out.get());
            if (FAILED(hr)) { set_error("connecting the video decoder", hr); return hr; }
        }
        if (has_audio) {
            hr = connect_decoder(apin.get(), audio_dec.get(), audio_out.get());
            if (FAILED(hr)) { if (!has_video) { set_error("connecting the audio decoder", hr); return hr; } note("audio could not be connected; playing without sound"); }
        }
        graph->QueryInterface(IID_IBasicAudio, audio.put_void());
        apply_volume();

        GUID fmt = TIME_FORMAT_MEDIA_TIME;
        seeking->SetTimeFormat(&fmt);
        publish_static_info(has_video);
        std::lock_guard<std::mutex> g(state_mu);
        state.opened = 1;
        return S_OK;
    }

    void publish_static_info(bool has_video) {
        LONGLONG dur = 0;
        double duration = SUCCEEDED(seeking->GetDuration(&dur)) ? dur / 1e7 : 0.0;
        int vw = 0, vh = 0;
        if (has_video) {
            Com<IPin> out;
            // The decoder's output pin is connected now; its media type tells the picture size.
            Com<IEnumPins> en;
            if (SUCCEEDED(video_dec->EnumPins(en.put()))) {
                Com<IPin> pin;
                while (en->Next(1, pin.put(), nullptr) == S_OK) {
                    PIN_DIRECTION d;
                    pin->QueryDirection(&d);
                    AM_MEDIA_TYPE mt{};
                    if (d == PINDIR_OUTPUT && SUCCEEDED(pin->ConnectionMediaType(&mt))) {
                        if (mt.pbFormat && IsEqualGUID(mt.formattype, FORMAT_VideoInfo) && mt.cbFormat >= sizeof(VIDEOINFOHEADER)) {
                            auto *vi = reinterpret_cast<VIDEOINFOHEADER *>(mt.pbFormat);
                            vw = vi->bmiHeader.biWidth; vh = std::abs(vi->bmiHeader.biHeight);
                        } else if (mt.pbFormat && IsEqualGUID(mt.formattype, FORMAT_VideoInfo2) && mt.cbFormat >= sizeof(VIDEOINFOHEADER2)) {
                            auto *vi = reinterpret_cast<VIDEOINFOHEADER2 *>(mt.pbFormat);
                            vw = vi->bmiHeader.biWidth; vh = std::abs(vi->bmiHeader.biHeight);
                        }
                        if (mt.cbFormat && mt.pbFormat) CoTaskMemFree(mt.pbFormat);
                        if (mt.pUnk) mt.pUnk->Release();
                    }
                }
            }
        }
        std::wstring name;
        Com<ILAVVideoStatus> status;
        if (SUCCEEDED(video_dec->QueryInterface(IID_ILAVVideoStatus, status.put_void()))) {
            if (LPCWSTR n = status->GetActiveDecoderName()) name = n;
        }
        std::lock_guard<std::mutex> g(state_mu);
        state.duration = duration;
        state.video_width = vw;
        state.video_height = vh;
        decoder = name;
    }

    void apply_volume() {
        if (!audio) return;
        long db = -10000;   // hundredths of a decibel; the lowest is silence
        if (!muted && volume > 0) db = std::min(0L, std::max(-10000L, static_cast<long>(2000.0 * std::log10(volume / 100.0))));
        audio->put_Volume(db);
    }

    void tick() {
        if (!graph) return;
        if (events) {
            long code = 0;
            LONG_PTR p1 = 0, p2 = 0;
            while (SUCCEEDED(events->GetEvent(&code, &p1, &p2, 0))) {
                {
                    std::lock_guard<std::mutex> g(state_mu);
                    if (code == EC_COMPLETE) { state.ended = 1; state.playing = 0; }
                    else if (code == EC_ERRORABORT || code == EC_ERRORABORTEX) state.error = static_cast<int>(p1) ? static_cast<int>(p1) : E_FAIL;
                    else if (code == EC_BUFFERING_DATA) state.buffering = p1 ? 1 : 0;
                }
                events->FreeEventParams(code, p1, p2);
            }
        }
        LONGLONG pos = 0;
        double position = (seeking && SUCCEEDED(seeking->GetCurrentPosition(&pos))) ? pos / 1e7 : 0.0;
        int running = 0;
        if (control) {
            OAFilterState fs;
            if (control->GetState(0, &fs) == S_OK && fs == State_Running) running = 1;
        }
        follow_parent();
        if (GetTickCount64() >= next_sample) {
            next_sample = GetTickCount64() + 3000;
            sample_renderer();
        }
        int visible = 0, ww = 0, wh = 0;
        if (window) {
            RECT rc{};
            GetClientRect(window, &rc);
            ww = rc.right - rc.left;
            wh = rc.bottom - rc.top;
            visible = IsWindowVisible(window) ? 1 : 0;
        }
        std::lock_guard<std::mutex> g(state_mu);
        state.position = position;
        if (!state.ended) state.playing = running;
        state.win_visible = visible;
        state.win_width = ww;
        state.win_height = wh;
        state.layer = opt.video_layer;
        state.frames_drawn = frames_drawn;
        state.frames_dropped = frames_dropped;
        state.screen_distinct = screen_distinct;
        state.screen_luma_range = screen_luma_range;
    }

    // Every few seconds: what the renderer says it drew, and what is actually on screen where the video window is.
    void sample_renderer() {
        frames_drawn = frames_dropped = -1;
        if (video_out) {
            Com<IQualProp> q;
            if (SUCCEEDED(video_out->QueryInterface(IID_IQualProp, q.put_void()))) {
                int n = 0;
                if (SUCCEEDED(q->get_FramesDrawn(&n))) frames_drawn = n;
                if (SUCCEEDED(q->get_FramesDroppedInRenderer(&n))) frames_dropped = n;
            }
        }
        screen_distinct = 0;
        screen_luma_range = 0;
        if (!window || !IsWindowVisible(window)) return;
        RECT rc{};
        GetWindowRect(window, &rc);
        int w = rc.right - rc.left, h = rc.bottom - rc.top;
        if (w < 32 || h < 32) return;
        // One shrink-copy of the screen area into a 16 x 9 bitmap. (144 separate GetPixel calls on the composed desktop took long enough to make
        // playback and seeking stutter, because this runs on the thread that also handles every player command.)
        HDC dc = GetDC(nullptr);
        if (!dc) return;
        std::set<unsigned> colours;
        int lo = 255, hi = 0;
        HDC mem = CreateCompatibleDC(dc);
        BITMAPINFO bi{};
        bi.bmiHeader.biSize = sizeof(BITMAPINFOHEADER);
        bi.bmiHeader.biWidth = 16;
        bi.bmiHeader.biHeight = -9;
        bi.bmiHeader.biPlanes = 1;
        bi.bmiHeader.biBitCount = 32;
        bi.bmiHeader.biCompression = BI_RGB;
        void *bits = nullptr;
        HBITMAP bmp = mem ? CreateDIBSection(dc, &bi, DIB_RGB_COLORS, &bits, nullptr, 0) : nullptr;
        if (bmp && bits) {
            HGDIOBJ old = SelectObject(mem, bmp);
            SetStretchBltMode(mem, COLORONCOLOR);
            if (StretchBlt(mem, 0, 0, 16, 9, dc, rc.left, rc.top, w, h, SRCCOPY)) {
                const unsigned char *px = static_cast<const unsigned char *>(bits);
                for (int i = 0; i < 16 * 9; ++i) {
                    int b = px[i * 4], g = px[i * 4 + 1], r = px[i * 4 + 2];
                    int luma = (r * 30 + g * 59 + b * 11) / 100;
                    lo = std::min(lo, luma);
                    hi = std::max(hi, luma);
                    colours.insert(((r >> 4) << 8) | ((g >> 4) << 4) | (b >> 4));
                }
            }
            SelectObject(mem, old);
        }
        if (bmp) DeleteObject(bmp);
        if (mem) DeleteDC(mem);
        ReleaseDC(nullptr, dc);
        screen_distinct = static_cast<int>(colours.size());
        screen_luma_range = hi >= lo ? hi - lo : 0;
    }

    static std::wstring fourcc_or_guid(const GUID &g) {
        wchar_t buf[64];
        if (g.Data2 == 0 && g.Data3 == 0x0010 && g.Data4[0] == 0x80) {   // a subtype made from a four-character code
            char c[5] = {static_cast<char>(g.Data1 & 0xFF), static_cast<char>((g.Data1 >> 8) & 0xFF), static_cast<char>((g.Data1 >> 16) & 0xFF), static_cast<char>((g.Data1 >> 24) & 0xFF), 0};
            bool printable = true;
            for (int i = 0; i < 4; ++i) if (c[i] < 32 || c[i] > 126) printable = false;
            if (printable) { std::swprintf(buf, 64, L"%hs", c); return buf; }
        }
        std::swprintf(buf, 64, L"{%08lX-%04X-%04X}", static_cast<unsigned long>(g.Data1), g.Data2, g.Data3);
        return buf;
    }

    static std::wstring pin_type(IPin *pin) {
        AM_MEDIA_TYPE mt{};
        if (FAILED(pin->ConnectionMediaType(&mt))) return L"(not connected)";
        std::wstringstream o;
        o << fourcc_or_guid(mt.subtype);
        if (mt.pbFormat && IsEqualGUID(mt.formattype, FORMAT_VideoInfo2) && mt.cbFormat >= sizeof(VIDEOINFOHEADER2)) {
            auto *vi = reinterpret_cast<VIDEOINFOHEADER2 *>(mt.pbFormat);
            o << L" " << vi->bmiHeader.biWidth << L"x" << std::abs(vi->bmiHeader.biHeight);
        } else if (mt.pbFormat && IsEqualGUID(mt.formattype, FORMAT_VideoInfo) && mt.cbFormat >= sizeof(VIDEOINFOHEADER)) {
            auto *vi = reinterpret_cast<VIDEOINFOHEADER *>(mt.pbFormat);
            o << L" " << vi->bmiHeader.biWidth << L"x" << std::abs(vi->bmiHeader.biHeight);
        }
        if (mt.cbFormat && mt.pbFormat) CoTaskMemFree(mt.pbFormat);
        if (mt.pUnk) mt.pUnk->Release();
        return o.str();
    }

    static std::wstring window_line(HWND h) {
        wchar_t cls[96] = L"?";
        GetClassNameW(h, cls, 96);
        RECT rc{};
        GetWindowRect(h, &rc);
        LONG style = GetWindowLongW(h, GWL_STYLE);
        std::wstringstream o;
        o << L"0x" << std::hex << reinterpret_cast<uintptr_t>(h) << std::dec << L" " << cls << L" " << (IsWindowVisible(h) ? L"shown" : L"hidden") << L" screen "
          << rc.left << L"," << rc.top << L" " << (rc.right - rc.left) << L"x" << (rc.bottom - rc.top)
          << ((style & WS_CLIPSIBLINGS) ? L" clip-siblings" : L"") << ((GetWindowLongW(h, GWL_EXSTYLE) & WS_EX_LAYERED) ? L" layered" : L"");
        return o.str();
    }

    std::wstring diagnostics_text() {
        sample_renderer();
        std::wstringstream o;
        static const wchar_t *names[] = {L"behind the page (child window, bottom)", L"in front of the page (child window, top)", L"own window above the app (popup)"};
        o << L"layer: " << names[std::min(2, std::max(0, opt.video_layer))] << L"\n";
        o << L"hardware option asked: " << opt.hw_accel << L"   headless: " << opt.headless << L"\n";
        if (parent_hwnd) {
            RECT cr{}; GetClientRect(parent_hwnd, &cr);
            o << L"app window: " << window_line(parent_hwnd) << L"  client " << (cr.right - cr.left) << L"x" << (cr.bottom - cr.top) << (IsIconic(parent_hwnd) ? L"  MINIMISED" : L"") << L"\n";
        } else {
            o << L"app window: none given\n";
        }
        if (window) {
            RECT cr{}; GetClientRect(window, &cr);
            o << L"video window: " << window_line(window) << L"  client " << (cr.right - cr.left) << L"x" << (cr.bottom - cr.top)
              << L"  asked for " << last_x << L"," << last_y << L" " << last_w << L"x" << last_h << L" in the app window\n";
        } else {
            o << L"video window: none\n";
        }
        if (parent_hwnd) {
            o << L"windows inside the app window, front to back:\n";
            int i = 0;
            for (HWND h = GetWindow(parent_hwnd, GW_CHILD); h && i < 12; h = GetWindow(h, GW_HWNDNEXT), ++i)
                o << L"  " << i << L". " << window_line(h) << (h == window ? L"   <== the video" : L"") << L"\n";
        }
        if (display) {
            SIZE sz{}, ar{};
            MFVideoNormalizedRect src{};
            RECT dst{};
            DWORD mode = 0;
            if (SUCCEEDED(display->GetNativeVideoSize(&sz, &ar))) o << L"renderer: native size " << sz.cx << L"x" << sz.cy << L", aspect " << ar.cx << L":" << ar.cy << L"\n";
            if (SUCCEEDED(display->GetVideoPosition(&src, &dst))) o << L"renderer: draws into " << dst.left << L"," << dst.top << L" " << (dst.right - dst.left) << L"x" << (dst.bottom - dst.top) << L" of the video window\n";
            if (SUCCEEDED(display->GetAspectRatioMode(&mode))) o << L"renderer: aspect mode " << mode << L"\n";
        } else {
            o << L"renderer: no display control (" << (opt.headless ? L"headless" : L"EVR did not give one") << L")\n";
        }
        o << L"frames drawn: " << frames_drawn << L"   dropped: " << frames_dropped << L"   (-1 = the renderer does not say)\n";
        o << L"on screen where the video is: " << screen_distinct << L" distinct colours, brightness range " << screen_luma_range
          << (screen_distinct <= 2 && window ? L"   <== looks flat: no picture is visible there" : L"") << L"\n";
        if (graph) {
            o << L"filters in the graph:\n";
            Com<IEnumFilters> en;
            if (SUCCEEDED(graph->EnumFilters(en.put()))) {
                Com<IBaseFilter> f;
                while (en->Next(1, f.put(), nullptr) == S_OK) {
                    FILTER_INFO fi{};
                    if (SUCCEEDED(f->QueryFilterInfo(&fi))) {
                        o << L"  " << fi.achName;
                        if (fi.pGraph) fi.pGraph->Release();
                    }
                    Com<IEnumPins> pins;
                    if (SUCCEEDED(f->EnumPins(pins.put()))) {
                        Com<IPin> pin;
                        while (pins->Next(1, pin.put(), nullptr) == S_OK) {
                            Com<IPin> peer;
                            if (pin->ConnectedTo(peer.put()) == S_OK) {
                                PIN_DIRECTION d;
                                pin->QueryDirection(&d);
                                o << L"  [" << (d == PINDIR_INPUT ? L"in " : L"out ") << pin_type(pin.get()) << L"]";
                            }
                        }
                    }
                    o << L"\n";
                }
            }
        }
        if (video_dec) {
            Com<ILAVVideoStatus> st;
            if (SUCCEEDED(video_dec->QueryInterface(IID_ILAVVideoStatus, st.put_void()))) {
                if (LPCWSTR n = st->GetActiveDecoderName()) o << L"decoder: " << n << L"\n";
                BSTR dev = nullptr;
                if (SUCCEEDED(st->GetHWAccelActiveDevice(&dev)) && dev) { o << L"hardware device: " << dev << L"\n"; SysFreeString(dev); }
            }
        }
        std::lock_guard<std::mutex> g(state_mu);
        o << L"last message: " << std::wstring(error.begin(), error.end()) << L"\n";
        return o.str();
    }

    int audio_streams(std::vector<std::pair<DWORD, std::wstring>> &list, int *selected_index) {
        list.clear();
        if (!splitter) return VFW_E_NOT_FOUND;
        Com<IAMStreamSelect> ss;
        HRESULT hr = splitter->QueryInterface(IID_IAMStreamSelect, ss.put_void());
        if (FAILED(hr)) return hr;
        DWORD n = 0;
        ss->Count(&n);
        int audio_no = 0;
        for (DWORD i = 0; i < n; ++i) {
            AM_MEDIA_TYPE *mt = nullptr;
            DWORD flags = 0, group = 0;
            LCID lcid = 0;
            WCHAR *name = nullptr;
            IUnknown *o1 = nullptr, *o2 = nullptr;
            if (FAILED(ss->Info(i, &mt, &flags, &lcid, &group, &name, &o1, &o2))) continue;
            bool is_audio = mt && IsEqualGUID(mt->majortype, MEDIATYPE_Audio);
            if (is_audio) {
                if (selected_index && (flags & AMSTREAMSELECTINFO_ENABLED)) *selected_index = audio_no;
                list.emplace_back(i, name ? name : L"");
                ++audio_no;
            }
            if (name) CoTaskMemFree(name);
            if (o1) o1->Release();
            if (o2) o2->Release();
            free_media_type(mt);
        }
        return 0;
    }
};

// ---------------------------------------------------------------------------------------------------------------------
extern "C" {

AXAPI int ax_create(const wchar_t *lav_dir, const AxOptions *options, AxPlayer **out) {
    if (!lav_dir || !out) return E_POINTER;
    auto p = std::make_unique<AxPlayer>();
    p->lav_dir = lav_dir;
    if (options) p->opt = *options;
    else p->opt.hw_accel = AX_HW_DXVA2_COPYBACK;
    std::wstring dir = p->lav_dir;
    if (!dir.empty() && dir.back() != L'\\' && dir.back() != L'/') dir += L'\\';
    for (const wchar_t *f : {L"LAVSplitter.ax", L"LAVVideo.ax", L"LAVAudio.ax"}) {
        if (GetFileAttributesW((dir + f).c_str()) == INVALID_FILE_ATTRIBUTES) {
            p->note("missing " + narrow(f) + " in " + narrow(p->lav_dir));
            *out = p.release();   // the caller can still read ax_last_error; every command will fail with the same message
            return HRESULT_FROM_WIN32(ERROR_FILE_NOT_FOUND);
        }
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
    if (HWND w = p->hide_target.load()) {
        // ASYNCWINDOWPOS posts the request to the window's thread instead of waiting for it.
        SetWindowPos(w, nullptr, 0, 0, 0, 0, SWP_HIDEWINDOW | SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE | SWP_ASYNCWINDOWPOS);
    }
}

AXAPI void ax_close(AxPlayer *p) {
    if (p && p->thread.joinable()) p->call([=] { p->close_graph(); return 0; });
}

AXAPI int ax_play(AxPlayer *p) {
    if (!p || !p->thread.joinable()) return E_POINTER;
    return p->call([=] {
        if (!p->control) return static_cast<int>(E_FAIL);
        { std::lock_guard<std::mutex> g(p->state_mu); if (p->state.ended) p->state.ended = 0; }
        HRESULT hr = p->control->Run();
        if (FAILED(hr)) p->set_error("starting playback", hr);
        return rc(hr);
    });
}

AXAPI int ax_pause(AxPlayer *p) {
    if (!p || !p->thread.joinable()) return E_POINTER;
    return p->call([=] { return p->control ? rc(p->control->Pause()) : static_cast<int>(E_FAIL); });
}

AXAPI int ax_stop(AxPlayer *p) {
    if (!p || !p->thread.joinable()) return E_POINTER;
    return p->call([=] { return p->control ? rc(p->control->Stop()) : static_cast<int>(E_FAIL); });
}

AXAPI int ax_seek(AxPlayer *p, double seconds) {
    if (!p || !p->thread.joinable()) return E_POINTER;
    return p->call([=] {
        if (!p->seeking) return static_cast<int>(E_FAIL);
        LONGLONG pos = static_cast<LONGLONG>(std::max(0.0, seconds) * 1e7);
        HRESULT hr = p->seeking->SetPositions(&pos, AM_SEEKING_AbsolutePositioning, nullptr, AM_SEEKING_NoPositioning);
        if (SUCCEEDED(hr)) { std::lock_guard<std::mutex> g(p->state_mu); p->state.ended = 0; p->state.position = seconds; }
        else p->set_error("seeking", hr);
        return rc(hr);
    });
}

AXAPI int ax_set_rate(AxPlayer *p, double rate) {
    if (!p || !p->thread.joinable()) return E_POINTER;
    return p->call([=] { return p->seeking ? rc(p->seeking->SetRate(rate)) : static_cast<int>(E_FAIL); });
}

AXAPI int ax_set_volume(AxPlayer *p, int percent) {
    if (!p || !p->thread.joinable()) return E_POINTER;
    return p->call([=] { p->volume = std::min(100, std::max(0, percent)); p->apply_volume(); return 0; });
}

AXAPI int ax_set_muted(AxPlayer *p, int muted) {
    if (!p || !p->thread.joinable()) return E_POINTER;
    return p->call([=] { p->muted = muted != 0; p->apply_volume(); return 0; });
}

AXAPI int ax_set_rect(AxPlayer *p, int x, int y, int w, int h) {
    if (!p || !p->thread.joinable()) return E_POINTER;
    return p->call([=] {
        p->place(x, y, w, h);
        if (p->display) {
            RECT rc{0, 0, std::max(w, 1), std::max(h, 1)};
            p->display->SetVideoPosition(nullptr, &rc);
            p->display->RepaintVideo();
        }
        return 0;
    });
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
        std::wcsncpy(buffer, text.c_str(), static_cast<size_t>(chars) - 1);
        buffer[chars - 1] = L'\0';
        written = static_cast<int>(std::wcslen(buffer));
        return 0;
    });
    return written;
}

AXAPI int ax_audio_track_count(AxPlayer *p) {
    if (!p || !p->thread.joinable()) return 0;
    int count = 0;
    p->call([&] { std::vector<std::pair<DWORD, std::wstring>> l; if (p->audio_streams(l, nullptr) == 0) count = static_cast<int>(l.size()); return 0; });
    return count;
}

AXAPI int ax_audio_track_name(AxPlayer *p, int index, wchar_t *buffer, int chars) {
    if (!p || !p->thread.joinable() || !buffer || chars <= 0) return E_POINTER;
    int selected_flag = 0;
    int hr = p->call([&] {
        std::vector<std::pair<DWORD, std::wstring>> l;
        int sel = -1;
        int r = p->audio_streams(l, &sel);
        if (r != 0) return r;
        if (index < 0 || index >= static_cast<int>(l.size())) return static_cast<int>(E_INVALIDARG);
        std::wcsncpy(buffer, l[static_cast<size_t>(index)].second.c_str(), static_cast<size_t>(chars) - 1);
        buffer[chars - 1] = L'\0';
        selected_flag = (sel == index) ? 1 : 0;
        return 0;
    });
    return hr < 0 ? hr : selected_flag;
}

AXAPI int ax_select_audio_track(AxPlayer *p, int index) {
    if (!p || !p->thread.joinable()) return E_POINTER;
    return p->call([=] {
        std::vector<std::pair<DWORD, std::wstring>> l;
        int r = p->audio_streams(l, nullptr);
        if (r != 0) return r;
        if (index < 0 || index >= static_cast<int>(l.size())) return static_cast<int>(E_INVALIDARG);
        Com<IAMStreamSelect> ss;
        HRESULT hr = p->splitter->QueryInterface(IID_IAMStreamSelect, ss.put_void());
        if (SUCCEEDED(hr)) hr = ss->Enable(l[static_cast<size_t>(index)].first, AMSTREAMSELECTENABLE_ENABLE);
        if (FAILED(hr)) p->set_error("choosing the audio track", hr);
        return rc(hr);
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
    std::wcsncpy(buffer, p->decoder.c_str(), static_cast<size_t>(chars) - 1);
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
