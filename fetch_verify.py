#!/usr/bin/env python3
"""Fetch long-history 4h OHLCV + funding for BTC/ETH perps to verify v0.3 claims.
Outputs timestamp,open,high,low,close,volume CSVs and timestamp,funding_rate CSVs
in the format backtest_4h_trend_v0.3.py expects."""
import time, sys, os
import ccxt, pandas as pd

OUT = os.path.join(os.path.dirname(__file__), "verify")
os.makedirs(OUT, exist_ok=True)
ex = ccxt.binanceusdm({"enableRateLimit": True, "timeout": 30000})
START = ex.parse8601("2020-01-01T00:00:00Z")

def fetch_ohlcv(sym, tf="4h"):
    tf_ms = ex.parse_timeframe(tf) * 1000
    cur, out = START, []
    while True:
        b = ex.fetch_ohlcv(sym, tf, since=cur, limit=1500)
        if not b: break
        out += b
        if len(b) < 1500: break
        cur = b[-1][0] + tf_ms
        time.sleep(ex.rateLimit/1000)
    seen = {r[0]: r for r in out}
    return [seen[k] for k in sorted(seen)]

def fetch_funding(sym):
    cur, out = START, []
    while True:
        b = ex.fetch_funding_rate_history(sym, since=cur, limit=1000)
        if not b: break
        out += b
        last = b[-1]["timestamp"]
        if len(b) < 1000: break
        cur = last + 1
        time.sleep(ex.rateLimit/1000)
    seen = {r["timestamp"]: r["fundingRate"] for r in out if r.get("fundingRate") is not None}
    return [(k, seen[k]) for k in sorted(seen)]

for sym, tag in [("BTC/USDT", "BTCUSDT"), ("ETH/USDT", "ETHUSDT")]:
    o = fetch_ohlcv(sym)
    df = pd.DataFrame(o, columns=["timestamp","open","high","low","close","volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df.to_csv(os.path.join(OUT, f"{tag}_4h.csv"), index=False)
    f = fetch_funding(sym)
    fdf = pd.DataFrame(f, columns=["timestamp","funding_rate"])
    fdf["timestamp"] = pd.to_datetime(fdf["timestamp"], unit="ms", utc=True)
    fdf.to_csv(os.path.join(OUT, f"{tag}_funding.csv"), index=False)
    print(f"{tag}: {len(df)} bars {df.timestamp.iloc[0].date()}->{df.timestamp.iloc[-1].date()}  "
          f"| funding {len(fdf)} rows  mean {fdf.funding_rate.astype(float).mean()*100:.4f}%/8h", flush=True)
print("done")
