# -*- coding: utf-8 -*-
"""字幕解析、句块合并、按显示宽度切分与换行。"""
import os
import re

CJK = r"\u4e00-\u9fff\u3000-\u303f\uff00-\uffef"
END_PUNCT = "。！？!?…；;，,：:、"
SENT_END = "。！？!?…"

ENCODINGS = ("utf-8-sig", "utf-8", "gb18030", "big5", "cp950", "utf-16", "latin-1")


def read_text(path):
    for enc in ENCODINGS:
        try:
            with open(path, encoding=enc) as f:
                return f.read()
        except (UnicodeDecodeError, UnicodeError):
            continue
    with open(path, "rb") as f:
        return f.read().decode("utf-8", "replace")


def _ts(s):
    s = s.strip().replace(".", ",")
    m = re.match(r"(?:(\d+):)?(\d+):(\d+)[,.](\d+)", s)
    if not m:
        raise ValueError("无法解析时间码：%r" % s)
    h = int(m.group(1) or 0)
    return h * 3600 + int(m.group(2)) * 60 + int(m.group(3)) + int(m.group(4)) / 1000.0


def _clean(t):
    t = re.sub(r"<[^>]+>", "", t)
    t = re.sub(r"\{\\[^}]*\}", "", t)                      # ASS 样式标记
    t = re.sub(r"[\[\(【（]\s*(音乐|掌声|笑|Music|Applause|Laughter)\s*[\]\)】）]", "", t, flags=re.I)
    t = t.replace("♪", "").replace("\u200b", "")
    t = re.sub(r"\s+", " ", t)
    return t.strip()


def parse_srt(text):
    cues = []
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if text.lstrip().upper().startswith("WEBVTT"):
        text = re.sub(r"^.*?WEBVTT[^\n]*\n", "", text, flags=re.S)
    for blk in re.split(r"\n\s*\n", text.strip()):
        lines = [l for l in blk.split("\n") if l.strip()]
        if not lines:
            continue
        i = 1 if lines[0].strip().isdigit() else 0
        if i >= len(lines) or "-->" not in lines[i]:
            continue
        a, b = lines[i].split("-->")[:2]
        t = _clean(" ".join(lines[i + 1:]))
        if not t or not re.search(r"[\w" + CJK + r"]", t):
            continue
        cues.append({"start": _ts(a), "end": _ts(b), "text": t})
    return cues


def parse_ass(text):
    cues = []
    section = None
    fields = None
    for line in text.replace("\r\n", "\n").split("\n"):
        s = line.strip()
        if s.startswith("[") and s.endswith("]"):
            section = s[1:-1].lower()
            continue
        if section != "events":
            continue
        if s.lower().startswith("format:"):
            fields = [x.strip().lower() for x in s.split(":", 1)[1].split(",")]
            continue
        if not s.lower().startswith("dialogue:") or not fields:
            continue
        parts = s.split(":", 1)[1].split(",", len(fields) - 1)
        row = dict(zip(fields, parts))
        t = _clean(re.sub(r"\\N", " ", row.get("text", "")))
        if not t:
            continue
        cues.append({"start": _ts(row["start"]), "end": _ts(row["end"]), "text": t})
    return cues


def parse_subtitle(path):
    text = read_text(path)
    ext = os.path.splitext(path)[1].lower()
    cues = parse_ass(text) if ext in (".ass", ".ssa") else parse_srt(text)
    cues.sort(key=lambda c: c["start"])
    if not cues:
        raise ValueError("字幕文件里没有解析到任何条目：%s" % path)
    return cues


# ---------------- 句块合并 ----------------

def join_text(a, b):
    if not a:
        return b
    return a + b if a[-1] in END_PUNCT else a + "，" + b


def normalize(t):
    t = re.sub(r"([：:，,])\s*[，,]", r"\1", t)
    t = re.sub(r"[，,]\s*([。！？!?…])", r"\1", t)
    t = re.sub(r"[，,]{2,}", "，", t)
    t = re.sub(r"^[，,。\s]+", "", t)
    return t.strip()


def merge_blocks(cues, max_chars=45, gap=0.5):
    """把碎字幕合并成适合连续朗读的句块。"""
    blocks, cur = [], None
    for c in cues:
        if cur is None:
            cur = dict(c)
            continue
        too_long = len(cur["text"]) + len(c["text"]) > max_chars
        if cur["text"][-1] in SENT_END or (c["start"] - cur["end"]) > gap or too_long:
            blocks.append(cur)
            cur = dict(c)
        else:
            cur["text"] = join_text(cur["text"], c["text"])
            cur["end"] = c["end"]
    if cur:
        blocks.append(cur)
    for i, b in enumerate(blocks):
        b["text"] = normalize(b["text"])
        b["idx"] = i
        b["next"] = blocks[i + 1]["start"] if i + 1 < len(blocks) else b["end"] + 1.0
    return blocks


# ---------------- 显示切分（不切断英文词） ----------------

TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9'’\-\.]*|\s+|.", re.S)
LATIN_RE = re.compile(r"[A-Za-z0-9]")
SOFT_BREAK = "的了是吧呢吗啊嘛呀就都也还又和与或但而且并以及"
WORD_START = "其这那我你他她它再又而但所为因把被让给对从到和与或及就还也都是在把"


def tokenize(t):
    return TOKEN_RE.findall(t)


def group_tokens(tokens):
    """把连续的英文词（含中间空格）合并成不可分割的单元。

    否则 "Color Boost" 会被空格拆开，跨两条字幕显示。
    保留英文后的空格，使 "Color Boost 是" 而不是 "Color Boost是"。
    """
    out, buf = [], ""
    for tk in tokens:
        if LATIN_RE.match(tk) or (tk.isspace() and buf):
            buf += tk
        else:
            if buf:
                out.append(buf)
                buf = ""
            out.append(tk)
    if buf:
        out.append(buf)
    return [x for x in out if x]


def width_of(t):
    """显示宽度：中日韩全角算 1，ASCII 算 0.55。"""
    w = 0.0
    for ch in t:
        w += 1.0 if re.match("[" + CJK + "]", ch) else 0.55
    return w


def _soft_cut(buf):
    """无标点可断时，回退到虚词之后或实词之前，避免断在词中间。"""
    for k in range(len(buf) - 1, max(len(buf) - 9, -1), -1):
        if buf[k] in SOFT_BREAK:
            return k + 1
        if buf[k] in WORD_START:
            return k
    return -1


def _refine_cut(buf, sc, target, hard_max=26.0):
    """断点后若只剩一两个字（如「…简单的 / 事，我就…」），
    优先回退到上一个断点；退无可退就把这点尾巴一起吞进本条。"""
    while sc > 0:
        if width_of(buf[sc:]) > 2.0 or width_of(buf[:sc]) < target * 0.40:
            break
        nxt = _soft_cut(buf[:sc])
        if 0 < nxt < sc:
            sc = nxt
            continue
        if width_of(buf) <= hard_max:
            return len(buf)
        break
    return sc


def split_for_display(text, target=18.0, hard_max=26.0):
    """切成若干条单屏字幕。英文词（含词组）作为整体，不会被劈开。"""
    units = group_tokens(tokenize(text))
    cues, buf, w = [], "", 0.0
    for tk in units:
        tw = width_of(tk)
        if buf and w + tw > target:
            cut = max((buf.rfind(p) for p in "，。！？；、,"), default=-1)
            if cut >= 0 and width_of(buf[:cut + 1]) >= target * 0.5:
                cues.append(buf[:cut + 1])
                buf = buf[cut + 1:]
            else:
                sc = _soft_cut(buf)
                if sc > 0 and width_of(buf[:sc]) >= target * 0.45:
                    sc = _refine_cut(buf, sc, target)
                    cues.append(buf[:sc])
                    buf = buf[sc:]
                else:
                    cues.append(buf)
                    buf = ""
            w = width_of(buf)
        if w + tw > hard_max and buf:
            cues.append(buf)
            buf, w = "", 0.0
        buf += tk
        w += tw
    if buf.strip():
        cues.append(buf)
    out = []
    for c in cues:
        c = c.strip()
        c = c.strip("，、,；;：:")
        c = re.sub(r"^\s+|\s+$", "", c)
        if c:
            out.append(c)
    return out


def wrap(cue, max_width=17.0):
    """超过一行宽度时按 token 边界折成两行。"""
    if width_of(cue) <= max_width:
        return cue
    units = group_tokens(tokenize(cue))
    total = width_of(cue)
    half = total / 2
    best, acc = None, 0.0
    for i, tk in enumerate(units):
        acc += width_of(tk)
        if acc >= half:
            best = i + 1
            break
    if not best or best >= len(units):
        return cue
    a = "".join(units[:best]).strip()
    b = "".join(units[best:]).strip()
    return a + "\n" + b if a and b else cue


def fmt_ts(t):
    t = max(0.0, t)
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return "%02d:%02d:%02d,%03d" % (h, m, s, ms)


def write_srt(rows, path):
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        for n, (a, b, text) in enumerate(rows, 1):
            f.write("%d\n%s --> %s\n%s\n\n" % (n, fmt_ts(a), fmt_ts(b), text))
    return path
