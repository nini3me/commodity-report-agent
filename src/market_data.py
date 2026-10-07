"""market_data: 行情获取。

数据源
------
外盘（BZ=F / CL=F / GC=F / SI=F）
    主源 yfinance（取其最新价与上一交易日结算价），
    失败自动回退新浪 hf_ 实时接口（hf_OIL / hf_CL / hf_GC / hf_SI）。

内盘（SC / AU / AG / PR 主连）
    新浪期货日线接口 InnerFuturesNewService.getDailyKLine，
    该接口直接返回每个交易日的 **结算价（字段 s）**，
    因此可以严格按需求文档口径取「最近交易日结算价 vs 前一交易日结算价」，
    完全不受北京时间 08:30 国内未开盘、以及夜盘行情的影响。

暗盘
    仅在美国市场休市时附加，走新浪 hf_ 实时接口。

所有网络请求均带重试；任一品种失败只影响该品种，不会中断整份报告。
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Iterable, Optional

import requests

SINA_QUOTE_URL = "https://hq.sinajs.cn/list={}"
SINA_KLINE_URL = (
    "https://stock2.finance.sina.com.cn/futures/api/jsonp.php/"
    "var%20_{symbol}=/InnerFuturesNewService.getDailyKLine?symbol={symbol}"
)
SINA_HEADERS = {
    "Referer": "https://finance.sina.com.cn",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
}
BATCH_SIZE = 40
TIMEOUT = 20
RETRIES = 3


# ----------------------------------------------------------------- utils
def _num(value) -> Optional[float]:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if out != out:  # NaN
        return None
    return out


def _http_get(url: str, headers: dict | None = None, encoding: str = "utf-8") -> str:
    last_err: Exception | None = None
    for attempt in range(1, RETRIES + 1):
        try:
            resp = requests.get(url, headers=headers or SINA_HEADERS, timeout=TIMEOUT)
            resp.raise_for_status()
            return resp.content.decode(encoding, errors="ignore")
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            if attempt < RETRIES:
                time.sleep(1.5 * attempt)
    raise RuntimeError(f"请求失败 {url}: {last_err}")


def _chunks(seq: list, size: int) -> Iterable[list]:
    for i in range(0, len(seq), size):
        yield seq[i:i + size]


# ----------------------------------------------------------------- 数据结构
@dataclass
class Quote:
    """一条行情记录。price 为报告展示价，prev 为对比基准价。"""

    id: str
    name: str
    price: Optional[float] = None
    prev: Optional[float] = None
    unit: str = ""
    decimals: int = 2
    source: str = ""
    price_date: Optional[str] = None
    basis_date: Optional[str] = None
    section: str = ""
    note: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.price is not None


@dataclass
class Kline:
    """内盘日线的一根 K 线。"""

    day: str
    close: Optional[float]
    settle: Optional[float]
    volume: Optional[float]
    open_interest: Optional[float]


# ----------------------------------------------------------------- 新浪接口
def fetch_sina_quotes(codes: list[str]) -> dict[str, list[str]]:
    """批量抓取新浪实时行情，返回 {代码: 字段列表}。"""
    result: dict[str, list[str]] = {}
    if not codes:
        return result
    for chunk in _chunks(list(codes), BATCH_SIZE):
        url = SINA_QUOTE_URL.format(",".join(chunk))
        text = _http_get(url, encoding="gbk")
        for match in re.finditer(r'hq_str_(\w+)="([^"]*)"', text):
            payload = match.group(2)
            if payload:
                result[match.group(1)] = payload.split(",")
    return result


def fetch_daily_kline(symbol: str) -> list[Kline]:
    """抓取新浪期货主连日线，含结算价。"""
    url = SINA_KLINE_URL.format(symbol=symbol)
    text = _http_get(url, encoding="utf-8")
    start, end = text.find("["), text.rfind("]")
    if start < 0 or end < 0:
        raise RuntimeError(f"{symbol} 日线数据解析失败")
    rows = json.loads(text[start:end + 1])
    out: list[Kline] = []
    for row in rows:
        out.append(
            Kline(
                day=str(row.get("d", "")),
                close=_num(row.get("c")),
                settle=_num(row.get("s")),
                volume=_num(row.get("v")),
                open_interest=_num(row.get("p")),
            )
        )
    return out


def _hf_fields(fields: list[str]) -> tuple[Optional[float], Optional[float], Optional[str]]:
    """解析新浪 hf_ 外盘字段：最新价、昨收、日期。"""
    price = _num(fields[0]) if len(fields) > 0 else None
    prev = _num(fields[7]) if len(fields) > 7 else None
    day = fields[12].strip() if len(fields) > 12 else None
    return price, prev, day


# ----------------------------------------------------------------- 外盘
# yfinance 熔断开关：
#   Yahoo Finance 在中国大陆网络下返回 403，在 GitHub Actions 等海外节点则正常。
#   直接用 yfinance 探测会触发它内部的重试与退避，可能拖慢甚至拖垮整个任务，
#   因此先用一次轻量 HTTP 请求做可达性探测，不通就整轮跳过 yfinance。
_YF_STATE: dict[str, Any] = {"disabled": False, "reason": ""}
YAHOO_PROBE_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval=1d&range=1d"
UA = SINA_HEADERS["User-Agent"]


def yfinance_state() -> dict[str, Any]:
    return dict(_YF_STATE)


def reset_yfinance() -> None:
    _YF_STATE["disabled"] = False
    _YF_STATE["reason"] = ""


def yahoo_reachable(symbol: str = "GC=F", timeout: int = 8) -> tuple[bool, str]:
    """轻量探测 Yahoo 是否可访问（中国大陆网络下为 403）。"""
    try:
        resp = requests.get(
            YAHOO_PROBE_URL.format(symbol=symbol),
            headers={"User-Agent": UA},
            timeout=timeout,
        )
        if resp.status_code == 200:
            return True, "ok"
        return False, f"HTTP {resp.status_code}"
    except Exception as exc:  # noqa: BLE001
        return False, type(exc).__name__


def _yfinance_quote(code: str) -> tuple[Optional[float], Optional[float], Optional[str]]:
    """yfinance：返回 (最新价, 上一交易日结算价, 行情日期)。"""
    if _YF_STATE["disabled"]:
        raise RuntimeError(f"yfinance 本轮已熔断（{_YF_STATE['reason']}）")

    import yfinance as yf  # 延迟导入，未安装时直接走回退源

    ticker = yf.Ticker(code)
    last = prev = None

    def _pick(info, key):
        try:
            return info[key]
        except Exception:  # noqa: BLE001
            try:
                return info.get(key)  # type: ignore[attr-defined]
            except Exception:  # noqa: BLE001
                return None

    try:
        info = ticker.fast_info
        last = _num(_pick(info, "lastPrice"))
        # 期货的「上一交易日结算价」对应以下两个键之一
        prev = _num(_pick(info, "previousClose")) or _num(
            _pick(info, "regularMarketPreviousClose")
        )
    except Exception as exc:  # noqa: BLE001
        _YF_STATE["disabled"] = True
        _YF_STATE["reason"] = f"{type(exc).__name__}: {exc}"
        raise

    if last is None:
        hist = ticker.history(period="10d", interval="1d", auto_adjust=False)
        closes = [c for c in hist["Close"].tolist() if c == c] if not hist.empty else []
        if closes:
            last = _num(closes[-1])
            if prev is None and len(closes) >= 2:
                prev = _num(closes[-2])

    return last, prev, None


def get_overseas(products: list[dict], source: str = "auto") -> list[Quote]:
    """抓取外盘行情。source: auto / yfinance / sina"""
    reset_yfinance()

    # auto 模式：先探测 Yahoo 可达性，不通就整轮走新浪，避免被 yfinance 内部重试拖死
    if source == "auto":
        probe_symbol = products[0]["code"] if products else "GC=F"
        reachable, detail = yahoo_reachable(probe_symbol)
        if not reachable:
            _YF_STATE["disabled"] = True
            _YF_STATE["reason"] = f"Yahoo 不可达（{detail}）"
            print(f"[warn] Yahoo 不可达（{detail}），本轮外盘改用新浪财经接口")

    out: list[Quote] = []
    for item in products:
        quote = Quote(
            id=item["id"],
            name=item["name"],
            unit=item.get("unit", ""),
            decimals=int(item.get("decimals", 2)),
            section="overseas",
        )
        if source in ("auto", "yfinance"):
            try:
                last, prev, day = _yfinance_quote(item["code"])
                if last is not None:
                    quote.price, quote.prev, quote.price_date = last, prev, day
                    quote.source = f"yfinance:{item['code']}"
            except Exception as exc:  # noqa: BLE001
                state = yfinance_state()
                detail = state["reason"] if state["disabled"] else f"{type(exc).__name__}: {exc}"
                quote.note = f"yfinance 不可用（{detail}）"

        if quote.price is None and item.get("sina"):
            try:
                fields = fetch_sina_quotes([item["sina"]]).get(item["sina"])
                if fields:
                    last, prev, day = _hf_fields(fields)
                    if last is not None:
                        quote.price, quote.prev, quote.price_date = last, prev, day
                        quote.source = f"sina:{item['sina']}"
            except Exception as exc:  # noqa: BLE001
                quote.note = (quote.note + f" 新浪回退失败（{type(exc).__name__}）").strip()

        out.append(quote)
    return out


# ----------------------------------------------------------------- 内盘
def get_domestic(products: list[dict], today: date, include_today: bool = False
                 ) -> tuple[list[Quote], dict[str, list[Kline]]]:
    """抓取内盘主连，按结算价口径。

    返回 (行情列表, {品种id: 过滤后的日线列表})
    """
    today_iso = today.isoformat()
    quotes: list[Quote] = []
    klines: dict[str, list[Kline]] = {}

    for item in products:
        quote = Quote(
            id=item["id"],
            name=item.get("label") or item["name"],
            unit=item.get("unit", ""),
            decimals=int(item.get("decimals", 2)),
            section="domestic",
        )
        try:
            rows = fetch_daily_kline(item["symbol"])
        except Exception as exc:  # noqa: BLE001
            quote.note = f"日线抓取失败（{type(exc).__name__}）"
            quotes.append(quote)
            continue

        # 北京时间 08:30 运行时，当天尚未结算，因此剔除当天及以后的记录
        usable = [r for r in rows if (r.day <= today_iso if include_today else r.day < today_iso)]
        usable = [r for r in usable if r.settle is not None]
        klines[item["id"]] = usable

        if len(usable) < 2:
            quote.note = "可用交易日不足 2 天，无法计算涨跌"
            quotes.append(quote)
            continue

        latest, prior = usable[-1], usable[-2]
        quote.price = latest.settle
        quote.prev = prior.settle
        quote.price_date = latest.day
        quote.basis_date = prior.day
        quote.source = f"sina:{item['symbol']}"
        quote.extra = {
            "close": latest.close,
            "open_interest": latest.open_interest,
            "volume": latest.volume,
            "exchange": item.get("exchange", ""),
        }
        quotes.append(quote)

    return quotes, klines


# ----------------------------------------------------------------- 暗盘
def get_dark_pool(items: list[dict]) -> list[Quote]:
    out: list[Quote] = []
    try:
        codes = [i["sina"] for i in items]
        snapshot = fetch_sina_quotes(codes)
    except Exception:  # noqa: BLE001
        snapshot = {}

    for item in items:
        quote = Quote(
            id=item["sina"],
            name=item["name"],
            unit=item.get("unit", ""),
            decimals=int(item.get("decimals", 2)),
            section="dark_pool",
        )
        fields = snapshot.get(item["sina"])
        if fields:
            last, prev, day = _hf_fields(fields)
            quote.price, quote.prev, quote.price_date = last, prev, day
            quote.source = f"sina:{item['sina']}"
        else:
            quote.note = "无报价"
        out.append(quote)
    return out


# ----------------------------------------------------------------- 主力合约
def _month_cursor(start: date, count: int) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    year, month = start.year, start.month
    for _ in range(count):
        out.append((year, month))
        month += 1
        if month > 12:
            month = 1
            year += 1
    return out


def get_main_contracts(products: list[dict], today: date, horizon: int = 15,
                       by: str = "open_interest") -> dict[str, dict]:
    """枚举各品种在市合约，按持仓量/成交量选出主力合约。

    返回 {品种id: {"contract": "au2612", "open_interest": ..., "volume": ..., "price": ...}}
    """
    codes: list[str] = []
    index: dict[str, tuple[str, str]] = {}
    for item in products:
        prefix = str(item.get("prefix") or item["symbol"][:-1]).upper()
        for year, month in _month_cursor(today, horizon):
            contract = f"{prefix}{year % 100:02d}{month:02d}"
            sina_code = f"nf_{contract}"
            codes.append(sina_code)
            index[sina_code] = (item["id"], contract.lower())

    try:
        snapshot = fetch_sina_quotes(codes)
    except Exception:  # noqa: BLE001
        snapshot = {}

    candidates: dict[str, list[dict]] = {}
    for code, fields in snapshot.items():
        if code not in index or len(fields) < 15:
            continue
        oi = _num(fields[13]) or 0.0
        vol = _num(fields[14]) or 0.0
        if oi <= 0 and vol <= 0:
            continue
        product_id, contract = index[code]
        candidates.setdefault(product_id, []).append(
            {
                "contract": contract,
                "open_interest": oi,
                "volume": vol,
                "price": _num(fields[8]),
                "full_name": fields[0],
            }
        )

    key = "volume" if by == "volume" else "open_interest"
    out: dict[str, dict] = {}
    for product_id, rows in candidates.items():
        rows.sort(key=lambda r: (r[key], r["open_interest"]), reverse=True)
        out[product_id] = rows[0]
    return out
