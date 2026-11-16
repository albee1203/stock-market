"""以 Claude 分析新聞情緒（選用）。

- 一次送一批新聞（預設 10 則）以節省請求數。
- 用 Structured Outputs（JSON schema）保證回傳格式可解析。
- 只接受資料庫中存在的股票代號，避免模型「幻想」出不存在的代號。
- 任何錯誤（無金鑰、網路、拒答、格式錯）都退回詞典分析，不會讓每日流程中斷。
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from ..config import settings
from .sentiment import ArticleAnalysis, LexiconAnalyzer, StockSentiment
from .stock_matcher import StockMatcher

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """你是台股的資深產業與量化研究員。你會收到一批台灣財經新聞（標題＋摘要），
請判斷每則新聞對「相關個股」股價的短線（1~5 個交易日）影響。

評分原則：
- sentiment 介於 -1（重大利空）到 1（重大利多）；中性或只是盤勢描述給 0 附近。
- 具體、可量化的基本面消息（營收、獲利、接單、目標價調整、財測）權重高於泛泛的市場評論。
- 「利多出盡」「漲多拉回」等屬於利空；「虧損縮小」「庫存去化完成」可屬利多，請依語意判斷而非只看字面。
- 只評估新聞中確實被提及、且出現在候選清單或你能確定代號的台灣上市櫃個股；不確定代號就不要列。
- reason 用繁體中文一句話（30 字內）說明理由。
- bullish_keywords / bearish_keywords 列出新聞中的關鍵利多/利空詞（繁體中文，各最多 5 個）。"""

SCHEMA = {
    "type": "object",
    "properties": {
        "articles": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer"},
                    "overall_sentiment": {"type": "number"},
                    "bullish_keywords": {"type": "array", "items": {"type": "string"}},
                    "bearish_keywords": {"type": "array", "items": {"type": "string"}},
                    "stocks": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "code": {"type": "string"},
                                "sentiment": {"type": "number"},
                                "reason": {"type": "string"},
                            },
                            "required": ["code", "sentiment", "reason"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["index", "overall_sentiment", "bullish_keywords", "bearish_keywords", "stocks"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["articles"],
    "additionalProperties": False,
}


@dataclass
class ArticleInput:
    title: str
    summary: str
    tagged_codes: list[str]


def _clip(x: float) -> float:
    return max(-1.0, min(1.0, float(x)))


class ClaudeAnalyzer:
    name = "claude"

    def __init__(self, matcher: StockMatcher, batch_size: int = 10):
        import anthropic  # 延遲載入：沒用 LLM 時不需要安裝/設定

        self.matcher = matcher
        self.batch_size = batch_size
        self.fallback = LexiconAnalyzer(matcher)
        self.client = anthropic.Anthropic(api_key=settings.anthropic_api_key or None)

    def _prompt(self, batch: list[ArticleInput]) -> str:
        parts = []
        for i, a in enumerate(batch):
            cands = sorted(self.matcher.find(f"{a.title}。{a.summary}") | set(a.tagged_codes))
            cand_txt = "、".join(f"{c} {self.matcher.stocks.get(c, '')}" for c in cands) or "（無）"
            parts.append(f"<article index=\"{i}\">\n標題：{a.title}\n摘要：{a.summary}\n候選個股：{cand_txt}\n</article>")
        return "請分析以下新聞，依 schema 回傳 JSON：\n\n" + "\n\n".join(parts)

    def _call(self, batch: list[ArticleInput]) -> list[ArticleAnalysis]:
        response = self.client.beta.messages.create(
            model=settings.anthropic_model,
            max_tokens=16000,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": self._prompt(batch)}],
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
            # 若主模型因安全分類器拒答，由伺服器自動改用備援模型重跑
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
        if response.stop_reason in ("refusal", "max_tokens"):
            raise RuntimeError(f"Claude 未完成回應：stop_reason={response.stop_reason}")
        text = "".join(b.text for b in response.content if b.type == "text")
        data = json.loads(text)

        results: list[ArticleAnalysis | None] = [None] * len(batch)
        for item in data.get("articles", []):
            idx = item.get("index")
            if not isinstance(idx, int) or not 0 <= idx < len(batch):
                continue
            title = batch[idx].title
            stocks: dict[str, StockSentiment] = {}
            for s in item.get("stocks", []):
                code = str(s.get("code", "")).strip()
                if code not in self.matcher.stocks:  # 過濾不存在的代號
                    continue
                stocks[code] = StockSentiment(
                    score=_clip(s.get("sentiment", 0)),
                    keywords=[f"+{k}" for k in item.get("bullish_keywords", [])[:5]]
                    + [f"-{k}" for k in item.get("bearish_keywords", [])[:5]],
                    reason=str(s.get("reason", ""))[:80],
                    in_title=self.matcher.mentions(code, title),
                )
            results[idx] = ArticleAnalysis(
                analyzer=self.name,
                sentiment=_clip(item.get("overall_sentiment", 0)),
                bullish_keywords=item.get("bullish_keywords", [])[:5],
                bearish_keywords=item.get("bearish_keywords", [])[:5],
                stocks=stocks,
            )
        # 模型漏掉的文章用詞典補
        return [
            r if r is not None else self.fallback.analyze(a.title, a.summary, a.tagged_codes)
            for r, a in zip(results, batch)
        ]

    def analyze_many(self, articles: list[ArticleInput]) -> list[ArticleAnalysis]:
        import anthropic

        out: list[ArticleAnalysis] = []
        for i in range(0, len(articles), self.batch_size):
            batch = articles[i:i + self.batch_size]
            try:
                out.extend(self._call(batch))
            except (anthropic.APIError, RuntimeError, json.JSONDecodeError) as exc:
                log.warning("Claude 分析失敗，此批改用詞典分析：%s", exc)
                out.extend(self.fallback.analyze(a.title, a.summary, a.tagged_codes) for a in batch)
        return out
