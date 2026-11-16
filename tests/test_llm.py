"""以假的 client 測試 Claude 分析器的解析、代號過濾與錯誤退回機制（不呼叫真實 API）。"""
import json
from types import SimpleNamespace

import pytest

pytest.importorskip("anthropic")

from app.analysis.llm import ArticleInput, ClaudeAnalyzer  # noqa: E402
from app.analysis.stock_matcher import StockMatcher  # noqa: E402

M = StockMatcher({"2330": "台積電", "2603": "長榮"})


class FakeMessages:
    def __init__(self, payload, stop_reason="end_turn"):
        self.payload, self.stop_reason, self.calls = payload, stop_reason, []

    def create(self, **kw):
        self.calls.append(kw)
        return SimpleNamespace(stop_reason=self.stop_reason, content=[
            SimpleNamespace(type="thinking", thinking=""),
            SimpleNamespace(type="text", text=json.dumps(self.payload, ensure_ascii=False)),
        ])


def make(payload, stop_reason="end_turn"):
    a = ClaudeAnalyzer(M)
    fake = FakeMessages(payload, stop_reason)
    a.client = SimpleNamespace(beta=SimpleNamespace(messages=fake))
    return a, fake


ARTS = [ArticleInput("台積電營收創新高", "AI 訂單滿載", []), ArticleInput("長榮運價崩跌", "獲利衰退", [])]


def test_parses_and_filters_unknown_codes():
    a, fake = make({"articles": [
        {"index": 0, "overall_sentiment": 0.8, "bullish_keywords": ["營收創新高"], "bearish_keywords": [],
         "stocks": [{"code": "2330", "sentiment": 1.4, "reason": "營收創高"}, {"code": "9999", "sentiment": 1, "reason": "x"}]},
    ]})
    r = a.analyze_many(ARTS)
    assert r[0].analyzer == "claude" and set(r[0].stocks) == {"2330"}
    assert r[0].stocks["2330"].score == 1.0  # 被裁切到 [-1, 1]
    assert r[1].analyzer == "lexicon" and r[1].stocks["2603"].score < 0  # 模型漏掉的用詞典補
    kw = fake.calls[0]
    assert kw["output_config"]["format"]["type"] == "json_schema"


def test_refusal_falls_back_to_lexicon():
    a, _ = make({}, stop_reason="refusal")
    r = a.analyze_many(ARTS)
    assert all(x.analyzer == "lexicon" for x in r)
