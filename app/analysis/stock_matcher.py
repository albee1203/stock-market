"""從新聞文字中找出相關個股（名稱 / 代號）。"""
from __future__ import annotations

import re

# 容易與一般詞彙混淆的兩字公司名：只有在旁邊出現代號時才算命中
AMBIGUOUS_NAMES = {
    "統一", "大同", "中華", "全新", "國產", "新產", "宏全", "華新", "大成", "台灣",
    "中興", "第一", "美德", "精英", "力麗", "合正", "名軒", "大華", "正道", "上品",
}

_CODE_IN_PARENS = re.compile(r"[(（](\d{4})(?:[-.]?TW[O]?)?[)）]")
_CODE_PREFIX = re.compile(r"(?<!\d)(\d{4})(?!\d)\s*[一-鿿]")


class StockMatcher:
    def __init__(self, stocks: dict[str, str]):
        """stocks: code -> name"""
        self.stocks = stocks
        names = [(n, c) for c, n in stocks.items() if len(n) >= 2]
        names.sort(key=lambda x: -len(x[0]))  # 長名稱優先，避免「台積電」被「台積」吃掉之類問題
        self._names = names

    def find(self, text: str) -> set[str]:
        found: set[str] = set()
        for m in _CODE_IN_PARENS.finditer(text):
            if m.group(1) in self.stocks:
                found.add(m.group(1))
        for m in _CODE_PREFIX.finditer(text):
            code = m.group(1)
            if code in self.stocks and text[m.end() - 1:].startswith(self.stocks[code][:1]):
                found.add(code)
        masked = text
        for name, code in self._names:
            if name not in masked:
                continue
            if name in AMBIGUOUS_NAMES and code not in found:
                continue
            found.add(code)
            masked = masked.replace(name, "□" * len(name))
        return found

    def mentions(self, code: str, text: str) -> bool:
        name = self.stocks.get(code, "")
        return (bool(name) and name in text) or code in text
