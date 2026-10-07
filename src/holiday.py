"""holiday: 中美交易日历判断。

规则
----
交易日 = 周一至周五 且 不在法定节假日列表中。

中国期货市场在「调休上班的周末」同样不开市，因此补班日无需特殊处理，
仅在报告中作标注（is_weekend_workday）。

美盘参考日
----------
报告在北京时间 08:30 生成，此时美东时间为前一日 19:30（冬令时）/ 20:30（夏令时），
因此「当前美盘对应的日历日」= 北京日期 − 1 天。
判断美盘是否因节假日休市，用这个日期去查美国节假日表。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional

MARKETS = ("china", "usa")


@dataclass
class MarketStatus:
    market: str
    day: date
    trading: bool
    reason: str  # trading / weekend / holiday
    name: Optional[str] = None
    weekend_workday: bool = False

    @property
    def closed(self) -> bool:
        return not self.trading

    def describe(self) -> str:
        if self.trading:
            return "正常交易日"
        if self.reason == "holiday":
            return f"休市（{self.name}）"
        return "休市（周末）"


class TradingCalendar:
    def __init__(self, holiday_cfg: dict):
        china = (holiday_cfg.get("china") or {})
        usa = (holiday_cfg.get("usa") or {})
        self._holidays = {
            "china": self._to_date_map(china.get("holidays")),
            "usa": self._to_date_map(usa.get("holidays")),
        }
        self._makeup = {self._d(x) for x in (china.get("makeup_workdays") or [])}
        self._early_close = self._to_date_map(holiday_cfg.get("usa_early_close"))

    # ---------------- 工具 ----------------
    @staticmethod
    def _d(value) -> date:
        if isinstance(value, date):
            return value
        return date.fromisoformat(str(value).strip())

    @classmethod
    def _to_date_map(cls, mapping) -> dict[date, str]:
        out: dict[date, str] = {}
        for key, val in (mapping or {}).items():
            out[cls._d(key)] = str(val)
        return out

    @staticmethod
    def is_weekend(day: date) -> bool:
        return day.weekday() >= 5

    # ---------------- 查询 ----------------
    def holiday_name(self, market: str, day: date) -> Optional[str]:
        return self._holidays.get(market, {}).get(day)

    def is_weekend_workday(self, day: date) -> bool:
        """是否为「调休补班日」（周末上班，但市场仍休市）"""
        return day in self._makeup

    def early_close_name(self, day: date) -> Optional[str]:
        return self._early_close.get(day)

    def is_trading_day(self, market: str, day: date) -> bool:
        if self.is_weekend(day):
            return False
        return self.holiday_name(market, day) is None

    def status(self, market: str, day: date) -> MarketStatus:
        name = self.holiday_name(market, day)
        if name:
            return MarketStatus(market, day, False, "holiday", name,
                                self.is_weekend_workday(day))
        if self.is_weekend(day):
            return MarketStatus(market, day, False, "weekend", None,
                                self.is_weekend_workday(day))
        return MarketStatus(market, day, True, "trading", None)

    def last_trading_day(self, market: str, day: date, include_self: bool = True,
                         max_lookback: int = 40) -> date:
        """向前回溯，找到最近的一个交易日。"""
        cursor = day if include_self else day - timedelta(days=1)
        for _ in range(max_lookback):
            if self.is_trading_day(market, cursor):
                return cursor
            cursor -= timedelta(days=1)
        return cursor

    def previous_trading_day(self, market: str, day: date) -> date:
        """day 之前的一个交易日（不含 day）。"""
        return self.last_trading_day(market, day - timedelta(days=1))

    # ---------------- 报告相关 ----------------
    def us_session_date(self, cn_date: date) -> date:
        """北京日期对应的美盘日历日（-1 天）。"""
        return cn_date - timedelta(days=1)

    def us_closed_session(self, cn_date: date) -> MarketStatus:
        """判断北京时间 cn_date 早上对应的美盘时段是否因节假日休市。"""
        return self.status("usa", self.us_session_date(cn_date))
