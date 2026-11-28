/*
 * axplayer: Archive X's native video engine (Windows).
 *
 * It plays a stream address through the same decoding parts MPC-HC uses: LAV Splitter, LAV Video Decoder (with Windows hardware
 * decoding) and LAV Audio Decoder, shown by the Enhanced Video Renderer. The filters are loaded straight from a folder, never registered,
 * so nothing on the PC changes and other players are unaffected.
 *
 * A player owns one worker thread (a COM apartment with a message loop) that creates and drives the DirectShow graph. Every function
 * below may be called from any thread; commands are handed to the worker and wait for it (5 s at most). Reading the state never waits.
 *
 * Return value: 0 on success, otherwise an HRESULT (negative) describing what failed; ax_last_error() says which step.
 */
#ifndef AXPLAYER_H
#define AXPLAYER_H

#include <stddef.h>

#ifdef _WIN32
#  ifdef AXPLAYER_EXPORTS
#    define AXAPI __declspec(dllexport)
#  else
#    define AXAPI __declspec(dllimport)
#  endif
#else
#  define AXAPI
#endif

#ifdef __cplusplus
extern "C" {
#endif

typedef struct AxPlayer AxPlayer;

/* Which hardware decoding LAV Video may use. The numbers are LAV's own (LAVHWAccel). */
enum {
    AX_HW_NONE = 0,         /* software decoding only */
    AX_HW_CUDA = 1,         /* NVIDIA */
    AX_HW_QUICKSYNC = 2,    /* Intel */
    AX_HW_DXVA2_COPYBACK = 3, /* works with every renderer; the default */
    AX_HW_DXVA2_NATIVE = 4, /* needs a renderer that shares Direct3D 9 surfaces (EVR does) */
    AX_HW_D3D11 = 5
};

/* Where the video window sits. */
enum {
    AX_LAYER_BEHIND = 0, /* a child window behind the other child windows (the web page can draw over the video through transparent areas) */
    AX_LAYER_FRONT = 1,  /* a child window in front of them */
    AX_LAYER_POPUP = 2,  /* its own borderless window owned by the parent: always above it, follows it when it moves, resizes or hides */
    AX_LAYER_UNDER = 3   /* its own borderless window UNDER the parent: the parent becomes owned by it (so it stays above), and the page, in a transparent parent, draws over the video as dx.bi does (libmpv engine; the LAV engine uses the popup) */
};

typedef struct AxOptions {
    int hw_accel;   /* one of AX_HW_*; software decoding is used whenever the hardware cannot do the video */
    int headless;   /* 1: no window and no sound device (null renderers), for automated tests */
    int video_layer;  /* where the video window goes (AX_LAYER_*): in front of the page's windows (default), behind them, or as its own window above */
} AxOptions;

typedef struct AxState {
    double position;     /* seconds */
    double duration;     /* seconds; 0 when not known */
    int playing;         /* 1 while running */
    int ended;           /* 1 once the end was reached */
    int buffering;       /* 1 while the source is refilling its buffer */
    int video_width;     /* 0 until known */
    int video_height;
    int error;           /* 0, or the HRESULT of a failure that stopped playback */
    int opened;          /* 1 once a video is loaded */
    int win_visible;     /* 1 when the video window exists and is shown */
    int win_width;       /* the video window's real size in pixels (0 when there is none) */
    int win_height;
    int layer;           /* AX_LAYER_* the video window is in */
    int frames_drawn;    /* frames the renderer says it has drawn (-1 when it does not tell) */
    int frames_dropped;  /* frames it dropped (-1 when unknown) */
    int screen_distinct; /* distinct colours among 144 points of the screen where the video window is: a black or flat area gives 1 or 2 */
    int screen_luma_range; /* brightest minus darkest of those points, 0 to 255 */
} AxState;

/* Creates a player. lav_dir is the folder with LAVSplitter.ax, LAVVideo.ax, LAVAudio.ax (and the DLLs next to them). */
AXAPI int ax_create(const wchar_t *lav_dir, const AxOptions *options, AxPlayer **out);
AXAPI void ax_destroy(AxPlayer *p);

/* Loads a video from an address (http, https or a file path). parent is the HWND to put the video window in as a child (NULL: a
 * hidden window, or none when headless); x, y, w, h place it inside the parent. Playback does not start until ax_play. */
AXAPI int ax_open(AxPlayer *p, const wchar_t *url, const wchar_t *user_agent, void *parent, int x, int y, int w, int h);
AXAPI void ax_close(AxPlayer *p);

AXAPI int ax_play(AxPlayer *p);
AXAPI int ax_pause(AxPlayer *p);
AXAPI int ax_stop(AxPlayer *p);
AXAPI int ax_seek(AxPlayer *p, double seconds);
AXAPI int ax_set_rate(AxPlayer *p, double rate);          /* 1.0 normal */
AXAPI int ax_set_volume(AxPlayer *p, int percent);        /* 0 to 100 */
AXAPI int ax_set_muted(AxPlayer *p, int muted);
AXAPI int ax_set_rect(AxPlayer *p, int x, int y, int w, int h);
/* Hides the video window at once, from any thread, without waiting for the engine's worker (which may be busy stopping a stream). Use it
 * before closing or destroying a player, so the picture never lingers over the page while the graph shuts down. */
AXAPI void ax_hide(AxPlayer *p);
/* Moves the video window to another layer (AX_LAYER_*), while playing. */
AXAPI int ax_set_layer(AxPlayer *p, int layer);
/* A plain-text report for support: the windows involved and their order, the renderer's counters, the graph's media types, the decoder, and what
 * is really on screen where the video should be. Returns the number of characters written. */
AXAPI int ax_diagnostics(AxPlayer *p, wchar_t *buffer, int chars);

/* Audio tracks (LAV Splitter's streams). */
AXAPI int ax_audio_track_count(AxPlayer *p);
AXAPI int ax_audio_track_name(AxPlayer *p, int index, wchar_t *buffer, int chars);   /* returns 1 when it is the selected one, 0 if not, <0 error */
AXAPI int ax_select_audio_track(AxPlayer *p, int index);

AXAPI void ax_get_state(AxPlayer *p, AxState *out);
/* What decodes the video right now (for example "avcodec" or "dxva2cb"); empty before a video is loaded. */
AXAPI int ax_decoder_name(AxPlayer *p, wchar_t *buffer, int chars);
AXAPI const char *ax_last_error(AxPlayer *p);

#ifdef __cplusplus
}
#endif
#endif
