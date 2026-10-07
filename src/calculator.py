"""calculator: 涨跌额/涨跌幅计算 + 主力合约换月检测。"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .market_data import Quote

UP, DOWN, FLAT, UNKNOWN = 1, -1, 0, 0


@dataclass
class Row:
    """报告中的一行：行情 + 计算结果。"""

    quote: Quote
    amount: Optional[float] = None
    pct: Optional[float] = None
    direction: int = 0

    @property
    def name(self) -> str:
        return self.quote.name

    @property
    def has_change(self) -> bool:
        return self.amount is not None and self.pct is not None


def compute_change(price: Optional[float], prev: Optional[float]) -> tuple[Optional[float],
                                                                          Optional[float], int]:
    """返回 (涨跌额, 涨跌幅%, 方向)。"""
    if price is None or prev is None or prev == 0:
        return None, None, UNKNOWN
    amount = price - prev
    pct = amount / prev * 100
    if abs(amount) < 1e-12:
        direction = FLAT
    else:
        direction = UP if amount > 0 else DOWN
    return amount, pct, direction


def build_rows(quotes: list[Quote]) -> list[Row]:
    rows: list[Row] = []
    for quote in quotes:
        amount, pct, direction = compute_change(quote.price, quote.prev)
        rows.append(Row(quote=quote, amount=amount, pct=pct, direction=direction))
    return rows


# ----------------------------------------------------------------- 换月检测
def detect_rollovers(current: dict[str, dict], previous: Optional[dict[str, dict]],
                     names: dict[str, str] | None = None) -> list[str]:
    """比对主力合约快照，返回换月提示文案列表。"""
    names = names or {}
    if not previous:
        return []
    notes: list[str] = []
    for product_id, now in current.items():
        before = previous.get(product_id)
        if not before:
            continue
        old, new = before.get("contract"), now.get("contract")
        if old and new and old != new:
            label = names.get(product_id, product_id)
            notes.append(
                f"{label} 主力合约已切换：{old.upper()} → {new.upper()}"
                f"（主连涨跌可能受换月跳空影响）"
            )
    return notes


def jump_notes(rows: list[Row], threshold: float, names: dict[str, str] | None = None) -> list[str]:
    """无历史快照时，用主连单日跳变做兜底提醒。"""
    names = names or {}
    notes: list[str] = []
    for row in rows:
        if row.quote.section != "domestic" or row.pct is None:
            continue
        if abs(row.pct) >= threshold:
            label = names.get(row.quote.id, row.quote.name)
            notes.append(
                f"{label} 主连单日变动 {row.pct:+.2f}%，偏离常态，请留意是否处于换月期"
            )
    return notes


# ----------------------------------------------------------------- 状态快照
def load_state(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh) or {}
    except Exception:  # noqa: BLE001
        return {}


def save_state(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.write("\n")


def make_snapshot(main_contracts: dict[str, dict], rows: list[Row],
                  pushed_at: datetime, push_ok: bool) -> dict:
    domestic_last = {}
    for row in rows:
        if row.quote.section == "domestic" and row.quote.price is not None:
            domestic_last[row.quote.id] = {
                "settle": row.quote.price,
                "date": row.quote.price_date,
            }
    return {
        "updated_at": pushed_at.astimezone(timezone.utc).isoformat(timespec="seconds"),
        "main_contracts": main_contracts,
        "domestic_last": domestic_last,
        "last_push": {
            "date": pushed_at.strftime("%Y-%m-%d"),
            "ok": push_ok,
        },
    }
