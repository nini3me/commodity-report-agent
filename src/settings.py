"""配置加载：把 config/*.yaml 读成结构化对象，供其他模块使用。"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_DIR = ROOT / "config"


def _load_yaml(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"配置文件不存在：{path}")
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


class Config:
    """全局配置（品种表 + 交易日历）。"""

    def __init__(self, config_dir: str | os.PathLike | None = None, root: Path | None = None):
        self.root = Path(root) if root else ROOT
        self.config_dir = Path(config_dir) if config_dir else DEFAULT_CONFIG_DIR
        products = _load_yaml(self.config_dir / "products.yaml")
        self.holidays = _load_yaml(self.config_dir / "holidays.yaml")

        self.runtime: dict[str, Any] = products.get("runtime", {}) or {}
        self.overseas: list[dict] = products.get("overseas", []) or []
        self.domestic: list[dict] = products.get("domestic", []) or []
        self.dark_pool: dict = products.get("dark_pool", {}) or {}
        self.contract_horizon: int = int(products.get("domestic_contract_horizon_months", 15))
        self.domestic_show_when_china_closed: bool = bool(
            products.get("domestic_show_when_china_closed", False)
        )

    # ---------------- 运行时参数 ----------------
    @property
    def tz_offset(self) -> int:
        return int(self.runtime.get("timezone_offset", 8))

    @property
    def include_today(self) -> bool:
        return bool(self.runtime.get("include_today", False))

    @property
    def main_contract_by(self) -> str:
        return str(self.runtime.get("main_contract_by", "open_interest"))

    @property
    def rollover_jump_threshold(self) -> float:
        return float(self.runtime.get("rollover_jump_threshold", 3.0))

    @property
    def state_path(self) -> Path:
        return self.root / str(self.runtime.get("state_file", "state/snapshot.json"))

    @property
    def pushplus_token(self) -> str | None:
        token = os.getenv("PUSHPLUS_TOKEN", "").strip()
        return token or None

    @property
    def pushplus_topic(self) -> str | None:
        topic = os.getenv("PUSHPLUS_TOPIC", "").strip()
        return topic or None

    @property
    def timezone(self):
        from datetime import timedelta, timezone

        return timezone(timedelta(hours=self.tz_offset))
