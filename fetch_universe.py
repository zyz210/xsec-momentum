#!/usr/bin/env python3
"""
Fetch a multi-symbol crypto universe (Binance USDT perpetuals) for
cross-sectional momentum research.

- Picks the top-N perps by 24h quote volume
- Downloads `years` of history at the given timeframe (default 4h)
- Writes one CSV per symbol into --out, plus a manifest.json

Usage:
    python3 fetch_universe.py --top 50 --years 3 --timeframe 4h --out ./data
"""
import argparse, json, os, sys, time
import ccxt
import pandas as pd

TF_MS = {
    "1h": 3600_000, "2h": 2*3600_000, "4h": 4*3600_000,
    "6h": 6*3600_000, "12h": 12*3600_000, "1d": 24*3600_000,
}

def log(*a):
    print(*a, flush=True)

def pick_universe(ex, top):
    """Top-N USDT-margined linear perps by 24h quote volume."""
    ex.load_markets()
    tickers = ex.fetch_tickers()
    rows = []
    for sym, t in tickers.items():
        m = ex.markets.get(sym)
        if not m: continue
        if not (m.get("swap") and m.get("linear") and m.get("quote") == "USDT" and m.get("active", True)):
            continue
        qv = t.get("quoteVolume")
        if qv is None:
            continue
        rows.append((sym, qv))
    rows.sort(key=lambda r: r[1], reverse=True)
    return [s for s, _ in rows[:top]]

def fetch_symbol(ex, symbol, timeframe, since_ms, tf_ms):
    all_rows = []
    limit = 1500
    cursor = since_ms
    while True:
        batch = ex.fetch_ohlcv(symbol, timeframe=timeframe, since=cursor, limit=limit)
        if not batch:
            break
        all_rows += batch
        last_ts = batch[-1][0]
        if len(batch) < limit:
            break
        nxt = last_ts + tf_ms
        if nxt <= cursor:
            break
        cursor = nxt
        time.sleep(ex.rateLimit / 1000.0)
    # dedupe + sort
    seen = {}
    for r in all_rows:
        seen[r[0]] = r
    rows = [seen[k] for k in sorted(seen)]
    return rows

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=50)
    ap.add_argument("--years", type=float, default=3.0)
    ap.add_argument("--timeframe", default="4h")
    ap.add_argument("--out", default="./data")
    ap.add_argument("--exchange", default="binanceusdm")
    args = ap.parse_args()

    tf_ms = TF_MS[args.timeframe]
    os.makedirs(args.out, exist_ok=True)
    ex = getattr(ccxt, args.exchange)({"enableRateLimit": True, "timeout": 30000})

    now = ex.milliseconds()
    since = now - int(args.years * 365 * 24 * 3600 * 1000)

    log(f"[universe] picking top {args.top} {args.exchange} USDT perps by 24h volume ...")
    universe = pick_universe(ex, args.top)
    log(f"[universe] {len(universe)} symbols: {', '.join(s.split(':')[0] for s in universe)}")

    manifest = {"exchange": args.exchange, "timeframe": args.timeframe,
                "years": args.years, "symbols": [], "generated_ms": now}
    for i, sym in enumerate(universe, 1):
        safe = sym.split(":")[0].replace("/", "")
        path = os.path.join(args.out, f"{safe}.csv")
        try:
            rows = fetch_symbol(ex, sym, args.timeframe, since, tf_ms)
            if not rows:
                log(f"  [{i}/{len(universe)}] {safe}: EMPTY, skip")
                continue
            df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"])
            df["dt"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
            df.to_csv(path, index=False)
            manifest["symbols"].append({"symbol": sym, "file": os.path.basename(path),
                                        "bars": len(df),
                                        "start": str(df["dt"].iloc[0]),
                                        "end": str(df["dt"].iloc[-1])})
            log(f"  [{i}/{len(universe)}] {safe}: {len(df)} bars  {df['dt'].iloc[0].date()} -> {df['dt'].iloc[-1].date()}")
        except Exception as e:
            log(f"  [{i}/{len(universe)}] {safe}: FAIL {type(e).__name__}: {str(e)[:100]}")
        time.sleep(ex.rateLimit / 1000.0)

    with open(os.path.join(args.out, "manifest.json"), "w") as f:
        json.dump(manifest, f, indent=2)
    log(f"[done] wrote {len(manifest['symbols'])} symbols to {args.out}")

if __name__ == "__main__":
    main()
