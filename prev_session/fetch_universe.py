#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fetch_universe.py — 단면 모멘텀용 다심볼 4H OHLCV 유니버스 수집기
================================================================================
Binance USDT-M 선물에서 거래대금 상위 N개 심볼의 4H OHLCV를 받아
cross_sectional_momentum.py 가 먹는 폴더(심볼별 {SYMBOL}_240.csv)로 저장한다.

*** Binance API 접속 가능한 '네 컴퓨터'에서 실행 (Claude 샌드박스는 거래소 차단). ***
사전: pip install ccxt pandas
사용: python3 fetch_universe.py --top 50 --years 3 --out ./universe
그다음: python3 cross_sectional_momentum.py --dir ./universe --lookback 30 --rebalance 6 --topk 8
"""
import argparse, os, time, sys
try:
    import ccxt, pandas as pd
except ImportError:
    sys.exit("설치 필요:  pip install ccxt pandas")


def top_symbols(ex, n):
    ex.load_markets()
    t = ex.fetch_tickers()
    perp = [(s, d.get("quoteVolume") or 0) for s, d in t.items()
            if s.endswith("/USDT:USDT") and ex.markets.get(s, {}).get("swap")]
    perp.sort(key=lambda x: x[1], reverse=True)
    return [s for s, _ in perp[:n]]


def fetch_ohlcv_all(ex, symbol, since_ms, tf="4h"):
    out, limit = [], 1500
    tf_ms = ex.parse_timeframe(tf) * 1000
    cur = since_ms
    while True:
        b = ex.fetch_ohlcv(symbol, tf, since=cur, limit=limit)
        if not b: break
        out += b; cur = b[-1][0] + tf_ms
        if len(b) < limit: break
        time.sleep(ex.rateLimit/1000)
    seen, uniq = set(), []
    for r in out:
        if r[0] not in seen: seen.add(r[0]); uniq.append(r)
    return uniq


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=50)
    ap.add_argument("--years", type=float, default=3.0)
    ap.add_argument("--out", default="./universe")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    ex = ccxt.binance({"options": {"defaultType": "future"}, "enableRateLimit": True})
    since = ex.milliseconds() - int(a.years*365*24*3600*1000)
    syms = top_symbols(ex, a.top)
    print(f"거래대금 상위 {len(syms)}개 심볼 수집 시작...")
    ok = 0
    for i, s in enumerate(syms, 1):
        try:
            o = fetch_ohlcv_all(ex, s, since)
            if len(o) < 300:
                print(f"  [{i}/{len(syms)}] {s} 데이터부족({len(o)}) 건너뜀"); continue
            df = pd.DataFrame(o, columns=["timestamp","open","high","low","close","volume"])
            df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
            name = s.replace("/USDT:USDT","USDT").replace("/","")
            df.to_csv(os.path.join(a.out, f"{name}_240.csv"), index=False)
            ok += 1; print(f"  [{i}/{len(syms)}] {name} 저장 ({len(df)}봉)")
        except Exception as e:
            print(f"  [{i}/{len(syms)}] {s} 실패: {str(e)[:50]}")
    print(f"\n완료: {ok}개 심볼 -> {a.out}")
    print(f"다음:  python3 cross_sectional_momentum.py --dir {a.out} --lookback 30 --rebalance 6 --topk 8")


if __name__ == "__main__":
    main()
