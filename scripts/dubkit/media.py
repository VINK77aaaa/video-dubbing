# -*- coding: utf-8 -*-
"""ffmpeg / ffprobe 封装：探测、裁剪、变速、拼接、响度标准化。"""
import json
import os
import re
import shutil
import subprocess


class DubError(RuntimeError):
    pass


def require_tools():
    missing = [t for t in ("ffmpeg", "ffprobe") if shutil.which(t) is None]
    if missing:
        raise DubError(
            "缺少依赖：%s。请先安装 ffmpeg 并加入 PATH。\n"
            "  Windows: winget install Gyan.FFmpeg\n"
            "  macOS:   brew install ffmpeg\n"
            "  Linux:   sudo apt install ffmpeg" % ", ".join(missing)
        )


def run(cmd, timeout=None):
    p = subprocess.run(cmd, capture_output=True, timeout=timeout)
    if p.returncode != 0:
        tail = p.stderr.decode("utf-8", "ignore").strip().splitlines()[-12:]
        raise DubError("命令失败：%s\n%s" % (" ".join(cmd)[:400], "\n".join(tail)))
    return p


def probe(path):
    p = run(["ffprobe", "-v", "error", "-print_format", "json",
             "-show_format", "-show_streams", path])
    return json.loads(p.stdout.decode("utf-8", "ignore"))


def duration_of(path):
    p = run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", path])
    return float(p.stdout.decode().strip())


def has_audio(path):
    return any(s.get("codec_type") == "audio" for s in probe(path).get("streams", []))


def trim_clip(src, dst, sr, silence_db=-40, max_pause=0.30, keep_pause=0.18, edge=0.04):
    """压掉块内超长停顿（TTS 在数字/标点处偶发数秒静默），并修剪首尾静音。"""
    af = ("silenceremove=stop_periods=-1:stop_duration=%.2f:stop_threshold=%ddB:stop_silence=%.2f,"
          "areverse,"
          "silenceremove=start_periods=1:start_threshold=%ddB:start_silence=%.2f,"
          "areverse" % (max_pause, silence_db, keep_pause, silence_db - 5, edge))
    run(["ffmpeg", "-y", "-v", "error", "-i", src, "-af", af,
         "-ar", str(sr), "-ac", "1", dst])


def render_clip(src, dst_pcm, tempo, sr, ch, fade=0.008):
    """按倍速渲染成目标采样率/声道的裸 PCM，便于按字节精确拼接。"""
    chain = []
    if abs(tempo - 1.0) > 1e-4:
        chain.append("atempo=%.5f" % tempo)
    chain.append("aresample=%d" % sr)
    if ch == 2:
        chain.append("pan=stereo|c0=c0|c1=c0")
    chain.append("afade=t=in:st=0:d=%.3f" % fade)
    run(["ffmpeg", "-y", "-v", "error", "-i", src, "-af", ",".join(chain),
         "-f", "s16le", "-acodec", "pcm_s16le", "-ar", str(sr), "-ac", str(ch), dst_pcm])


def silence_bytes(seconds, sr, ch):
    frame = sr * ch * 2
    n = int(round(seconds * sr)) * ch * 2
    return b"\x00" * max(0, n - (n % 4))


def loudnorm(in_pcm, out_wav, sr, ch, target_i=-16.0, target_tp=-1.5, target_lra=11.0):
    """两遍 loudnorm；解析失败时自动退回单遍。"""
    src = ["-f", "s16le", "-ar", str(sr), "-ac", str(ch), "-i", in_pcm]
    params = "loudnorm=I=%.1f:TP=%.1f:LRA=%.1f" % (target_i, target_tp, target_lra)
    measured = None
    try:
        p = subprocess.run(["ffmpeg", "-hide_banner", "-v", "info"] + src +
                           ["-af", params + ":print_format=json", "-f", "null", "-"],
                           capture_output=True)
        blob = p.stderr.decode("utf-8", "ignore")
        start, end = blob.rfind("{"), blob.rfind("}")
        if start != -1 and end > start:
            m = json.loads(blob[start:end + 1])
            if m.get("input_i") not in (None, "-inf"):
                measured = m
    except Exception:
        measured = None

    af = params
    if measured:
        af = (params + ":measured_I=%s:measured_TP=%s:measured_LRA=%s:measured_thresh=%s"
              ":offset=%s:linear=true" % (measured["input_i"], measured["input_tp"],
                                          measured["input_lra"], measured["input_thresh"],
                                          measured.get("target_offset", "0.0")))
    run(["ffmpeg", "-y", "-v", "error"] + src + ["-af", af + ",aresample=%d" % sr,
         "-c:a", "pcm_s16le", out_wav])
    return bool(measured)


def mux(video, audio, out, container="mkv", video_bitrate=None, audio_bitrate="192k",
        subtitle=None, faststart=True, duration=None, extra_video_args=None):
    """封装：MKV 直拷视频流；MP4 用 QSV/NVENC/libx264 转 H.264。

    duration 用于把音轨裁到与视频等长（配音轨尾部常多补半秒静音）。
    """
    cmd = ["ffmpeg", "-y", "-v", "error", "-i", video, "-i", audio]
    maps = ["-map", "0:v:0", "-map", "1:a:0"]
    if subtitle:
        cmd += ["-i", subtitle]
        maps += ["-map", "2:s:0"]
    cmd += maps
    if container == "mkv":
        cmd += ["-c:v", "copy", "-c:a", "aac", "-b:a", audio_bitrate]
        if subtitle:
            cmd += ["-c:s", "srt", "-metadata:s:s:0", "language=chi",
                    "-metadata:s:s:0", "title=中文配音字幕"]
    else:
        enc = pick_h264_encoder()
        cmd += enc + ["-c:a", "aac", "-b:a", audio_bitrate]
        if subtitle:
            cmd += ["-c:s", "mov_text", "-metadata:s:s:0", "language=chi",
                    "-metadata:s:s:0", "title=中文配音字幕"]
        if faststart:
            cmd += ["-movflags", "+faststart"]
    if duration:
        cmd += ["-t", "%.3f" % duration]
    cmd += [out]
    run(cmd)
    return out


_ENC_CACHE = {}


def pick_h264_encoder():
    """按 NVENC → QSV → libx264 顺序挑一个真能用的硬件编码器。"""
    if "enc" in _ENC_CACHE:
        return _ENC_CACHE["enc"]
    probes = [
        (["-c:v", "h264_nvenc", "-preset", "p5", "-cq", "22", "-b:v", "10M"],
         ["-c:v", "h264_nvenc", "-preset", "p5", "-cq", "22"]),
        (["-c:v", "h264_qsv", "-global_quality", "22"], ["-c:v", "h264_qsv", "-global_quality", "22"]),
        (["-c:v", "libx264", "-preset", "medium", "-crf", "20"], ["-c:v", "libx264", "-preset", "medium", "-crf", "20"]),
    ]
    for full, short in probes:
        try:
            subprocess.run(["ffmpeg", "-y", "-hide_banner", "-v", "error",
                            "-f", "lavfi", "-i", "color=c=black:s=256x144:d=0.2:r=10",
                            "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
                            "-t", "0.2"] + full + ["-f", "null", "-"],
                           capture_output=True, timeout=25, check=True)
            _ENC_CACHE["enc"] = full
            return full
        except Exception:
            continue
    _ENC_CACHE["enc"] = probes[-1][0]
    return probes[-1][0]


def burn_subtitles(video, srt, out, font="Microsoft YaHei", font_size=18,
                   encoder=None, crf_quality=None):
    """硬字幕烧录（需要 ffmpeg 带 libass）。"""
    path = os.path.abspath(srt).replace("\\", "/").replace(":", "\\:")
    style = ("FontName=%s,FontSize=%d,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,"
             "BorderStyle=1,Outline=2,Shadow=1,MarginV=28" % (font, font_size))
    vf = "subtitles=filename='%s':force_style='%s'" % (path, style)
    enc = encoder or pick_h264_encoder()
    run(["ffmpeg", "-y", "-v", "error", "-i", video, "-vf", vf] + enc +
        ["-c:a", "copy", "-movflags", "+faststart", out])
    return out


def mix_bed(dub_audio, original, out, bed_gain_db=-24.0, duration=None):
    """把原声压低后垫在配音下面（保留音乐/环境音）。"""
    cmd = ["ffmpeg", "-y", "-v", "error", "-i", dub_audio, "-i", original,
           "-filter_complex",
           "[1:a]volume=%.1fdB[bed];[0:a][bed]amix=inputs=2:duration=first:normalize=0[a]"
           % bed_gain_db,
           "-map", "[a]", "-c:a", "pcm_s16le", out]
    if duration:
        cmd += ["-t", "%.3f" % duration]
    run(cmd)
    return out
