# 小米 MiMo TTS 接口参考

## 协议

OpenAI 兼容的 `chat/completions`，音频放在 `audio` 字段里返回 base64。

```
POST https://api.xiaomimimo.com/v1/chat/completions
Authorization: Bearer <API_KEY>
Content-Type: application/json
```

```json
{
  "model": "mimo-v2.5-tts",
  "messages": [
    {"role": "user",      "content": "<风格提示，可省略>"},
    {"role": "assistant", "content": "<要朗读的正文>"}
  ],
  "audio": {"format": "wav", "voice": "苏打"}
}
```

响应：

```json
{"choices": [{"message": {
  "role": "assistant",
  "audio": {"id": "...", "data": "<base64>", "expires_at": ..., "transcript": null}
}}]}
```

- **正文必须放 `assistant` 角色**，放 `user` 会被当成指令而不是朗读内容。
- 风格提示放 `user`，可省略；省略时模型从文本自行推断情绪。
- `format` 支持 `wav` / `mp3`。wav 是 24 kHz 单声道 pcm_s16le，本工具后续统一重采样到 48 kHz。
- `api-key: <KEY>` 头也可用，但 `Authorization: Bearer` 更通用。

## 模型

| 模型 | 用途 |
|---|---|
| `mimo-v2.5-tts` | 预设音色，最常用 |
| `mimo-v2.5-tts-voicedesign` | 自然语言描述设计音色，必须提供风格提示 |
| `mimo-v2.5-tts-voiceclone` | 声音克隆，需 base64 data URI 音频样本 |

## 预设音色

| ID | 性别 | 语言 |
|---|---|---|
| `mimo_default` | 女 | 中 / 英 |
| `冰糖` | 女 | 中文 |
| `茉莉` | 女 | 中文 |
| `苏打` | 男 | 中文 |
| `白桦` | 男 | 中文 |
| `Mia` | 女 | 英文 |
| `Chloe` | 女 | 英文 |
| `Milo` | 男 | 英文 |
| `Dean` | 男 | 英文 |

中英混排（技术教程常见）优先 `mimo_default`；纯中文男声选 `苏打`，实测语速最接近自然讲解。

## 限流与配额

- RPM 100，TPM 10M
- 上下文 8K token，单次输出上限 8K——**单次请求不要超过约 500 字**，本工具默认 45 字/块
- TTS 系列目前免费

## 错误处理要点

| 状态 | 含义 | 处理 |
|---|---|---|
| 429 | 限流 | 读 `Retry-After`，退避后重试；并发降到 4–6 |
| 400 | 参数错（音色不存在等） | 不要重试，直接报错 |
| 401 / 403 | Key 无效或无权限 | 不要重试 |
| 5xx | 服务端抖动 | 指数退避重试 3–4 次 |

## 已知行为（实测）

- **数字后可能插入超长静音**：`"10. 看看这个"` 曾产生 7.9 秒空白。必须做块内静音压缩。
- **英文术语整词朗读**：`Color Boost` 约 1.12 秒，`Resolve` 约 1.12 秒，不会拼字母。
- **语速对风格提示敏感**：同一句话，普通风格 5.76 秒，加"语速偏快、干脆利落"后 4.00 秒，差 30%。
- **首尾自带少量静音**：约 0.03–0.06 秒，需修剪，否则拼接处会有可感知的粘连。
