# -*- coding: utf-8 -*-
"""小米 MiMo TTS 客户端：OpenAI 兼容 chat/completions + audio 字段。"""
import base64
import hashlib
import json
import os
import threading
import time
import urllib.error
import urllib.request

DEFAULT_BASE = "https://api.xiaomimimo.com/v1"
DEFAULT_MODEL = "mimo-v2.5-tts"

# 官方内置音色（mimo-v2.5-tts 预设音色模式）
VOICES = {
    "mimo_default": "MiMo 默认（中/英）",
    "冰糖": "女声 · 中文",
    "茉莉": "女声 · 中文",
    "苏打": "男声 · 中文",
    "白桦": "男声 · 中文",
    "Mia": "女声 · 英文",
    "Chloe": "女声 · 英文",
    "Milo": "男声 · 英文",
    "Dean": "男声 · 英文",
}

DEFAULT_STYLE = ("语速偏快、干脆利落的中文口语讲解，像视频博主一样边说边想，"
                 "语气自然放松，不要播音腔，不要拖长音，英文术语按英文发音。")


class TTSError(RuntimeError):
    pass


def resolve_api_key(explicit=None):
    key = explicit or os.environ.get("MIMO_API_KEY") or ""
    key = key.strip()
    if not key:
        raise TTSError(
            "缺少 API Key。请设置环境变量 MIMO_API_KEY，或用 --api-key 传入。\n"
            "  获取地址：https://platform.xiaomimimo.com/console/api-keys"
        )
    return key


def cache_key(text, voice, style, model, fmt="wav"):
    """缓存键必须包含所有影响音频的因素，否则换音色会静默复用旧文件。"""
    raw = "\x1f".join([model, voice, fmt, style, text])
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


class MimoTTS:
    def __init__(self, api_key=None, base_url=DEFAULT_BASE, model=DEFAULT_MODEL,
                 voice="苏打", style=DEFAULT_STYLE, rpm=85, timeout=180):
        self.api_key = resolve_api_key(api_key)
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.model = model
        self.voice = voice
        self.style = style
        self.timeout = timeout
        self.min_interval = 60.0 / max(1, rpm)
        self._lock = threading.Lock()
        self._last = 0.0
        self.calls = 0

    def _throttle(self):
        with self._lock:
            now = time.time()
            wait = self._last + self.min_interval - now
            if wait > 0:
                time.sleep(wait)
                now = time.time()
            self._last = now
            self.calls += 1

    def key_for(self, text):
        return cache_key(text, self.voice, self.style, self.model)

    def synthesize(self, text, style=None, retries=4):
        payload = {
            "model": self.model,
            "messages": [
                {"role": "user", "content": style or self.style},
                {"role": "assistant", "content": text},
            ],
            "audio": {"format": "wav", "voice": self.voice},
        }
        body = json.dumps(payload).encode("utf-8")
        last = None
        for attempt in range(retries):
            self._throttle()
            req = urllib.request.Request(self.url, data=body, method="POST")
            req.add_header("Content-Type", "application/json")
            req.add_header("Authorization", "Bearer " + self.api_key)
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    res = json.loads(r.read())
                msg = res["choices"][0]["message"]
                audio = msg.get("audio") or {}
                if not audio.get("data"):
                    raise TTSError("响应里没有音频数据：%s" % str(msg)[:200])
                return base64.b64decode(audio["data"])
            except urllib.error.HTTPError as e:
                detail = e.read()[:300].decode("utf-8", "ignore")
                last = "HTTP %s %s" % (e.code, detail)
                if e.code == 429:
                    # 尊重服务端 Retry-After，否则指数退避
                    wait = 5.0
                    try:
                        wait = float(e.headers.get("Retry-After", 5))
                    except Exception:
                        pass
                    time.sleep(min(wait, 60))
                    continue
                if e.code in (400, 401, 403, 404):
                    raise TTSError(last)   # 参数/鉴权错误，重试无意义
            except Exception as e:
                last = "%s: %s" % (type(e).__name__, e)
            time.sleep(1.5 * (attempt + 1))
        raise TTSError("重试 %d 次仍失败：%s" % (retries, last))
