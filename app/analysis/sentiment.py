"""新聞情緒分析。

- LexiconAnalyzer：內建台股常見利多/利空詞典，零成本、可離線，作為預設與備援。
- 逐句計分：只拿「有提到該個股的句子」來評分該個股，避免一篇新聞提到多檔股票時互相污染。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .stock_matcher import StockMatcher

# 權重 1.0 = 一般，1.5~2.0 = 強烈訊號
BULLISH: dict[str, float] = {
    "營收創新高": 2.0, "營收創同期新高": 1.8, "獲利創新高": 2.0, "EPS創新高": 2.0, "創歷史新高": 1.2,
    "營收成長": 1.2, "營收年增": 1.0, "獲利成長": 1.3, "獲利倍增": 1.8, "轉虧為盈": 1.8, "毛利率提升": 1.3,
    "毛利率創新高": 1.6, "調升目標價": 1.8, "上調目標價": 1.8, "上修目標價": 1.8, "目標價上看": 1.2,
    "上修": 1.0, "調升": 0.8, "優於預期": 1.5, "超乎預期": 1.5, "優於市場預期": 1.5, "財測上修": 1.8,
    "接單暢旺": 1.5, "訂單滿載": 1.6, "急單": 1.0, "大單": 1.2, "拿下訂單": 1.4, "獲大單": 1.5, "打入供應鏈": 1.3,
    "擴產": 1.0, "產能滿載": 1.4, "供不應求": 1.5, "漲價": 1.0, "調漲報價": 1.3, "需求強勁": 1.3,
    "庫藏股": 1.2, "配息創新高": 1.4, "高殖利率": 0.8, "增資": 0.3, "合併綜效": 0.8,
    "法說會報喜": 1.5, "展望樂觀": 1.3, "樂觀看待": 1.0, "看好": 0.8, "利多": 1.0, "買超": 0.6,
    "漲停": 1.2, "大漲": 0.8, "強勢": 0.6, "旺季": 0.6, "復甦": 0.8, "回溫": 0.8, "受惠": 0.9,
    "認證通過": 1.0, "取得認證": 1.0, "獲准": 0.8, "簽約": 0.8, "合作": 0.4,
}
BEARISH: dict[str, float] = {
    "營收衰退": 1.3, "營收年減": 1.0, "營收創新低": 1.8, "獲利衰退": 1.4, "虧損擴大": 1.8, "由盈轉虧": 1.8,
    "轉盈為虧": 1.8, "虧損": 1.2, "毛利率下滑": 1.3, "調降目標價": 1.8, "下修目標價": 1.8, "下調目標價": 1.8,
    "下修": 1.0, "調降": 0.8, "財測下修": 1.8, "低於預期": 1.5, "不如預期": 1.5, "遜於預期": 1.5,
    "砍單": 1.6, "訂單減少": 1.3, "庫存調整": 1.0, "去化": 0.6, "需求疲弱": 1.3, "供過於求": 1.4,
    "跌價": 1.0, "殺價": 1.2, "停工": 1.5, "停產": 1.5, "減產": 1.2, "裁員": 1.2, "火災": 1.4, "斷鏈": 1.2,
    "違約": 1.8, "掏空": 2.0, "下市": 2.0, "全額交割": 2.0, "處分": 0.8, "處置股": 1.0, "注意股": 0.6,
    "調查": 0.8, "起訴": 1.4, "罰款": 1.0, "裁罰": 1.2, "訴訟": 1.0, "專利侵權": 1.0, "財報延遲": 1.5,
    "利空": 1.0, "利多出盡": 1.4, "賣超": 0.6, "跌停": 1.2, "重挫": 1.0, "大跌": 0.8, "崩跌": 1.2,
    "保守看待": 1.0, "展望保守": 1.3, "淡季": 0.6, "衰退": 0.8, "疲弱": 0.8, "警訊": 1.0, "降評": 1.5,
    "下調評等": 1.5, "關稅": 0.5, "制裁": 1.0, "禁令": 1.0, "管制": 0.6,
}
NEGATIONS = ("不", "未", "沒有", "無", "非", "並非", "未能", "難以")

_SENT_SPLIT = re.compile(r"[。！？!?；;\n]+")


@dataclass
class StockSentiment:
    score: float  # -1 ~ 1
    keywords: list[str] = field(default_factory=list)
    reason: str = ""
    in_title: bool = False


@dataclass
class ArticleAnalysis:
    analyzer: str
    sentiment: float
    bullish_keywords: list[str]
    bearish_keywords: list[str]
    stocks: dict[str, StockSentiment]


def _build_terms() -> list[tuple[str, float]]:
    terms = [(k, w) for k, w in BULLISH.items()] + [(k, -w) for k, w in BEARISH.items()]
    return sorted(terms, key=lambda x: -len(x[0]))  # 長詞先比對，「利多出盡」優先於「利多」


_TERMS = _build_terms()


def score_text(text: str) -> tuple[float, list[str], list[str]]:
    """回傳 (分數 -1~1, 利多詞, 利空詞)。"""
    pos_w = neg_w = 0.0
    pos_kw: list[str] = []
    neg_kw: list[str] = []
    masked = text
    for term, weight in _TERMS:
        start = 0
        while (idx := masked.find(term, start)) != -1:
            prefix = masked[max(0, idx - 3):idx]
            w = -weight if any(prefix.endswith(n) for n in NEGATIONS) else weight
            if w > 0:
                pos_w += w
                pos_kw.append(term)
            else:
                neg_w += -w
                neg_kw.append(term)
            masked = masked[:idx] + "□" * len(term) + masked[idx + len(term):]
            start = idx + len(term)
    total = pos_w + neg_w
    if total == 0:
        return 0.0, [], []
    # 方向 (pos-neg)/total，信心隨命中強度提升（總權重 3 以上視為滿信心）
    score = (pos_w - neg_w) / total * min(1.0, total / 3.0)
    return round(score, 4), list(dict.fromkeys(pos_kw)), list(dict.fromkeys(neg_kw))


class LexiconAnalyzer:
    name = "lexicon"

    def __init__(self, matcher: StockMatcher):
        self.matcher = matcher

    def analyze(self, title: str, summary: str, tagged_codes: list[str] | None = None) -> ArticleAnalysis:
        full = f"{title}。{summary}"
        overall, pos_kw, neg_kw = score_text(full)
        codes = self.matcher.find(full) | {c for c in (tagged_codes or []) if c in self.matcher.stocks}
        sentences = [s for s in _SENT_SPLIT.split(full) if s.strip()]
        stocks: dict[str, StockSentiment] = {}
        for code in codes:
            related = [s for s in sentences if self.matcher.mentions(code, s)]
            if related:
                s_score, s_pos, s_neg = score_text("。".join(related))
                # 提到該股的句子沒有情緒詞時，以全文情緒打折代替
                if not s_pos and not s_neg:
                    s_score, s_pos, s_neg = overall * 0.5, pos_kw, neg_kw
            else:  # 只有來源標註、內文沒寫到名稱
                s_score, s_pos, s_neg = overall * 0.5, pos_kw, neg_kw
            stocks[code] = StockSentiment(
                score=round(s_score, 4),
                keywords=[f"+{k}" for k in s_pos] + [f"-{k}" for k in s_neg],
                in_title=self.matcher.mentions(code, title),
            )
        return ArticleAnalysis(self.name, overall, pos_kw, neg_kw, stocks)
