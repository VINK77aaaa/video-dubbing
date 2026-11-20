---
name: video-dubbing
description: 用小米 MiMo TTS（mimo-v2.5-tts）把带字幕的视频自动配成中文语音，并生成对齐的字幕文件与内嵌软字幕/硬字幕成片。当用户要求给视频配音、中文配音、AI 朗读视频、把外文视频做成中文解说版、或需要 TTS 批量合成按时间轴对齐的旁白时使用。
agent_created: true
---

# 视频配音（MiMo TTS）

把「视频 + 字幕」变成「中文配音成片」。核心难点不是调 TTS，而是**让逐句合成的语音严丝合缝地落回原片时间轴**——字幕是碎的、TTS 语速和原片不一样、还会偶发超长静音。

## 何时使用

- 给外语视频配中文解说（有现成字幕）
- 把文案/字幕做成按时间轴对齐的旁白
- 需要 TTS 音频 + 对齐字幕 + 成品视频一整套产物

不适用：无字幕视频（需先做 ASR，本技能不含）；需要情感表演/多角色对白的影视级配音。

## 前置条件

- `ffmpeg` / `ffprobe` 在 PATH 中
- 环境变量 `MIMO_API_KEY`（或 `--api-key` 传入）
- Python 3.8+，仅用标准库，无需安装依赖

## 工作流

### 1. 自检

```bash
python scripts/dub.py doctor
```

检查 ffmpeg、可用的 H.264 硬件编码器、API Key 与连通性。任一项失败先修，不要往下走。

### 2. 预演（不花钱）

```bash
python scripts/dub.py plan --video in.mkv --subs in.zh.srt --dry-run
```

输出句块数量、总字数、字/秒、预计 API 调用次数与耗时。**先看这个再决定是否开跑**——长片可能上千次调用。

### 3. 正式执行

```bash
export MIMO_API_KEY=sk-...
python scripts/dub.py run \
  --video in.mkv --subs in.zh.srt --out-dir ./out \
  --voice 苏打 --container both --subs-mode soft
```

产物在 `--out-dir`：`*.zh-dub.mkv`、`*.zh-dub.mp4`、`配音字幕.srt`、`report.json`。

## 关键参数

| 参数 | 默认 | 说明 |
|---|---|---|
| `--voice` | 苏打 | 男声中文。女声用 `冰糖`/`茉莉`；中英混排优先 `mimo_default` |
| `--style` | 内置口语风格 | 风格提示，直接影响语速。**语速主要靠这里调，不是靠 atempo** |
| `--max-chars` | 45 | 句块字数上限。太大则 TTS 一口气读不完，太小则断句生硬 |
| `--max-tempo` | 1.45 | 加速上限。超过会明显失真，宁可顺延 |
| `--rpm` | 85 | 官方限 100，留余量防 429 |
| `--workers` | 6 | 并发数。配合 rpm 生效，别超过 8 |
| `--bed` | 关闭 | 原声背景音量，如 `-24` 保留音乐/环境音 |
| `--subs-mode` | soft | `soft` 软字幕 / `burn` 烧录 / `none` |
| `--limit N` | 0 | 只跑前 N 个句块，调试用 |

## 必须知道的坑

1. **缓存按内容哈希，不按序号**。改文案或换音色会自动重新合成，不会误用旧音频。缓存放在 `<out-dir>/cache/`，删掉即强制重来。
2. **TTS 会在数字/标点处插入超长静音**。见过 "10." 触发 7.9 秒空白，把一句 5 秒的话撑到 13 秒。`--max-pause 0.30` 会压掉块内超过 0.3 秒的停顿，这一步不能省。
3. **不要用 atempo 硬扛语速差异**。原片语速快时，正确做法是改 `--style` 让模型本身读快，atempo 只做 1.0–1.2 的微调。
4. **合成失败必须报错**。默认遇失败即中止，`--allow-gaps` 才允许带空档出片。
5. **英文术语是整词朗读**（Color Boost 约 1.1 秒），不会逐字母拼；但字幕切分必须保证不切断英文词，`split_for_display` 已按 token 处理。
6. **字幕时间轴不能用原字幕**。句块经过合并、加速、顺延，时间轴已变，必须按实际渲染时长反推。

细节参考：
- `references/mimo-tts-api.md` —— 接口协议、音色表、限流与错误码
- `references/pacing.md` —— 语速调优与对齐策略

## 常见调整

**语速偏慢/偏快** → 先改 `--style`（加"语速偏快""干脆利落"），再看 `report.json` 里的 `tempo_avg`。`tempo_avg > 1.15` 说明风格提示还不够快。

**某些句子对不上** → 看 `report.json` 的 `shifted_blocks` 与 `shift_max`。若 `shift_max > 1.0`，调小 `--max-chars` 让句块更短，或加大 `--rpm` 后重跑。

**要发微信/手机** → 软字幕多数播放器不显示，用 `--subs-mode burn --container mp4`。

**保留原片音乐** → `--bed -24`。
