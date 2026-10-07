"""clawbot_push: 通过 WorkBuddy「微信助理」(ClawBot) 通道推送到个人微信。

与 pushplus 的区别（这是选它的理由）：
    PushPlus 走微信公众号服务号，消息落在「订阅号/服务号」里；
    ClawBot 是普通聊天消息，直接出现在微信会话列表里，与好友消息同级。

⚠️ 这是**回复型协议**，不是广播接口：
    `sendmessage` 必须携带 `context_token`，而该 token 只能由
    「用户先给 bot 发过一条消息」产生。token 可缓存复用（实测静置 ≥12 天仍有效）。
    token 失效时服务端返回 ret=-2 "prepare failed"，此时需用户再给 bot 发一条消息刷新。

凭据一律通过环境变量注入（GitHub Secrets），绝不写进代码或配置文件：

    CLAWBOT_BOT_TOKEN      必填  botToken（形如 1b2976b0f569@im.bot:0600...）
    CLAWBOT_CONTEXT_TOKEN  必填  会话上下文 token
    CLAWBOT_TO_USER        必填  收件人 userId（形如 o9cq80...@im.wechat）
    CLAWBOT_BASE_URL       选填  默认 https://ilinkai.weixin.qq.com

协议字段依据（从 WorkBuddy app.asar 内的实现逐字核对）：
    packages/workbuddy-server/src/claw/plugins/weixin/weixin-api.ts
      · buildBaseInfo()            -> { channel_version: "workbuddy-desktop-1.0.0" }
      · randomWechatUin()          -> base64(String(uint32))
      · headers: Content-Type / AuthorizationType: ilink_bot_token /
                 Content-Length / X-WECHAT-UIN / Authorization: Bearer <botToken>
    WeixinClawBotClient.sendTextReply({userId, contextToken}, text)
      · 只依赖 userId + contextToken，无需服务端会话绑定，故可脱离本机客户端运行
"""
from __future__ import annotations

import base64
import json
import os
import random
import time
from dataclasses import dataclass

import requests

DEFAULT_BASE_URL = "https://ilinkai.weixin.qq.com"
CHANNEL_VERSION = "workbuddy-desktop-1.0.0"
TIMEOUT = 25
RETRIES = 2

# 这些错误码重试没有意义（凭据/会话问题，不是网络抖动）
FATAL_RET = {
    -2: "context_token 缺失或已失效 → 让用户给 bot 发一条消息后，重取 token 并更新 CLAWBOT_CONTEXT_TOKEN",
    -14: "会话超时（context_token 过期）→ 同上，需刷新 token",
}

HINT_REFRESH = (
    "刷新方法：在微信里给「微信助理」bot 发一条消息，然后在本机跑\n"
    "  python3 ~/.workbuddy/skills/workbuddy-claw-wechat-send/scripts/send_wx.py --acquire\n"
    "把 ~/.workbuddy/claw-state/weixin/<accountId>.context_token.json 里的 token\n"
    "更新到仓库 Secret CLAWBOT_CONTEXT_TOKEN。"
)


@dataclass
class PushResult:
    ok: bool
    message: str
    raw: str = ""


def read_env() -> dict:
    """从环境变量读取凭据；缺任意必填项返回 {}。"""
    token = (os.getenv("CLAWBOT_BOT_TOKEN") or "").strip()
    context = (os.getenv("CLAWBOT_CONTEXT_TOKEN") or "").strip()
    to_user = (os.getenv("CLAWBOT_TO_USER") or "").strip()
    if not (token and context and to_user):
        return {}
    return {
        "token": token,
        "context_token": context,
        "to_user": to_user,
        "base": (os.getenv("CLAWBOT_BASE_URL") or DEFAULT_BASE_URL).rstrip("/"),
    }


def configured() -> bool:
    return bool(read_env())


def _client_id() -> str:
    """workbuddy-<毫秒时间戳>-<6 位 base36>，与客户端一致。"""
    alphabet = "0123456789abcdefghijklmnopqrstuvwxyz"
    suffix = "".join(random.choice(alphabet) for _ in range(6))
    return f"workbuddy-{int(time.time() * 1000)}-{suffix}"


def _wechat_uin() -> str:
    """base64(十进制字符串的 uint32)，与客户端 randomWechatUin() 一致。"""
    return base64.b64encode(str(random.getrandbits(32)).encode()).decode()


def _compose_text(title: str, content: str) -> str:
    """聊天气泡里 Markdown 不可渲染，统一降级为纯文本。"""
    body = (content or "").strip()
    if title and not body.lstrip().startswith(title.strip()):
        return f"{title.strip()}\n\n{body}"
    return body or title


def send(title: str, content: str, creds: dict | None = None) -> PushResult:
    creds = creds if creds is not None else read_env()
    if not creds:
        return PushResult(
            False,
            "未配置 CLAWBOT_BOT_TOKEN / CLAWBOT_CONTEXT_TOKEN / CLAWBOT_TO_USER",
        )

    payload = {
        "msg": {
            "from_user_id": "",
            "to_user_id": creds["to_user"],
            "client_id": _client_id(),
            "message_type": 2,
            "message_state": 2,
            "context_token": creds["context_token"],
            "item_list": [{"type": 1, "text_item": {"text": _compose_text(title, content)}}],
        },
        "base_info": {"channel_version": CHANNEL_VERSION},
    }
    headers = {
        "Content-Type": "application/json",
        "AuthorizationType": "ilink_bot_token",
        "X-WECHAT-UIN": _wechat_uin(),
        "Authorization": "Bearer " + creds["token"],
    }

    last_err = ""
    for attempt in range(1, RETRIES + 1):
        try:
            resp = requests.post(
                f"{creds['base']}/ilink/bot/sendmessage",
                json=payload,
                headers=headers,
                timeout=TIMEOUT,
            )
            if resp.status_code != 200:
                last_err = f"HTTP {resp.status_code}: {resp.text[:200]}"
                if attempt < RETRIES:
                    time.sleep(2 * attempt)
                continue

            body = resp.json()
            ret = body.get("ret", 0)
            if ret in (0, None):
                mid = body.get("message_id")
                return PushResult(True, f"推送成功 message_id={mid}", str(body))

            # 凭据类错误：不重试，直接给出可执行的下一步
            if ret in FATAL_RET:
                return PushResult(False, f"ret={ret} {body.get('errmsg')}｜{FATAL_RET[ret]}", str(body))
            last_err = f"ret={ret} errmsg={body.get('errmsg')}"
        except Exception as exc:  # noqa: BLE001
            last_err = f"{type(exc).__name__}: {exc}"
            if attempt < RETRIES:
                time.sleep(2 * attempt)

    return PushResult(False, last_err or "推送失败")


if __name__ == "__main__":  # 便于本地自检：python -m src.clawbot_push "文本"
    import sys

    creds = read_env()
    print("凭据齐全:", bool(creds))
    if creds:
        text = sys.argv[1] if len(sys.argv) > 1 else "clawbot_push 自检消息"
        res = send("", text, creds)
        print(("ok  " if res.ok else "FAIL ") + res.message)
        if not res.ok:
            print(HINT_REFRESH)
