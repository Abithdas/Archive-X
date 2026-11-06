"""Inventory the legacy Archive X UI for parity checklists.
usage: python -I extract.py <web/ui dir> <out dir>"""
import html, json, re, sys
from html.parser import HTMLParser
from pathlib import Path

UI = Path(sys.argv[1]); OUT = Path(sys.argv[2]); OUT.mkdir(exist_ok=True)
HANDLER_FN = re.compile(r"(?<![\w.$])([A-Za-z_$][\w$]*)\s*\(")
SKIP = {"if", "for", "while", "return", "function", "event", "this", "String", "Number", "Boolean", "JSON", "Math",
        "Array", "Object", "setTimeout", "clearTimeout", "encodeURIComponent", "parseInt", "parseFloat", "await",
        "async", "new", "Date", "Promise", "jsa", "esc", "Set", "Map", "typeof", "void", "querySelector",
        "getElementById", "stopPropagation", "preventDefault", "closest", "contains", "toggle", "add", "remove",
        "requestAnimationFrame", "focus", "click", "select", "blur", "replace", "trim", "includes", "test", "max", "min"}

def handler_fns(code):
    return sorted({m for m in HANDLER_FN.findall(code or "") if m not in SKIP and not m.startswith("_ax") and len(m) > 2})

class Inv(HTMLParser):
    INTER = {"button", "input", "select", "textarea", "a", "summary", "label", "details", "dialog"}
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.items = []; self.stack = []; self.headings = []; self.cur_head = None
    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        handlers = {k: v for k, v in a.items() if k.startswith("on")}
        rec = None
        if tag in ("h1", "h2", "h3", "h4", "legend") or (tag in ("div", "span", "b") and "pnt" in (a.get("class") or "").split()):
            self.cur_head = {"tag": tag, "id": a.get("id"), "text": ""}; self.headings.append(self.cur_head)
        if tag in self.INTER or handlers or a.get("role") in ("button", "tab", "switch", "checkbox", "menuitem"):
            if tag == "label" and not handlers: pass
            else:
                rec = {"tag": tag, "id": a.get("id"), "type": a.get("type"), "role": a.get("role"),
                       "label": a.get("aria-label") or a.get("title") or a.get("placeholder") or "",
                       "text": "", "handlers": {k: handler_fns(v) for k, v in handlers.items()},
                       "classes": (a.get("class") or "")[:80], "section": self.headings[-1]["text"] if self.headings else ""}
                self.items.append(rec)
        self.stack.append((tag, rec))
    def handle_endtag(self, tag):
        while self.stack:
            t, rec = self.stack.pop()
            if t == tag: break
        if tag in ("h1", "h2", "h3", "h4", "legend"): self.cur_head = None
    def handle_data(self, data):
        txt = " ".join(data.split())
        if not txt: return
        if self.cur_head is not None and len(self.cur_head["text"]) < 90: self.cur_head["text"] = (self.cur_head["text"] + " " + txt).strip()
        for t, rec in reversed(self.stack):
            if rec is not None:
                if len(rec["text"]) < 70: rec["text"] = (rec["text"] + " " + txt).strip()
                break

def scan_html(path):
    raw = path.read_text(encoding="utf-8-sig")
    p = Inv(); p.feed(raw)
    dialogs = re.findall(r'<(?:dialog|div|section)[^>]*\bid="([^"]+)"[^>]*class="[^"]*\b(?:mdl|ax-scene-match-dialog|ax2-sheet|ax2-overlay-root|ax-ux-sheet)[^"]*"', raw)
    dialogs += re.findall(r'<dialog[^>]*\bid="([^"]+)"', raw)
    return {"file": str(path.relative_to(UI)), "items": p.items, "headings": [h for h in p.headings if h["text"]], "dialogs": sorted(set(dialogs))}

# ---- JS function index ----------------------------------------------------------
FUNC = re.compile(r"^(?:async\s+)?function\s+([A-Za-z_$][\w$]*)\s*\(|^(?:const|let)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?\(?[\w,\s]*\)?\s*=>", re.M)
API = re.compile(r"""(?:api|fetch|apiJson|_api)\(\s*[`'"](/api/[^`'"?$]*)""")
WS = re.compile(r"new WebSocket\(")
TPL_HANDLER = re.compile(r'on(click|change|input|keydown|submit|pointerdown)=\\?"([^"\\]{1,300})')
def scan_js(path):
    src = path.read_text(encoding="utf-8", errors="ignore")
    starts = [(m.start(), m.group(1) or m.group(2)) for m in FUNC.finditer(src)]
    funcs = []
    for i, (pos, name) in enumerate(starts):
        end = starts[i + 1][0] if i + 1 < len(starts) else len(src)
        body = src[pos:end]
        funcs.append({"name": name, "file": str(path.relative_to(UI)), "line": src.count("\n", 0, pos) + 1,
                      "lines": body.count("\n") + 1,
                      "api": sorted(set(API.findall(body))),
                      "ws": bool(WS.search(body)),
                      "ui_handlers": sorted({f for _, code in TPL_HANDLER.findall(body) for f in handler_fns(code)}),
                      "dialog_ids": sorted(set(re.findall(r"\$\('((?:[a-z0-9]+-)*(?:modal|dialog|sheet|drawer|mdl)[a-z0-9-]*)'\)", body)))})
    keys = sorted(set(re.findall(r"""(?:e|ev|event)\.key\s*===?\s*['"]([^'"]+)['"]""", src)))
    return funcs, keys

html_files = sorted(UI.glob("html/*.html")) + sorted(UI.glob("html/views/*.html"))
html_inv = [scan_html(p) for p in html_files if "icon-sprite" not in p.name]
js_files = sorted(UI.glob("js/*.js")) + sorted(UI.glob("js/*/*.js"))
functions, keys = [], {}
for p in js_files:
    f, k = scan_js(p); functions += f
    if k: keys[str(p.relative_to(UI))] = k
json.dump({"html": html_inv, "functions": functions, "keys": keys}, open(OUT / "inventory.json", "w"), indent=1)
print("html files", len(html_inv), "interactive", sum(len(h["items"]) for h in html_inv),
      "dialogs", sum(len(h["dialogs"]) for h in html_inv), "functions", len(functions),
      "with api", sum(1 for f in functions if f["api"]), "distinct endpoints", len({a for f in functions for a in f["api"]}))
