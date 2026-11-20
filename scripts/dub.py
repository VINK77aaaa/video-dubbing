#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""dub.py —— 用小米 MiMo TTS 给视频做中文配音（含字幕）。

用法：
  python dub.py doctor                       # 环境自检
  python dub.py plan  --video a.mkv --subs a.zh.srt --dry-run
  python dub.py run   --video a.mkv --subs a.zh.srt --out-dir ./out
"""
import argparse
import json
import os
import shutil
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dubkit import media, mimo, plan as planner, subs as subkit  # noqa: E402

SR = 48000
CH = 2


def log(msg=""):
    print(msg, flush=True)


def human_time(sec):
    sec = int(sec)
    return "%d分%02d秒" % (sec // 60, sec % 60) if sec >= 60 else "%d秒" % sec


# ---------------------------------------------------------------- doctor
def cmd_doctor(args):
    ok = True
    log("== 依赖检查 ==")
    for tool in ("ffmpeg", "ffprobe"):
        path = shutil.which(tool)
        log("  %-8s %s" % (tool, path or "缺失"))
        ok &= bool(path)
    if not ok:
        log("\n请先安装 ffmpeg 后重试。")
        return 1

    log("\n== 硬件编码器 ==")
    enc = media.pick_h264_encoder()
    log("  选中 %s" % " ".join(enc))

    log("\n== API ==")
    try:
        key = mimo.resolve_api_key(args.api_key)
        log("  Key      %s…%s" % (key[:6], key[-4:]))
    except mimo.TTSError as e:
        log("  " + str(e))
        return 1
    try:
        t0 = time.time()
        tts = mimo.MimoTTS(api_key=key, base_url=args.base_url, rpm=600)
        data = tts.synthesize("测试")
        log("  连通     正常，返回 %d 字节音频，耗时 %.1fs" % (len(data), time.time() - t0))
    except Exception as e:
        log("  连通     失败：%s" % e)
        return 1
    log("\n环境正常，可以开跑。")
    return 0


# ---------------------------------------------------------------- plan
def load_blocks(args):
    cues = subkit.parse_subtitle(args.subs)
    blocks = subkit.merge_blocks(cues, max_chars=args.max_chars, gap=args.gap)
    return cues, blocks


def cmd_plan(args):
    cues, blocks = load_blocks(args)
    chars = sum(len(b["text"]) for b in blocks)
    span = blocks[-1]["end"] - blocks[0]["start"]
    log("原始字幕 %d 条 → 句块 %d 个" % (len(cues), len(blocks)))
    log("总字数 %d，覆盖 %.1f 秒，%.2f 字/秒" % (chars, span, chars / span))
    est_calls = len(blocks)
    est_min = est_calls / args.rpm
    log("预计 API 调用 %d 次，限速 %d RPM → 约 %s" % (est_calls, args.rpm, human_time(est_min * 60)))
    if args.dry_run:
        log("\n前 5 个句块：")
        for b in blocks[:5]:
            log("  [%03d] %6.2f-%6.2f  %s" % (b["idx"], b["start"], b["end"], b["text"]))
        log("\n--dry-run：未调用 API。")
    return 0


# ---------------------------------------------------------------- synth
def synth_all(tts, blocks, cache_dir, workers, force=False):
    os.makedirs(cache_dir, exist_ok=True)
    results, failed = {}, []

    def one(b):
        path = os.path.join(cache_dir, tts.key_for(b["text"]) + ".wav")
        if not force and os.path.exists(path) and os.path.getsize(path) > 2000:
            return b["idx"], path, "cached"
        data = tts.synthesize(b["text"])
        tmp = path + ".tmp"
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
        return b["idx"], path, "ok"

    total = len(blocks)
    done = 0
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(one, b): b for b in blocks}
        for f in as_completed(futs):
            b = futs[f]
            try:
                idx, path, state = f.result()
                results[idx] = path
                done += 1
            except Exception as e:
                failed.append({"idx": b["idx"], "text": b["text"][:60], "error": str(e)[:300]})
            if (done + len(failed)) % 20 == 0 or (done + len(failed)) == total:
                log("  合成 %d/%d  失败 %d  已用 %s"
                    % (done + len(failed), total, len(failed),
                       human_time(time.time() - t0)))
    return results, failed


# ---------------------------------------------------------------- build
def measure_and_plan(blocks, raw_paths, work_trim, args, tts=None, cache_dir=None):
    """修剪静音 → 量长度 → 规划时间轴（可选：超长块用更快语速重合成一次）。"""
    os.makedirs(work_trim, exist_ok=True)
    durs, trims = {}, {}
    for b in blocks:
        i = b["idx"]
        src = raw_paths.get(i)
        if not src:
            continue
        dst = os.path.join(work_trim, "%04d.wav" % i)
        media.trim_clip(src, dst, SR, max_pause=args.max_pause)
        durs[i] = media.duration_of(dst)
        trims[i] = dst

    timeline = planner.plan_timeline(blocks, durs, max_tempo=args.max_tempo)

    if tts and args.fast_retry:
        over = [r for r in timeline if r["tempo"] >= args.max_tempo * 0.999 and r["late"] > 0.15]
        if over:
            log("  %d 个句块原速读不完，尝试用更快语速重合成…" % len(over))
            faster = args.style + "语速再快一点，一口气说完，尽量不停顿。"
            improved = 0
            for r in over:
                b = blocks[r["idx"]]
                try:
                    data = tts.synthesize(b["text"], style=faster)
                except Exception:
                    continue
                key = mimo.cache_key(b["text"], tts.voice, faster, tts.model)
                raw = os.path.join(cache_dir or os.path.join(work_trim, "..", "..", "cache"),
                                   key + ".wav")
                raw = os.path.normpath(raw)
                with open(raw, "wb") as f:
                    f.write(data)
                tmp_trim = os.path.join(work_trim, "%04d_f.wav" % r["idx"])
                media.trim_clip(raw, tmp_trim, SR, max_pause=args.max_pause)
                d2 = media.duration_of(tmp_trim)
                if d2 < durs[r["idx"]] * 0.97:
                    durs[r["idx"]] = d2
                    trims[r["idx"]] = tmp_trim
                    improved += 1
            log("  其中 %d 个成功缩短" % improved)
            timeline = planner.plan_timeline(blocks, durs, max_tempo=args.max_tempo)

    return timeline, trims


def build_track(timeline, trims, total_dur, work_pcm, args):
    """顺序拼接成整轨。复用同一个临时 PCM，避免生成大量中间文件。"""
    os.makedirs(work_pcm, exist_ok=True)
    track = os.path.join(work_pcm, "track.pcm")
    scratch = os.path.join(work_pcm, "_clip.pcm")
    frame = SR * CH * 2
    with open(track, "wb") as out:
        for r in timeline:
            media.render_clip(trims[r["idx"]], scratch, r["tempo"], SR, CH)
            with open(scratch, "rb") as f:
                data = f.read()
            real = len(data) / frame
            r["rendered"] = real
            gap = r["start"] - out.tell() / frame
            if gap > 0:
                out.write(media.silence_bytes(gap, SR, CH))
            out.write(data)
        tail = total_dur - out.tell() / frame
        if tail > 0:
            out.write(media.silence_bytes(tail + 0.5, SR, CH))
    if os.path.exists(scratch):
        os.remove(scratch)
    return track


# ---------------------------------------------------------------- run
def cmd_run(args):
    t_start = time.time()
    media.require_tools()
    out_dir = os.path.abspath(args.out_dir)
    work = os.path.join(out_dir, "work")
    cache = os.path.join(out_dir, "cache")
    for d in (out_dir, work, cache):
        os.makedirs(d, exist_ok=True)

    info = media.probe(args.video)
    total_dur = float(info["format"]["duration"])
    log("视频 %.1f 秒（%s）" % (total_dur, os.path.basename(args.video)))

    cues, blocks = load_blocks(args)
    if args.limit:
        blocks = blocks[:args.limit]
        for i, b in enumerate(blocks):
            b["idx"] = i
            b["next"] = blocks[i + 1]["start"] if i + 1 < len(blocks) else b["end"] + 1.0
    chars = sum(len(b["text"]) for b in blocks)
    log("字幕 %d 条 → 句块 %d 个，%d 字" % (len(cues), len(blocks), chars))

    if args.dry_run:
        log("--dry-run：预计 %d 次 API 调用，约 %s。未实际执行。"
            % (len(blocks), human_time(len(blocks) / args.rpm * 60)))
        return 0

    tts = mimo.MimoTTS(api_key=args.api_key, base_url=args.base_url, model=args.model,
                       voice=args.voice, style=args.style, rpm=args.rpm)

    log("\n[1/5] 合成语音（音色 %s）" % args.voice)
    raw, failed = synth_all(tts, blocks, cache, args.workers)
    if failed:
        log("  ! %d 个句块合成失败，成片会有空档：" % len(failed))
        for f in failed[:5]:
            log("    [%03d] %s ← %s" % (f["idx"], f["text"], f["error"][:80]))
        if not args.allow_gaps:
            raise media.DubError("存在合成失败的句块。加 --allow-gaps 可强行继续。")
    if not raw:
        raise media.DubError("没有任何音频合成成功。")

    log("\n[2/5] 贴合时间轴")
    timeline, trims = measure_and_plan(blocks, raw, os.path.join(work, "trim"), args, tts, cache)
    st = planner.stats(timeline)
    log("  %d 块，平均加速 %.3fx（最大 %.2fx），顺延 %d 块（最大 %.2fs，末块 %.2fs）"
        % (st["blocks"], st["tempo_avg"], st["tempo_max"],
           st["shifted_blocks"], st["shift_max"], st["final_drift"]))

    log("\n[3/5] 拼接配音轨")
    track = build_track(timeline, trims, total_dur, os.path.join(work, "pcm"), args)
    dub_wav = os.path.join(work, "dub.wav")
    two_pass = media.loudnorm(track, dub_wav, SR, CH, target_i=args.lufs)
    log("  响度标准化 %s（%s）" % ("两遍" if two_pass else "单遍", dub_wav))

    if args.bed > -99:
        bed = os.path.join(work, "dub_bed.wav")
        media.mix_bed(dub_wav, args.video, bed, bed_gain_db=args.bed, duration=total_dur)
        dub_wav = bed
        log("  已混入原声背景（%.0f dB）" % args.bed)

    log("\n[4/5] 生成字幕")
    srt_path = os.path.join(out_dir, "配音字幕.srt")
    rows = planner.rows_to_cues(
        timeline,
        lambda t: subkit.split_for_display(t, args.cue_width, args.cue_hard_max),
        lambda t: subkit.wrap(t, args.line_width))
    subkit.write_srt(rows, srt_path)
    log("  %d 条 → %s" % (len(rows), srt_path))

    log("\n[5/5] 封装")
    base = os.path.splitext(os.path.basename(args.video))[0]
    outputs = []
    targets = ["mkv", "mp4"] if args.container == "both" else [args.container]
    for c in targets:
        out = os.path.join(out_dir, "%s.zh-dub.%s" % (base, c))
        if c == "mp4" and args.subs_mode == "burn":
            tmp = os.path.join(work, "tmp_sub.mp4")
            media.mux(args.video, dub_wav, tmp, container="mp4", audio_bitrate=args.audio_bitrate)
            media.burn_subtitles(tmp, srt_path, out, font=args.font, font_size=args.font_size)
        else:
            media.mux(args.video, dub_wav, out, container=c, audio_bitrate=args.audio_bitrate,
                      subtitle=srt_path if args.subs_mode != "none" else None)
        outputs.append(out)
        log("  %s  %.1f MB" % (out, os.path.getsize(out) / 1048576))

    worst = sorted(timeline, key=lambda r: -r["late"])[:5]
    report = {
        "video": args.video,
        "duration_sec": round(total_dur, 3),
        "voice": args.voice,
        "model": args.model,
        "blocks": len(blocks),
        "cues": len(rows),
        "chars": chars,
        "timeline": st,
        "worst_shifts": [{"idx": r["idx"], "late": round(r["late"], 2),
                          "tempo": round(r["tempo"], 2),
                          "text": r["text"][:40]} for r in worst if r["late"] > 0.05],
        "failed_blocks": failed,
        "outputs": outputs,
        "elapsed_sec": round(time.time() - t_start, 1),
    }
    with open(os.path.join(out_dir, "report.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    if not args.keep_temp:
        # 清理失败不应影响成片交付
        for path in (os.path.join(work, "pcm"), os.path.join(work, "trim")):
            try:
                shutil.rmtree(path, ignore_errors=True)
            except Exception:
                pass
        try:
            if os.path.exists(track):
                os.remove(track)
        except Exception:
            pass

    log("\n完成，用时 %s。报告：%s" % (human_time(time.time() - t_start),
                                     os.path.join(out_dir, "report.json")))
    return 0


# ---------------------------------------------------------------- CLI
def build_parser():
    p = argparse.ArgumentParser(prog="dub.py", description="MiMo TTS 视频配音工具")
    sub = p.add_subparsers(dest="cmd", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--api-key", default=None, help="默认读环境变量 MIMO_API_KEY")
    common.add_argument("--base-url", default=mimo.DEFAULT_BASE)
    common.add_argument("--model", default=mimo.DEFAULT_MODEL)

    d = sub.add_parser("doctor", parents=[common], help="环境与接口自检")
    d.set_defaults(func=cmd_doctor)

    for name, fn in (("plan", cmd_plan), ("run", cmd_run)):
        sp = sub.add_parser(name, parents=[common])
        sp.add_argument("--video", required=True)
        sp.add_argument("--subs", required=True, help=".srt / .vtt / .ass")
        sp.add_argument("--out-dir", default="./dub-out")
        sp.add_argument("--voice", default="苏打", choices=list(mimo.VOICES))
        sp.add_argument("--style", default=mimo.DEFAULT_STYLE)
        sp.add_argument("--max-chars", type=int, default=45, help="单句块字数上限")
        sp.add_argument("--gap", type=float, default=0.5, help="合并句块的静音间隔阈值")
        sp.add_argument("--rpm", type=int, default=85, help="请求限速（官方上限 100）")
        sp.add_argument("--dry-run", action="store_true")
        sp.add_argument("--limit", type=int, default=0, help="只处理前 N 个句块（调试用）")
        sp.set_defaults(func=fn)
        if name == "run":
            sp.add_argument("--workers", type=int, default=6)
            sp.add_argument("--max-tempo", type=float, default=1.45)
            sp.add_argument("--max-pause", type=float, default=0.30,
                            help="块内超过该秒数的停顿会被压缩")
            sp.add_argument("--fast-retry", dest="fast_retry", action="store_true", default=True)
            sp.add_argument("--no-fast-retry", dest="fast_retry", action="store_false")
            sp.add_argument("--lufs", type=float, default=-16.0)
            sp.add_argument("--bed", type=float, default=-99,
                            help="原声背景音量 dB，如 -24；默认不混")
            sp.add_argument("--container", choices=["mkv", "mp4", "both"], default="both")
            sp.add_argument("--subs-mode", choices=["soft", "burn", "none"], default="soft")
            sp.add_argument("--cue-width", type=float, default=18.0)
            sp.add_argument("--cue-hard-max", type=float, default=26.0)
            sp.add_argument("--line-width", type=float, default=17.0)
            sp.add_argument("--font", default="Microsoft YaHei")
            sp.add_argument("--font-size", type=int, default=18)
            sp.add_argument("--audio-bitrate", default="192k")
            sp.add_argument("--allow-gaps", action="store_true")
            sp.add_argument("--keep-temp", action="store_true")
    return p


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    args = build_parser().parse_args()
    try:
        return args.func(args)
    except (media.DubError, mimo.TTSError) as e:
        print("\n错误：%s" % e, file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\n已中断。", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
