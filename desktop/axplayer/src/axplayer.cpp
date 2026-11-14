// axplayer: see include/axplayer.h. One worker thread owns the DirectShow graph; the C functions only hand it commands.
#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include <objbase.h>
#include <initguid.h>
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
#include <memory>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

#include "axplayer.h"
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
    int last_x = 0, last_y = 0, last_w = 1, last_h = 1;   // where the video window was last put
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

    // Position the video window; by default at the bottom of its siblings, so the web page (a sibling above it) can show through.
    void place(int x, int y, int w, int h) {
        last_x = x; last_y = y; last_w = std::max(w, 1); last_h = std::max(h, 1);
        if (!window) return;
        SetWindowPos(window, opt.video_on_top ? HWND_TOP : HWND_BOTTOM, x, y, last_w, last_h, SWP_NOACTIVATE | SWP_SHOWWINDOW);
        if (opt.video_on_top) RedrawWindow(window, nullptr, nullptr, RDW_INVALIDATE | RDW_UPDATENOW);
    }

    void set_layer(bool on_top) {
        opt.video_on_top = on_top ? 1 : 0;
        place(last_x, last_y, last_w, last_h);
        if (display) display->RepaintVideo();
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
        DWORD style = parent ? (WS_CHILD | WS_VISIBLE | WS_CLIPSIBLINGS | WS_CLIPCHILDREN) : (WS_POPUP | WS_CLIPCHILDREN);
        window = CreateWindowExW(0, kVideoClass, L"", style, x, y, std::max(w, 1), std::max(h, 1), parent, nullptr, GetModuleHandleW(nullptr), nullptr);
        if (!window) return HRESULT_FROM_WIN32(GetLastError());
        own_window = true;
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
        state.on_top = opt.video_on_top ? 1 : 0;
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

AXAPI int ax_set_layer(AxPlayer *p, int on_top) {
    if (!p || !p->thread.joinable()) return E_POINTER;
    return p->call([=] { p->set_layer(on_top != 0); return 0; });
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
