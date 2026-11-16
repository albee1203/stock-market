"""集中管理設定值。所有參數都可用環境變數或 .env 覆寫。"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
TZ = ZoneInfo("Asia/Taipei")


def _load_dotenv() -> None:
    """極簡 .env 讀取（不額外引入套件）；已存在的環境變數優先。"""
    env_file = ROOT / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


_load_dotenv()


@dataclass(frozen=True)
class ScoringConfig:
    # 外資分數權重 / 綜合權重
    weight_foreign: float = 0.6
    weight_news: float = 0.4
    # 外資金額在 tanh 中的尺度（元）：約 3 億元買超 ≈ 0.76 分
    amount_scale: float = 3e8
    # 沒有股價時，改用張數尺度
    lots_scale: float = 3000
    # 外資買賣超佔當日成交量比例的尺度
    ratio_scale: float = 0.10
    # 「單日大買/大賣」門檻（元）
    big_amount: float = 5e8
    # 「連續買/賣超」門檻（日）
    min_streak: int = 3
    # 單日買/賣超金額排名前 N 名也列入候選
    top_n_by_amount: int = 30
    # 輸出清單長度
    list_size: int = 10


@dataclass(frozen=True)
class RiskConfig:
    """停損停利參數。停損幅度 = clamp(sl_sigma_mult × 近 vol_window 日報酬標準差, sl_min, sl_max)。"""
    vol_window: int = 20
    sl_sigma_mult: float = 2.0
    sl_min: float = 0.04
    sl_max: float = 0.10
    default_sl: float = 0.07       # 歷史資料不足時使用
    reward_ratio: float = 2.0      # 停利幅度 = 停損幅度 × reward_ratio
    hold_days: int = 20            # 清單追蹤最長天數
    near_pct: float = 0.02         # 距停損／停利價 2% 內視為「接近」


@dataclass(frozen=True)
class Settings:
    database_url: str = os.getenv("DATABASE_URL", f"sqlite:///{ROOT / 'data' / 'twstock.db'}")
    anthropic_api_key: str = os.getenv("ANTHROPIC_API_KEY", "")
    anthropic_model: str = os.getenv("ANTHROPIC_MODEL", "claude-opus-5-5")
    api_base_url: str = os.getenv("API_BASE_URL", "http://127.0.0.1:8000")
    # 證交所有頻率限制，連續請求間隔秒數
    request_interval: float = float(os.getenv("REQUEST_INTERVAL", "3"))
    request_timeout: float = float(os.getenv("REQUEST_TIMEOUT", "20"))
    include_etf: bool = os.getenv("INCLUDE_ETF", "0") == "1"
    rss_feeds: dict[str, str] = field(
        default_factory=lambda: {
            # 名稱: RSS URL（可自行增減；抓不到的來源會被略過並記錄警告）
            "經濟日報": os.getenv("RSS_UDN", "https://money.udn.com/rssfeed/news/1001/5590?ch=money"),
            "工商時報": os.getenv("RSS_CTEE", "https://www.ctee.com.tw/rss_web/livenews/stock"),
        }
    )
    scoring: ScoringConfig = field(default_factory=ScoringConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)


settings = Settings()
