"""
XAUUSD Signal Bot - Streamlit app
Install:  pip install streamlit yfinance pandas numpy feedparser
Run:      streamlit run xauusd_bot.py
Signals only. Not financial advice. No strategy wins every trade.
"""
import os, json, time, urllib.request, urllib.parse
import numpy as np
import pandas as pd
import yfinance as yf
import feedparser
import streamlit as st

TICKERS = ["XAUUSD=X", "GC=F"]  # spot, then gold futures fallback

# ---------- data ----------
@st.cache_data(ttl=60)
def get_data(interval, period):
    for t in TICKERS:
        df = yf.download(t, interval=interval, period=period,
                         progress=False, auto_adjust=True)
        if df is not None and not df.empty:
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            return df.dropna(), t
    return pd.DataFrame(), None

# ---------- indicators ----------
def ema(s, n): return s.ewm(span=n, adjust=False).mean()

def rsi(s, n=14):
    d = s.diff()
    up = d.clip(lower=0).ewm(alpha=1/n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1/n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))

def atr(df, n=14):
    c = df["Close"].shift()
    tr = pd.concat([df["High"] - df["Low"],
                    (df["High"] - c).abs(),
                    (df["Low"] - c).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1/n, adjust=False).mean()

# ---------- strategies: each returns (+1 buy / -1 sell / 0 neutral, reason) ----------
def s_trend(df):
    c = df["Close"]; f, s = ema(c, 20).iloc[-1], ema(c, 50).iloc[-1]
    if c.iloc[-1] > f > s: return 1, "Price > EMA20 > EMA50"
    if c.iloc[-1] < f < s: return -1, "Price < EMA20 < EMA50"
    return 0, "No clean trend"

def s_rsi(df):
    r = rsi(df["Close"]).iloc[-1]
    if r < 30: return 1, f"RSI {r:.0f} oversold"
    if r > 70: return -1, f"RSI {r:.0f} overbought"
    return 0, f"RSI {r:.0f} neutral"

def s_macd(df):
    m = ema(df["Close"], 12) - ema(df["Close"], 26)
    h = m - ema(m, 9)
    if h.iloc[-1] > 0 and h.iloc[-1] > h.iloc[-2]: return 1, "MACD rising above signal"
    if h.iloc[-1] < 0 and h.iloc[-1] < h.iloc[-2]: return -1, "MACD falling below signal"
    return 0, "MACD flat"

def s_boll(df):
    c = df["Close"]; ma, sd = c.rolling(20).mean(), c.rolling(20).std()
    if c.iloc[-1] < (ma - 2 * sd).iloc[-1]: return 1, "Below lower Bollinger band"
    if c.iloc[-1] > (ma + 2 * sd).iloc[-1]: return -1, "Above upper Bollinger band"
    return 0, "Inside bands"

def s_break(df):
    hi, lo = df["High"].rolling(20).max().shift().iloc[-1], df["Low"].rolling(20).min().shift().iloc[-1]
    if df["Close"].iloc[-1] > hi: return 1, "Broke 20-bar high"
    if df["Close"].iloc[-1] < lo: return -1, "Broke 20-bar low"
    return 0, "No breakout"

def s_bigtrend(df):
    c = df["Close"]
    if ema(c, 50).iloc[-1] > ema(c, 200).iloc[-1]: return 1, "EMA50 > EMA200 (bull regime)"
    return -1, "EMA50 < EMA200 (bear regime)"

STRATS = {"EMA Trend": s_trend, "RSI": s_rsi, "MACD": s_macd,
          "Bollinger": s_boll, "Breakout": s_break}

def analyze(df, tf):
    rows, votes = [], []
    for name, fn in STRATS.items():
        v, why = fn(df); votes.append(v); rows.append((tf, name, v, why))
    return float(np.mean(votes)), rows

# ---------- news ----------
BULL = ["rate cut", "dovish", "safe haven", "safe-haven", "weaker dollar", "dollar falls",
        "inflation rises", "geopolitical", "war", "tensions", "central bank buying",
        "gold rises", "gold climbs", "gold jumps", "record high", "yields fall", "etf inflows"]
BEAR = ["rate hike", "hawkish", "stronger dollar", "dollar rises", "dollar surges",
        "yields rise", "yields jump", "gold falls", "gold drops", "gold slips",
        "profit-taking", "risk-on", "ceasefire", "etf outflows", "strong jobs"]

@st.cache_data(ttl=300)
def get_news():
    qs = ["gold price XAUUSD", "gold Federal Reserve dollar yields", "gold geopolitical safe haven"]
    seen, items = set(), []
    for q in qs:
        url = f"https://news.google.com/rss/search?q={q.replace(' ', '+')}+when:1d&hl=en-US&gl=US&ceid=US:en"
        for e in feedparser.parse(url).entries[:12]:
            if e.title not in seen:
                seen.add(e.title)
                t = e.title.lower()
                sc = sum(k in t for k in BULL) - sum(k in t for k in BEAR)
                items.append((e.title, int(np.sign(sc))))
    score = float(np.mean([s for _, s in items])) if items else 0.0
    return score, items

# ---------- app ----------
st.set_page_config(page_title="XAUUSD Bot", page_icon="🥇", layout="centered")
STATE_FILE = "last_signal.json"

def tg_send(token, chat, text):
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = urllib.parse.urlencode({"chat_id": chat, "text": text}).encode()
    try:
        urllib.request.urlopen(url, data=data, timeout=10)
        return True, ""
    except Exception as e:
        return False, str(e)

def load_last():
    try:
        return json.load(open(STATE_FILE)).get("sig", "WAIT")
    except Exception:
        return "WAIT"

def save_last(sig):
    try:
        json.dump({"sig": sig}, open(STATE_FILE, "w"))
    except Exception:
        pass

st.title("🥇 XAUUSD Signal Bot")
with st.sidebar:
    st.header("📲 Telegram alerts")
    tg_token = st.text_input("Bot token", value=os.getenv("TG_TOKEN", ""), type="password")
    tg_chat = st.text_input("Chat ID", value=os.getenv("TG_CHAT_ID", ""))
    alerts_on = st.checkbox("Send alerts on new BUY/SELL", value=True)
    min_conf = st.slider("Min confidence to alert (%)", 35, 90, 40)
    if st.button("Send test message"):
        ok, err = tg_send(tg_token, tg_chat, "✅ XAUUSD bot connected.")
        st.success("Sent!") if ok else st.error(f"Failed: {err}")
    st.header("⏱ Auto-scan")
    auto = st.checkbox("Keep scanning automatically", value=False)
    mins = st.slider("Every (minutes)", 1, 60, 5)
if st.button("🔄 Rescan now"):
    st.cache_data.clear()

frames = {"15m": ("15m", "5d", 0.25), "1h": ("1h", "30d", 0.35), "1d": ("1d", "2y", 0.40)}
tech, all_rows, base = 0.0, [], None
for tf, (iv, per, w) in frames.items():
    df, tk = get_data(iv, per)
    if df.empty or len(df) < 60:
        st.warning(f"No data for {tf}"); continue
    sc, rows = analyze(df, tf)
    if tf == "1d":
        v, why = s_bigtrend(df); rows.append((tf, "Regime", v, why)); sc = (sc * 5 + v) / 6
    tech += sc * w; all_rows += rows
    if tf == "1h": base = df

if base is None:
    st.error("Could not load price data. Check internet connection."); st.stop()

news_score, headlines = get_news()
final = 0.75 * tech + 0.25 * news_score
price, a = float(base["Close"].iloc[-1]), float(atr(base).iloc[-1])

if final >= 0.35:   sig, sl, tp = "BUY", price - 1.5 * a, price + 3 * a
elif final <= -0.35: sig, sl, tp = "SELL", price + 1.5 * a, price - 3 * a
else:               sig, sl, tp = "WAIT", None, None

# ---- Telegram alert (only when the signal changes, to avoid spam) ----
last = load_last()
if sig != last:
    if sig in ("BUY", "SELL") and alerts_on and tg_token and tg_chat and abs(final) * 100 >= min_conf:
        top = [f"{r[1]}({r[0]})" for r in all_rows if r[2] == (1 if sig == "BUY" else -1)][:5]
        msg = (f"🥇 XAUUSD {sig}\nEntry: {price:,.2f}\nSL: {sl:,.2f}\nTP: {tp:,.2f}\n"
               f"Confidence: {abs(final)*100:.0f}%\nNews score: {news_score:+.2f}\n"
               f"Agreeing: {', '.join(top)}\n\nNot financial advice.")
        ok, err = tg_send(tg_token, tg_chat, msg)
        st.toast("Alert sent" if ok else f"Alert failed: {err}")
        if ok: save_last(sig)
    elif sig == "WAIT":
        save_last("WAIT")

c1, c2, c3 = st.columns(3)
c1.metric("Price", f"{price:,.2f}")
c2.metric("Signal", sig)
c3.metric("Confidence", f"{abs(final)*100:.0f}%")
st.caption(f"Tech score {tech:+.2f} | News score {news_score:+.2f} | Combined {final:+.2f} | Source {tk}")

if sl:
    st.success(f"**{sig}** entry ≈ {price:,.2f}  |  SL {sl:,.2f}  |  TP {tp:,.2f}  (RR 1:2)")
    risk = st.number_input("Account balance ($)", value=1000.0)
    pct = st.slider("Risk per trade (%)", 0.25, 2.0, 1.0)
    st.info(f"Risking ${risk*pct/100:,.2f}: lot size = risk ÷ (|entry−SL| × 100 oz per lot) "
            f"= {risk*pct/100/(abs(price-sl)*100):.3f} lots")
else:
    st.warning("Strategies disagree. No trade is a valid position.")

st.subheader("Strategy votes")
st.dataframe(pd.DataFrame(all_rows, columns=["TF", "Strategy", "Vote", "Why"]),
             hide_index=True, use_container_width=True)
st.subheader("News")
for t, s in headlines[:12]:
    st.write(("🟢 " if s > 0 else "🔴 " if s < 0 else "⚪ ") + t)
st.line_chart(base["Close"].tail(120))
st.caption("Signals only, not financial advice. Avoid trading around CPI, NFP and FOMC releases.")

if auto:
    time.sleep(mins * 60)
    st.cache_data.clear()
    st.rerun()
