"""report: 生成 Markdown 日报。

约定
----
· 涨跌配色遵循国内习惯：涨红 🔴、跌绿 🟢。
· 报告只陈述行情事实，不含任何投资建议、买卖判断或预测。
· 同时产出 Markdown 与纯文本两版（企业微信等不支持表格的通道用纯文本版）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from .calculator import DOWN, FLAT, UP, Row
from .holiday import MarketStatus

LABEL_OVERSEAS = "外盘"
LABEL_DOMESTIC = "内盘"
LABEL_DARK = "暗盘行情"

FOOTER = "数据来源：新浪财经 / yfinance · 本报告仅为行情数据汇总，不构成任何投资建议"


@dataclass
class Report:
    title: str
    markdown: str
    plain: str
    generated_at: datetime
    sections: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


# ----------------------------------------------------------------- 格式化
def _fmt_price(value: Optional[float], decimals: int) -> str:
    if value is None:
        return "—"
    return f"{value:,.{decimals}f}"


def _fmt_signed(value: Optional[float], decimals: int) -> str:
    if value is None:
        return "—"
    return f"{value:+,.{decimals}f}"


def _marker(direction: int) -> str:
    if direction == UP:
        return "🔴"
    if direction == DOWN:
        return "🟢"
    if direction == FLAT:
        return "⚪"
    return ""


def _fmt_pct(pct: Optional[float], direction: int) -> str:
    if pct is None:
        return "—"
    return f"{_marker(direction)} {pct:+.2f}%".strip()


def _cell_change(row: Row) -> str:
    if not row.has_change:
        return "—"
    return f"{_marker(row.direction)} {row.pct:+.2f}%"


# ----------------------------------------------------------------- 生成
def _md_table(rows: list[Row], price_header: str) -> list[str]:
    lines = [
        f"| 品种 | {price_header} | 涨跌额 | 涨跌幅 |",
        "|:---|---:|---:|---:|",
    ]
    for row in rows:
        q = row.quote
        price = f"{_fmt_price(q.price, q.decimals)} {q.unit}".strip()
        amount = _fmt_signed(row.amount, q.decimals)
        lines.append(f"| {q.name} | {price} | {amount} | {_cell_change(row)} |")
    return lines


def _text_table(rows: list[Row]) -> list[str]:
    """纯文本对齐表（企业微信 markdown 不支持表格）。"""
    if not rows:
        return []
    name_w = max(_disp_width(r.name) for r in rows)
    price_w = max(
        _disp_width(f"{_fmt_price(r.quote.price, r.quote.decimals)} {r.quote.unit}".strip())
        for r in rows
    )
    lines = []
    for row in rows:
        q = row.quote
        price = f"{_fmt_price(q.price, q.decimals)} {q.unit}".strip()
        amount = _fmt_signed(row.amount, q.decimals)
        lines.append(
            f"{_pad(q.name, name_w)}  {_pad(price, price_w)}  "
            f"{amount:>12}  {_cell_change(row)}"
        )
    return lines


def _disp_width(text: str) -> int:
    return sum(2 if ord(ch) > 0x2E80 else 1 for ch in text)


def _pad(text: str, width: int) -> str:
    return text + " " * max(0, width - _disp_width(text))


def build_report(
    overseas_rows: list[Row],
    domestic_rows: list[Row],
    dark_rows: list[Row],
    *,
    generated_at: datetime,
    china_status: MarketStatus,
    us_status: MarketStatus,
    domestic_ref_date: Optional[str] = None,
    domestic_basis_date: Optional[str] = None,
    rollover_notes: list[str] | None = None,
    show_domestic_when_closed: bool = False,
) -> Report:
    rollover_notes = rollover_notes or []
    sections: list[str] = []
    warnings: list[str] = []

    stamp = generated_at.strftime("%Y-%m-%d %H:%M")
    title = f"大宗商品行情日报 {generated_at:%m-%d}"

    md: list[str] = [f"# 📊 大宗商品行情日报", f"**{stamp} 北京时间**", ""]
    txt: list[str] = ["📊 大宗商品行情日报", f"{stamp} 北京时间", ""]

    # ---- 外盘 ----
    if overseas_rows:
        sections.append(LABEL_OVERSEAS)
        md.append(f"## {LABEL_OVERSEAS}")
        md.extend(_md_table(overseas_rows, "最新价"))
        md.append("")
        txt.append(f"【{LABEL_OVERSEAS}】")
        txt.extend(_text_table(overseas_rows))
        txt.append("")
        if us_status.closed:
            note = f"美盘休市（{us_status.name or '周末'}），以下为最近一个交易日收盘口径"
            md.append(f"> {note}")
            md.append("")
            txt.append(f"注：{note}")
            txt.append("")

    # ---- 内盘 ----
    domestic_blocked = china_status.closed and not show_domestic_when_closed
    if domestic_rows and not domestic_blocked:
        sections.append(LABEL_DOMESTIC)
        header = "结算价"
        if domestic_ref_date:
            header = f"结算价（{domestic_ref_date}）"
        md.append(f"## {LABEL_DOMESTIC}")
        if domestic_ref_date and domestic_basis_date:
            md.append(
                f"> 国内 08:30 未开盘，取最近交易日 **{domestic_ref_date}** 结算价，"
                f"对比前一交易日 **{domestic_basis_date}** 结算价"
            )
            txt.append(
                f"注：国内 08:30 未开盘，取 {domestic_ref_date} 结算价 "
                f"对比 {domestic_basis_date} 结算价"
            )
        md.append("")
        md.extend(_md_table(domestic_rows, header))
        md.append("")
        txt.append(f"【{LABEL_DOMESTIC}】")
        txt.extend(_text_table(domestic_rows))
        txt.append("")
    elif domestic_blocked:
        reason = china_status.name or "周末"
        md.append(f"## {LABEL_DOMESTIC}")
        md.append(f"> 中国市场休市（{reason}），内盘行情暂停")
        md.append("")
        txt.append(f"【{LABEL_DOMESTIC}】中国市场休市（{reason}），内盘行情暂停")
        txt.append("")

    # ---- 暗盘 ----
    live_dark = [r for r in dark_rows if r.quote.ok]
    if live_dark:
        sections.append(LABEL_DARK)
        md.append(f"## {LABEL_DARK}")
        md.extend(_md_table(live_dark, "最新价"))
        md.append("")
        txt.append(f"【{LABEL_DARK}】")
        txt.extend(_text_table(live_dark))
        txt.append("")

    # ---- 提示 ----
    if rollover_notes:
        warnings.extend(rollover_notes)
        md.append("## ⚠️ 提示")
        for note in rollover_notes:
            md.append(f"- {note}")
        md.append("")
        txt.append("⚠️ 提示")
        for note in rollover_notes:
            txt.append(f"· {note}")
        txt.append("")

    # 数据缺口（只在有缺口时列出，避免噪音）
    failed = [r.quote for r in (overseas_rows + domestic_rows) if not r.quote.ok]
    if failed:
        names = "、".join(q.name for q in failed)
        md.append(f"> 数据缺口：{names} 本次未取到有效报价")

    md.append("---")
    md.append(FOOTER)
    txt.append(FOOTER)

    return Report(
        title=title,
        markdown="\n".join(md),
        plain="\n".join(txt),
        generated_at=generated_at,
        sections=sections,
        warnings=warnings,
    )
