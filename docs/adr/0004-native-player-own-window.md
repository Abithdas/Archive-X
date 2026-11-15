# 0004. The native video player draws in its own window above the app

**Status:** accepted after a test on a real PC (30.2.2).

**Context.** The native player (LAV Filters and the Enhanced Video Renderer, in `native/axplayer`) needs a window to draw into. A child window placed in front of or behind the web view did not show the picture on a real PC: the web view paints over or hides it. Audio and hardware decoding worked.

**Decision.** The default layer is a borderless window owned by the app window, placed over the page's stage area, following the app when it moves, resizes or minimises. The page keeps its controls above and below it. The other two layers stay selectable for diagnosis.

**Consequences.** The page cannot draw over the picture. Captions, the seek preview and markers must be drawn by the player (an overlay window or the renderer's own text), which is the next step. The video window and the app window share an input queue, so closing the player hides the window first and stops the stream on another thread.
