# video-dubbing

用**小米 MiMo TTS**（`mimo-v2.5-tts`）给带字幕的视频自动配中文语音，一次产出**配音成片 + 对齐字幕 + 质检报告**。

不是「调个 TTS 接口」那么简单——真正的难点是让逐句合成的语音严丝合缝地落回原片时间轴：字幕是碎的、TTS 语速和原片不一样、还会偶发超长静音。

## 效果

一部 17 分钟、634 条字幕的 4K 英文教程，全流程约 12 分钟（含 4K 转码）：

| 指标 | 结果 |
|---|---|
| 字幕 → 句块 | 634 条 → 167 块 |
| 合成成功率 | 167/167 |
| 平均加速 | 1.047x（几乎听不出变调） |
| 顺延块数 | 8 / 167，最大 0.7 秒 |
| 片尾累计偏移 | 0.00 秒 |
| 字幕与语音偏差 | < 0.2 秒 |

## 快速开始

```bash
# 1. 自检（ffmpeg / 硬件编码器 / API 连通性）
python scripts/dub.py doctor

# 2. 预演，不消耗配额
python scripts/dub.py plan --video in.mkv --subs in.zh.srt --dry-run

# 3. 正式执行
export MIMO_API_KEY=sk-...
python scripts/dub.py run \
  --video in.mkv --subs in.zh.srt --out-dir ./out \
  --voice 苏打 --container both --subs-mode soft
```

产物：

```
out/
├── in.zh-dub.mkv        视频流直拷 + AAC 配音 + 软字幕
├── in.zh-dub.mp4        H.264 通用版（自动挑硬件编码器）
├── 配音字幕.srt          与配音严格对齐
└── report.json          质检报告
```

## 为什么不是简单拼接

**逐条字幕合成会得到一串支离破碎的短句。** 字幕按阅读节奏切分，一条常只有 1–2 秒、5–10 个字，还经常断在句子中间：

```
[12] 我就完全抛弃了
[13] 你一级调色里的饱和度
```

每条都重新起调、重新收尾，听感像机器人念词条。所以先按标点、停顿和字数上限**合并成语义完整的句块**，再整块朗读。

**TTS 语速和原片天然不匹配。** 同一句话换个风格提示，时长能差 30%。所以语速问题在**风格提示**里解决，`atempo` 只做 1.0–1.2 的微调。

**TTS 会偶发超长静音。** 实测 `"10."` 之后静默 7.9 秒，把一句 5 秒的话撑到 13 秒。必须做块内静音压缩。

**读不完的句子不能截断。** 加速到 1.45x 仍超长时，让后续句子顺延（`start = max(natural_start, cursor)`），遇到空档自动复位，不累积漂移、不切字尾。

## 关键设计

| 机制 | 说明 |
|---|---|
| 缓存按内容哈希 | 键包含文本+音色+风格+模型，换音色不会误用旧音频 |
| 失败即中止 | 合成失败默认报错，`--allow-gaps` 才允许带空档出片 |
| 两遍 loudnorm | 精确到 -16 LUFS，峰值 -1.5 dB |
| 二次重合成 | 超长块先用更快语速重试一次，再考虑加速 |
| 字幕保护英文词 | `Color Boost` 不会被拆成 `Color` / `Boost` |
| 断点续跑 | 重跑自动复用 `<out-dir>/cache/` |

## 参数

| 参数 | 默认 | 说明 |
|---|---|---|
| `--voice` | 苏打 | `苏打`/`白桦` 男声，`冰糖`/`茉莉` 女声，中英混排用 `mimo_default` |
| `--style` | 内置口语风格 | **语速主要靠这里调** |
| `--max-chars` | 45 | 句块字数上限（≈7 秒） |
| `--max-tempo` | 1.45 | 加速上限，超过明显失真 |
| `--rpm` | 85 | 官方限 100，留余量 |
| `--workers` | 6 | 并发数 |
| `--bed` | 关闭 | 原声背景音量，如 `-24` 保留音乐 |
| `--subs-mode` | soft | `soft` / `burn` / `none` |
| `--limit N` | 0 | 只跑前 N 块，调试用 |

## 依赖

- Python 3.8+（仅标准库，无第三方依赖）
- `ffmpeg` / `ffprobe` 在 PATH 中
- MiMo API Key：https://platform.xiaomimimo.com/console/api-keys

## 自测

```bash
python tests/test_subs.py
```

## 已知限制

- **需要现成字幕**。无字幕视频要先做 ASR，本工具不含该环节。
- 只做单人旁白，不支持多角色对白分轨。
- 硬字幕烧录依赖 ffmpeg 的 libass，字体需系统已安装。

## 接口参考

MiMo TTS 的协议、音色表、限流与踩坑记录见 `references/mimo-tts-api.md`，语速与对齐策略见 `references/pacing.md`。
