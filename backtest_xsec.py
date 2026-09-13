#!/usr/bin/env python3
"""
Cross-sectional momentum backtest over a multi-symbol crypto panel.

Loads every <SYM>.csv (ts, open, high, low, close, volume) from --data,
aligns closes on a common 4h grid, and runs a ranked long-only (optionally
long/short) momentum portfolio with periodic rebalancing.

Key mechanic: at each rebalance only assets with a fully-populated lookback
window are eligible, so freshly-listed coins are excluded until they have
enough history (no look-ahead, no survivorship injection).

Usage:
    python3 backtest_xsec.py --data ./data --lookback 84 --skip 6 \
        --rebalance 42 --topk 5 --fee 0.0005
    python3 backtest_xsec.py --data ./data --sweep
"""
import argparse, glob, json, os
import numpy as np
import pandas as pd

BARS_PER_YEAR = 365 * 6  # 4h bars

def load_panel(data_dir, min_bars=200):
    files = sorted(glob.glob(os.path.join(data_dir, "*.csv")))
    closes = {}
    for f in files:
        name = os.path.basename(f)[:-4]
        try:
            df = pd.read_csv(f, usecols=["ts", "close"])
        except Exception:
            continue
        if len(df) < min_bars:
            continue
        s = pd.Series(df["close"].values, index=df["ts"].values, name=name)
        s = s[~s.index.duplicated(keep="last")]
        closes[name] = s
    panel = pd.DataFrame(closes).sort_index()
    panel.index = pd.to_datetime(panel.index, unit="ms", utc=True)
    return panel

def annualize_stats(port_ret):
    port_ret = port_ret.dropna()
    if len(port_ret) < 10:
        return None
    eq = (1 + port_ret).cumprod()
    total = eq.iloc[-1]
    years = len(port_ret) / BARS_PER_YEAR
    cagr = total ** (1 / years) - 1 if years > 0 and total > 0 else float("nan")
    mean = port_ret.mean()
    std = port_ret.std(ddof=1)
    sharpe = (mean / std) * np.sqrt(BARS_PER_YEAR) if std > 0 else float("nan")
    roll_max = eq.cummax()
    mdd = ((eq - roll_max) / roll_max).min()
    n = len(port_ret)
    tstat = mean / (std / np.sqrt(n)) if std > 0 else float("nan")
    hit = (port_ret > 0).mean()
    return {"years": years, "total_return": total - 1, "cagr": cagr,
            "sharpe": sharpe, "mdd": mdd, "tstat": tstat, "hit": hit,
            "n_periods": n, "final_equity": total}

def run_backtest(panel, lookback, skip, rebalance, topk, fee, short=False, bottomk=None):
    """
    lookback: bars used to measure momentum (return over the window)
    skip:     recent bars skipped (short-term reversal filter)
    rebalance: hold period in bars between reweightings
    topk:     number of long positions (equal weight)
    fee:      per-side proportional cost (e.g. 0.0005 = 5 bps)
    """
    prices = panel.values
    T, N = prices.shape
    ret = np.zeros(T)
    weights_prev = np.zeros(N)
    turnover_sum = 0.0
    n_rebals = 0
    start = lookback + skip + 1
    # log return series for portfolio accounting
    logp = np.log(prices)

    # bar-to-bar simple returns
    simple_ret = np.vstack([np.zeros((1, N)), prices[1:] / prices[:-1] - 1])

    cur_w = np.zeros(N)
    for t in range(start, T):
        # rebalance decision at bar t (using info up to close of t-1)
        if (t - start) % rebalance == 0:
            past = prices[t - 1 - skip]
            base = prices[t - 1 - skip - lookback]
            with np.errstate(invalid="ignore", divide="ignore"):
                mom = past / base - 1.0
            # eligibility: both endpoints present and finite and positive prices
            elig = np.isfinite(mom) & np.isfinite(prices[t - 1]) & (base > 0) & (past > 0)
            idx = np.where(elig)[0]
            new_w = np.zeros(N)
            if len(idx) >= max(topk, 2):
                order = idx[np.argsort(mom[idx])]
                longs = order[-topk:]
                new_w[longs] = 1.0 / topk
                if short:
                    bk = bottomk or topk
                    shorts = order[:bk]
                    new_w[shorts] = -1.0 / bk
            turnover = np.abs(new_w - cur_w).sum()
            turnover_sum += turnover
            n_rebals += 1
            ret[t] += -turnover * fee  # transaction cost charged at rebalance bar
            cur_w = new_w
        # portfolio return this bar from current weights (positions held through bar t)
        r = simple_ret[t]
        r = np.where(np.isfinite(r), r, 0.0)
        ret[t] += float(np.dot(cur_w, r))

    port = pd.Series(ret[start:], index=panel.index[start:])
    stats = annualize_stats(port)
    if stats:
        stats["avg_turnover"] = turnover_sum / max(n_rebals, 1)
        stats["n_rebals"] = n_rebals
    return port, stats

def benchmark_equal_weight(panel):
    ret = panel.pct_change()
    # equal weight across available assets each bar
    port = ret.mean(axis=1, skipna=True).fillna(0.0)
    return annualize_stats(port)

def benchmark_btc(panel):
    for c in ["BTCUSDT", "BTC/USDT"]:
        if c in panel.columns:
            return annualize_stats(panel[c].pct_change().dropna())
    return None

def fmt(s):
    if not s: return "n/a"
    return (f"CAGR {s['cagr']*100:6.1f}%  Sharpe {s['sharpe']:5.2f}  "
            f"MDD {s['mdd']*100:6.1f}%  t {s['tstat']:5.2f}  hit {s['hit']*100:4.1f}%  "
            f"tot {s['total_return']*100:7.1f}%")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="./data")
    ap.add_argument("--lookback", type=int, default=84)
    ap.add_argument("--skip", type=int, default=6)
    ap.add_argument("--rebalance", type=int, default=42)
    ap.add_argument("--topk", type=int, default=5)
    ap.add_argument("--fee", type=float, default=0.0005)
    ap.add_argument("--short", action="store_true")
    ap.add_argument("--sweep", action="store_true")
    ap.add_argument("--min-bars", type=int, default=200)
    args = ap.parse_args()

    panel = load_panel(args.data, min_bars=args.min_bars)
    print(f"panel: {panel.shape[1]} symbols x {panel.shape[0]} bars  "
          f"{panel.index[0].date()} -> {panel.index[-1].date()}")
    bh_btc = benchmark_btc(panel)
    bh_ew = benchmark_equal_weight(panel)
    print(f"  BENCH BTC buy&hold : {fmt(bh_btc)}")
    print(f"  BENCH equal-weight : {fmt(bh_ew)}")
    print()

    if args.sweep:
        lookbacks = [42, 84, 126, 168]      # 1w, 2w, 3w, 4w
        rebals = [6, 42]                      # daily, weekly
        topks = [3, 5, 8]
        rows = []
        print(f"{'LB':>4} {'REB':>4} {'K':>3}  {'long-only':>60}")
        for lb in lookbacks:
            for rb in rebals:
                for k in topks:
                    _, s = run_backtest(panel, lb, 6, rb, k, args.fee, short=False)
                    if s:
                        rows.append((lb, rb, k, s))
                        print(f"{lb:>4} {rb:>4} {k:>3}  {fmt(s)}")
        # best by Sharpe
        rows = [r for r in rows if np.isfinite(r[3]["sharpe"])]
        rows.sort(key=lambda r: r[3]["sharpe"], reverse=True)
        print("\nTOP 3 by Sharpe:")
        for lb, rb, k, s in rows[:3]:
            print(f"  LB{lb} REB{rb} K{k}: {fmt(s)}  turnover {s['avg_turnover']:.2f}")
        best = rows[0]
        out = {"best": {"lookback": best[0], "rebalance": best[1], "topk": best[2], **best[3]},
               "bench_btc": bh_btc, "bench_ew": bh_ew}
        json.dump(out, open(os.path.join(args.data, "..", "sweep_result.json"), "w"), indent=2, default=float)
    else:
        port, s = run_backtest(panel, args.lookback, args.skip, args.rebalance,
                               args.topk, args.fee, short=args.short)
        tag = "long/short" if args.short else "long-only"
        print(f"STRATEGY LB{args.lookback} skip{args.skip} REB{args.rebalance} "
              f"K{args.topk} fee{args.fee*1e4:.0f}bps {tag}:")
        print(f"  {fmt(s)}")
        if s:
            print(f"  avg turnover/rebal {s['avg_turnover']:.2f}  rebals {s['n_rebals']}  "
                  f"years {s['years']:.2f}")
        port.to_frame("ret").to_csv(os.path.join(args.data, "..", "strategy_returns.csv"))

if __name__ == "__main__":
    main()
