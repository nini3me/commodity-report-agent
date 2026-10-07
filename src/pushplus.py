"""pushplus: 微信推送。

Token 通过环境变量 PUSHPLUS_TOKEN 注入（GitHub Actions 里配 Secret），
绝不写进代码或配置文件。

· 只配 PUSHPLUS_TOKEN      -> 发给自己
· 另配 PUSHPLUS_TOPIC      -> 发给群组（自己 + 已订阅群组的好友）
"""
from __future__ import annotations

import time
from dataclasses import dataclass

import requests

PUSHPLUS_URL = "https://www.pushplus.plus/send"
TIMEOUT = 20
RETRIES = 3


@dataclass
class PushResult:
    ok: bool
    message: str
    raw: str = ""


def send(token: str, title: str, content: str, template: str = "markdown",
         topic: str | None = None, channel: str = "wechat") -> PushResult:
    if not token:
        return PushResult(False, "未配置 PUSHPLUS_TOKEN")

    payload = {
        "token": token,
        "title": title[:100],
        "content": content,
        "template": template,
        "channel": channel,
    }
    if topic:
        payload["topic"] = topic

    last_err = ""
    for attempt in range(1, RETRIES + 1):
        try:
            resp = requests.post(PUSHPLUS_URL, json=payload, timeout=TIMEOUT)
            body = resp.json()
            code = body.get("code")
            if code == 200:
                return PushResult(True, "推送成功", str(body))
            last_err = f"code={code} msg={body.get('msg')}"
            # 903 无效 token / 905 未实名，重试没有意义
            if code in (903, 905):
                break
        except Exception as exc:  # noqa: BLE001
            last_err = f"{type(exc).__name__}: {exc}"
            if attempt < RETRIES:
                time.sleep(2 * attempt)
    return PushResult(False, last_err or "推送失败")
