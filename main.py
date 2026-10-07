#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""大宗商品行情日报 Agent —— 入口。

流程：
    读取配置 → 判断中美交易日 → 抓取外盘/内盘/暗盘行情
    → 计算涨跌 → 检测主力换月 → 生成 Markdown 日报 → PushPlus 推送 → 回写状态快照

用法：
    python main.py                     # 正常抓取并推送
    python main.py --dry-run           # 只打印，不推送
    python main.py --no-state          # 不写状态快照
    python main.py --source sina       # 强制用新浪抓外盘（跳过 yfinance）

环境变量：
    PUSHPLUS_TOKEN   必填（--dry-run 时可不填）
    PUSHPLUS_TOPIC   选填，群组编码（用于同时发给指定好友）
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from src import calculator, pushplus, report as report_mod
from src.holiday import TradingCalendar
from src.market_data import get_dark_pool, get_domestic, get_main_contracts, get_overseas
from src.settings import Config


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="大宗商品行情日报 Agent")
    parser.add_argument("--dry-run", action="store_true", help="只打印报告，不推送")
    parser.add_argument("--source", choices=["auto", "yfinance", "sina"], default="auto",
                        help="外盘数据源")
    parser.add_argument("--no-state", action="store_true", help="不写状态快照")
    parser.add_argument("--template", default="markdown",
                        choices=["markdown", "html", "txt"], help="PushPlus 消息模板")
    parser.add_argument("--date", metavar="YYYY-MM-DD",
                        help="模拟运行日期（用于测试节假日/换月逻辑，行情仍取实时数据）")
    parser.add_argument("--no-output", action="store_true", help="不写 output/ 日报存档")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = Config()
    calendar = TradingCalendar(config.holidays)

    now = datetime.now(config.timezone)
    if args.date:
        sim = date.fromisoformat(args.date)
        now = datetime(sim.year, sim.month, sim.day, 8, 30, tzinfo=config.timezone)
        print(f"[info] 模拟运行日期：{sim}")
    today = now.date()

    china = calendar.status("china", today)
    us = calendar.us_closed_session(today)

    print(f"[info] 生成时间：{now:%Y-%m-%d %H:%M} (UTC+{config.tz_offset})")
    print(f"[info] 中国市场：{china.describe()}")
    print(f"[info] 美盘时段：{us.describe()}（参考日 {calendar.us_session_date(today)}）")

    # ---------------- 抓取 ----------------
    print("[info] 抓取外盘…")
    overseas = get_overseas(config.overseas, source=args.source)
    for quote in overseas:
        print(f"       {quote.name:<12} {quote.price} ← {quote.source or quote.note}")

    domestic, _klines = ([], {})
    if china.trading or config.domestic_show_when_china_closed:
        print("[info] 抓取内盘（结算价口径）…")
        domestic, _klines = get_domestic(config.domestic, today, config.include_today)
        for quote in domestic:
            print(f"       {quote.name:<12} {quote.price} (结算日 {quote.price_date})")
    else:
        print("[info] 中国市场休市，按需求文档取消内盘板块")

    dark = []
    if config.dark_pool and _dark_enabled(config, china, us):
        print(f"[info] 抓取暗盘（{config.dark_pool.get('title', '暗盘')}）…")
        dark = get_dark_pool(config.dark_pool.get("items", []))

    main_contracts: dict[str, dict] = {}
    if domestic:
        print("[info] 枚举主力合约…")
        main_contracts = get_main_contracts(
            config.domestic, today, config.contract_horizon, config.main_contract_by
        )
        for pid, info in main_contracts.items():
            print(f"       {pid:<4} 主力 {info['contract'].upper()} "
                  f"持仓 {info['open_interest']:,.0f}")

    # ---------------- 计算 ----------------
    overseas_rows = calculator.build_rows(overseas)
    domestic_rows = calculator.build_rows(domestic)
    dark_rows = calculator.build_rows(dark)

    state = calculator.load_state(config.state_path)
    names = {p["id"]: (p.get("label") or p["name"]) for p in config.domestic}
    previous_contracts = (state.get("main_contracts") or {}) if state else None

    rollover = calculator.detect_rollovers(main_contracts, previous_contracts, names)
    if not previous_contracts:
        rollover = calculator.jump_notes(domestic_rows, config.rollover_jump_threshold, names)

    # ---------------- 生成报告 ----------------
    doc = report_mod.build_report(
        overseas_rows,
        domestic_rows,
        dark_rows,
        generated_at=now,
        china_status=china,
        us_status=us,
        domestic_ref_date=next((r.quote.price_date for r in domestic_rows if r.quote.ok), None),
        domestic_basis_date=next((r.quote.basis_date for r in domestic_rows if r.quote.ok), None),
        rollover_notes=rollover,
        show_domestic_when_closed=config.domestic_show_when_china_closed,
    )

    print("\n" + "=" * 60)
    print(doc.plain)
    print("=" * 60 + "\n")

    # ---------------- 存档 ----------------
    if not args.no_output:
        out_dir = Path(__file__).resolve().parent / "output"
        out_dir.mkdir(parents=True, exist_ok=True)
        archive = out_dir / f"{today.isoformat()}.md"
        archive.write_text(doc.markdown, encoding="utf-8")
        (out_dir / "latest.md").write_text(doc.markdown, encoding="utf-8")
        print(f"[info] 日报已存档 {archive}")

    # ---------------- 推送 ----------------
    push_ok = False
    if args.dry_run:
        print("[info] dry-run：跳过推送")
    else:
        token = config.pushplus_token
        if not token:
            print("[error] 未配置 PUSHPLUS_TOKEN，无法推送", file=sys.stderr)
            return 2
        result = pushplus.send(
            token=token,
            title=doc.title,
            content=doc.markdown,
            template=args.template,
            topic=config.pushplus_topic,
        )
        push_ok = result.ok
        print(f"[{'ok' if result.ok else 'error'}] PushPlus：{result.message}")
        if not result.ok:
            print(f"        {result.raw}", file=sys.stderr)

    # ---------------- 状态快照 ----------------
    if not args.no_state:
        snapshot = calculator.make_snapshot(main_contracts, domestic_rows, now, push_ok)
        calculator.save_state(config.state_path, snapshot)
        print(f"[info] 状态已写入 {config.state_path}")

    return 0 if (push_ok or args.dry_run) else 1


def _dark_enabled(config: Config, china, us) -> bool:
    rule = str(config.dark_pool.get("enabled_when", "us_closed"))
    if rule == "always":
        return True
    if rule == "never":
        return False
    if rule == "china_closed":
        return china.closed
    return us.closed and us.reason == "holiday"


if __name__ == "__main__":
    sys.exit(main())
