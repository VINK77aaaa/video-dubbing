# -*- coding: utf-8 -*-
"""轻量自测：python tests/test_subs.py"""
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))

from dubkit import subs  # noqa: E402

FAIL = []


def check(name, cond, detail=""):
    if cond:
        print("  ok   %s" % name)
    else:
        print("  FAIL %s %s" % (name, detail))
        FAIL.append(name)


def test_no_split_latin_phrase():
    text = "也就是饱和度和 Color Boost 之间的差别"
    cues = subs.split_for_display(text)
    joined = "|".join(cues)
    bad = any(c.endswith("Color") for c in cues) or any(c.startswith("Boost") for c in cues)
    check("英文词组不被拆开", not bad, joined)


def test_no_orphan_latin_letter():
    for text in ["我们不是在贬低 Color Boost，我们要深入探究一下到底发生了什么",
                 "100%的Color Boost对吧然后我们看矢量示波器"]:
        cues = subs.split_for_display(text)
        for c in cues:
            check("不以孤立英文尾结束: %s" % c[-8:],
                  not re.search(r"[A-Za-z]$", c) or re.search(r"[A-Za-z]+$", c).group() != c.split()[-1][-1],
                  c)


def test_soft_break():
    text = "在 Resolve 里以及其他饱和度技术我们再看看"
    cues = subs.split_for_display(text)
    ok = all(c[-1] in subs.SOFT_BREAK or cues[i + 1][0] in subs.WORD_START
             for i, c in enumerate(cues[:-1]))
    check("断点落在词边界", ok, "|".join(cues))
    check("不断在「其」后面", not any(c.endswith("其") for c in cues), "|".join(cues))


def test_width_limit():
    for text in ["好吧我真希望这是标题党但这确实发生在我身上我不记得", "a" * 40]:
        cues = subs.split_for_display(text)
        check("单条不超硬上限", all(subs.width_of(c) <= 26.5 for c in cues),
              str([round(subs.width_of(c), 1) for c in cues]))


def test_wrap():
    long_cue = "所以现在我们是百分之百的 Color Boost 对吧然后看看效果"
    wrapped = subs.wrap(long_cue, 10.0)
    same = re.sub(r"\s+", "", wrapped) == re.sub(r"\s+", "", long_cue)
    check("换行不丢字", same and "\n" in wrapped, wrapped)


def test_merge_blocks():
    cues = [{"start": 0, "end": 2, "text": "第一句"},
            {"start": 2.1, "end": 4, "text": "第二句"},
            {"start": 6.0, "end": 8, "text": "间隔太远"}]
    blocks = subs.merge_blocks(cues, max_chars=45, gap=0.5)
    check("长间隔断开", len(blocks) == 2, str(len(blocks)))
    check("短间隔合并并补逗号", blocks[0]["text"] == "第一句，第二句", blocks[0]["text"])


def test_normalize():
    check("重复标点清理", subs.normalize("他就说：，“发生了什么？”") == "他就说：“发生了什么？”",
          subs.normalize("他就说：，“发生了什么？”"))


def test_no_orphan_tail():
    text = "所以我们会先，做一件非常非常简单的事，我就直接到这里"
    cues = subs.split_for_display(text)
    check("不以单字开头成条", not any(len(c) <= 2 and k > 0 for k, c in enumerate(cues)),
          "|".join(cues))
    check("「简单的事」不被劈开", not any(c.endswith("简单的事") and c == text for c in cues) and
          not any(c.startswith("事，") for c in cues), "|".join(cues))


def test_merge_orphans():
    from dubkit import plan
    rows = [(0.0, 2.0, "完整的一句话"), (2.0, 2.3, "哦"), (2.3, 4.0, "下一句")]
    merged = plan.merge_orphans(rows)
    check("孤儿条并入上一条", len(merged) == 2 and merged[0][2] == "完整的一句话哦", str(merged))


def test_cache_key_sensitivity():
    from dubkit import mimo
    a = mimo.cache_key("测试文本", "苏打", "风格A", "mimo-v2.5-tts")
    b = mimo.cache_key("测试文本", "白桦", "风格A", "mimo-v2.5-tts")
    c = mimo.cache_key("测试文本", "苏打", "风格B", "mimo-v2.5-tts")
    check("换音色缓存键变化", a != b)
    check("换风格缓存键变化", a != c)


def test_parse_vtt():
    vtt = "WEBVTT\n\n00:00:01.000 --> 00:00:03.000\n你好世界\n"
    cues = subs.parse_srt(vtt)
    check("解析 VTT", len(cues) == 1 and cues[0]["text"] == "你好世界", str(cues))


if __name__ == "__main__":
    for fn in [test_no_split_latin_phrase, test_soft_break, test_no_orphan_tail,
               test_merge_orphans, test_width_limit, test_wrap, test_merge_blocks,
               test_normalize, test_cache_key_sensitivity, test_parse_vtt,
               test_no_orphan_latin_letter]:
        print(fn.__name__)
        fn()
    print("\n失败 %d 项" % len(FAIL))
    sys.exit(1 if FAIL else 0)
