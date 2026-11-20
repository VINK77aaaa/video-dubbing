# -*- coding: utf-8 -*-
"""时间轴规划：倍速贴合 + 顺延，并据此生成字幕。"""


def plan_timeline(blocks, clip_durs, max_tempo=1.45, shift=True):
    """返回每条句块的落点。

    clip_durs: {idx: 去静音后的原始时长}
    策略：优先按字幕原起点摆放；若一句读不完，先温和加速（上限 max_tempo），
    仍超长则顺延后续（不截断、不重叠），遇到空档自动复位。
    """
    rows, cursor, drift = [], 0.0, 0.0
    for b in blocks:
        i = b["idx"]
        d = clip_durs.get(i)
        if not d:
            continue
        slot = b["next"] - b["start"]
        tempo = 1.0
        if slot > 0.2 and d > slot - 0.04:
            tempo = min(max_tempo, d / (slot - 0.04))
        real = d / tempo
        start = max(b["start"], cursor) if shift else b["start"]
        late = start - b["start"]
        drift = late
        rows.append({
            "idx": i,
            "start": start,
            "dur": real,
            "raw_dur": d,
            "slot": slot,
            "tempo": tempo,
            "late": late,
            "text": b["text"],
        })
        cursor = start + real
    return rows


def rows_to_cues(timeline, splitter, wrapper=None, min_dur=0.6):
    """把句块按字数比例切成单屏字幕。"""
    out = []
    for r in timeline:
        parts = splitter(r["text"])
        if not parts:
            continue
        total = sum(len(p) for p in parts) or 1
        t = r["start"]
        for k, p in enumerate(parts):
            last = k == len(parts) - 1
            seg = r["start"] + r["dur"] if last else t + r["dur"] * len(p) / total
            if not last:
                seg = max(seg, t + min_dur)
            text = wrapper(p) if wrapper else p
            out.append((t, seg, text))
            t = seg
    fixed = []
    for k, (a, b, c) in enumerate(out):
        if k + 1 < len(out) and b > out[k + 1][0]:
            b = max(a + 0.3, out[k + 1][0] - 0.01)
        fixed.append((a, b, c))
    return merge_orphans(fixed)


def merge_orphans(rows, min_chars=3):
    """把只有一两个字的碎片条并回上一条，避免闪一下就消失的孤儿字幕。"""
    out = []
    for r in rows:
        if out and len(r[2].replace("\n", "").strip()) < min_chars:
            a, _, prev = out[-1]
            out[-1] = (a, r[1], prev + r[2])
        else:
            out.append(r)
    return out


def stats(timeline):
    if not timeline:
        return {}
    tempos = [r["tempo"] for r in timeline]
    lates = [r["late"] for r in timeline]
    return {
        "blocks": len(timeline),
        "tempo_avg": round(sum(tempos) / len(tempos), 3),
        "tempo_max": round(max(tempos), 3),
        "fit_blocks": sum(1 for t in tempos if t > 1.001),
        "shifted_blocks": sum(1 for x in lates if x > 0.05),
        "shift_max": round(max(lates), 3),
        "final_drift": round(lates[-1], 3),
    }
