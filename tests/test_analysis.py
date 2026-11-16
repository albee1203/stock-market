from app.analysis.scoring import ForeignMetrics, calc_streak, foreign_score
from app.analysis.sentiment import LexiconAnalyzer, score_text
from app.analysis.stock_matcher import StockMatcher
from app.config import ScoringConfig

STOCKS = {"2330": "台積電", "2317": "鴻海", "2002": "中鋼", "1216": "統一", "2603": "長榮"}


def test_score_text_direction():
    assert score_text("營收創新高，外資調升目標價")[0] > 0.5
    assert score_text("營收衰退，外資下修目標價")[0] < -0.5
    assert score_text("今日大盤震盪")[0] == 0


def test_longest_match_and_negation():
    s, pos, neg = score_text("利多出盡")
    assert s < 0 and neg == ["利多出盡"] and pos == []
    s, _, neg = score_text("營收不如預期")
    assert s < 0
    s, pos, neg = score_text("並未虧損")  # 否定詞翻轉
    assert s > 0


def test_matcher_names_codes_and_ambiguous():
    m = StockMatcher(STOCKS)
    assert m.find("台積電與鴻海同步走強") == {"2330", "2317"}
    assert m.find("中鋼(2002)調降盤價") == {"2002"}
    assert m.find("兩岸統一議題") == set()  # 模糊名稱需有代號
    assert m.find("統一(1216)營收創新高") == {"1216"}


def test_per_stock_sentence_scoring():
    lex = LexiconAnalyzer(StockMatcher(STOCKS))
    r = lex.analyze("台積電營收創新高", "台積電獲利成長。另外長榮運價崩跌，獲利衰退。")
    assert r.stocks["2330"].score > 0
    assert r.stocks["2603"].score < 0
    assert r.stocks["2330"].in_title and not r.stocks["2603"].in_title


def test_calc_streak():
    assert calc_streak([5, 3, 1, -1, 4]) == 3
    assert calc_streak([-2, -1, 0, -5]) == -2
    assert calc_streak([0, 1]) == 0
    assert calc_streak([]) == 0


def test_foreign_score_uses_amount_not_lots():
    cfg = ScoringConfig()
    cheap = ForeignMetrics("A", "A", "TWSE", 10_000_000, 10.0, 100_000_000, 1, 0, net_amount=1e8, net_ratio=0.1)
    pricey = ForeignMetrics("B", "B", "TWSE", 1_000_000, 1000.0, 10_000_000, 1, 0, net_amount=1e9, net_ratio=0.1)
    assert foreign_score(pricey, cfg) > foreign_score(cheap, cfg)
    sell = ForeignMetrics("C", "C", "TWSE", -1_000_000, 1000.0, 10_000_000, -5, 0, net_amount=-1e9, net_ratio=-0.1)
    assert -1 <= foreign_score(sell, cfg) < -0.8
