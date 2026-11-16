"""Streamlit 介面：streamlit run frontend/streamlit_app.py

直接讀資料庫（與 FastAPI 共用 app.services），不必另外啟動後端也能看畫面。
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import services  # noqa: E402
from app.db import get_session, init_db  # noqa: E402
from app.pipeline import run_daily  # noqa: E402

st.set_page_config(page_title="台股外資＋新聞觀察清單", page_icon="📊", layout="wide")
init_db()


@st.cache_data(ttl=300)
def load(kind: str, d: str, **kw):
    s = get_session()
    try:
        dd = date.fromisoformat(d)
        if kind == "rec":
            return services.get_recommendations(s, dd)
        if kind == "foreign":
            return services.get_foreign_ranking(s, dd, kw["side"], 30)
        if kind == "news":
            return services.get_news(s, dd, kw.get("code"))
        if kind == "stock":
            return services.get_stock_detail(s, kw["code"], 30)
    finally:
        s.close()


s = get_session()
dates = services.list_dates(s)
s.close()

with st.sidebar:
    st.header("⚙️ 設定")
    if st.button("🔄 抓取今日資料並分析", help="約需 1~2 分鐘；15:30 後執行最完整"):
        with st.spinner("抓取外資、股價、新聞中…"):
            sess = get_session()
            try:
                res = run_daily(sess, date.today())
            finally:
                sess.close()
        st.cache_data.clear()
        (st.success if res["ok"] else st.warning)(res.get("message") or f"完成：{res}")
        st.rerun()
    if not dates:
        st.info("資料庫還是空的。\n\n先在終端機執行：\n```\npython -m app.cli demo\n```\n或\n```\npython -m app.cli run\n```")
        st.stop()
    sel = st.selectbox("交易日", dates, index=0)

st.title("📊 台股每日觀察清單")
st.caption(f"資料日期 {sel}｜外資籌碼 × 新聞情緒 綜合評分")

rec = load("rec", sel)
tab1, tab2, tab3, tab4 = st.tabs(["⭐ 觀察清單", "🏦 外資排行", "📰 新聞情緒", "🔍 個股"])


def rec_table(rows: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df["關鍵字"] = df["keywords"].apply(lambda k: "、".join(k))
    return df.rename(columns={
        "rank": "#", "code": "代號", "name": "名稱", "total_score": "綜合", "foreign_score": "外資分",
        "news_score": "新聞分", "streak": "連續天數", "net_lots": "買賣超(張)", "net_amount_e8": "金額(億)",
        "news_count": "新聞數", "reason": "推薦理由",
    })[["#", "代號", "名稱", "綜合", "外資分", "新聞分", "連續天數", "買賣超(張)", "金額(億)", "新聞數", "關鍵字", "推薦理由"]]


score_cfg = {c: st.column_config.ProgressColumn(c, min_value=-1, max_value=1, format="%.2f")
             for c in ("綜合", "外資分", "新聞分")}
score_cfg["推薦理由"] = st.column_config.TextColumn("推薦理由", width="large")


def show_rec(rows: list[dict]) -> None:
    df = rec_table(rows)
    if df.empty:
        st.write("今日無符合條件標的")
    else:
        st.dataframe(df, hide_index=True, column_config=score_cfg, width="stretch")

with tab1:
    st.subheader("📈 建議買入（外資買 + 新聞利多）")
    show_rec(rec["buy"])
    st.subheader("📉 建議賣出／避開（外資賣 + 新聞利空）")
    show_rec(rec["sell"])
    st.warning(rec["disclaimer"])

with tab2:
    side = st.radio("方向", ["buy", "sell"], format_func=lambda x: "買超" if x == "buy" else "賣超", horizontal=True)
    df = pd.DataFrame(load("foreign", sel, side=side))
    if not df.empty:
        df["金額(億)"] = (df["net_amount"] / 1e8).round(2)
        df["佔成交量"] = (df["net_ratio"] * 100).round(1)
        df["旗標"] = df["flags"].apply(lambda f: "、".join(f))
        st.dataframe(df.rename(columns={
            "code": "代號", "name": "名稱", "market": "市場", "net_lots": "買賣超(張)", "streak": "連續天數",
            "close": "收盤", "score": "外資分",
        })[["代號", "名稱", "市場", "收盤", "買賣超(張)", "金額(億)", "佔成交量", "連續天數", "外資分", "旗標"]],
            hide_index=True, width="stretch")

with tab3:
    news = load("news", sel)
    st.write(f"共 {len(news)} 則新聞")
    for a in news:
        s_ = a["sentiment"] or 0
        icon = "🟢" if s_ > 0.1 else "🔴" if s_ < -0.1 else "⚪"
        with st.expander(f"{icon} [{a['source']}] {a['title']}　({s_:+.2f})"):
            st.write(a["summary"])
            if a["bullish_keywords"]:
                st.markdown("**利多：** " + "、".join(a["bullish_keywords"]))
            if a["bearish_keywords"]:
                st.markdown("**利空：** " + "、".join(a["bearish_keywords"]))
            if a["stocks"]:
                st.markdown("**相關個股：** " + "　".join(
                    f"{x['code']} {x['name']}（{x['score']:+.2f}）" for x in a["stocks"]))
            st.caption(f"{a['published_at']}｜分析器：{a['analyzer']}｜[原文]({a['url']})")

with tab4:
    code = st.text_input("股票代號", value=(rec["buy"][0]["code"] if rec["buy"] else "2330"))
    detail = load("stock", sel, code=code) if code else None
    if not detail:
        st.write("查無資料")
    else:
        st.subheader(f"{detail['code']} {detail['name']}（{detail['market']}）")
        h = pd.DataFrame(detail["history"]).set_index("date")
        st.markdown("外資買賣超（張）")
        st.bar_chart(h["net_lots"])
        if h["close"].notna().any():
            st.markdown("收盤價")
            st.line_chart(h["close"])
        st.markdown("相關新聞")
        for a in load("news", sel, code=code):
            st.markdown(f"- [{a['title']}]({a['url']})（{a['sentiment']:+.2f}）")
