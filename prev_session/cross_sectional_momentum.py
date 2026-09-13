#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
cross_sectional_momentum.py — 단면(cross-sectional) 모멘텀 백테스트 엔진
================================================================================
아이디어: 매 리밸런싱마다 유니버스의 모든 심볼을 모멘텀(과거수익)으로 랭킹해서,
상위 그룹을 롱 / 하위 그룹을 숏. "어느 코인이 오를까"가 아니라 "어느 코인이
남들보다 강/약한가"를 베팅 → 시장 전체 방향에 대한 노출이 상쇄돼 시장중립에 가까움.
이것이 시계열 모멘텀보다 역사적으로 강하고, 인간이 용량 때문에 못 하는 엣지다.

*** 유니버스(여러 심볼 CSV)를 넣어야 진짜 답이 나온다. 데이터 없이 결과를 만들지 않는다. ***

입력: 한 폴더 안에 심볼별 4H OHLCV CSV. 파일명 = {SYMBOL}_240.csv
      각 CSV 컬럼: timestamp,open,high,low,close,volume
사용:
  python3 cross_sectional_momentum.py --dir ./universe --lookback 30 --rebalance 6 --topk 3
"""
import argparse, glob, os, sys
import numpy as np, pandas as pd


def load_dir(d):
    files = sorted(glob.glob(os.path.join(d, "*_240.csv")))
    if not files:
        sys.exit(f"[에러] {d} 에 *_240.csv 심볼 파일이 없습니다.")
    closes = {}
    for f in files:
        sym = os.path.basename(f).replace("_240.csv", "")
        df = pd.read_csv(f); cols = {c.lower(): c for c in df.columns}
        if "timestamp" not in cols or "close" not in cols: continue
        df = df.rename(columns={cols["timestamp"]: "timestamp", cols["close"]: "close"})
        ts = (pd.to_datetime(df["timestamp"], unit="ms", utc=True)
              if pd.api.types.is_numeric_dtype(df["timestamp"])
              else pd.to_datetime(df["timestamp"], utc=True))
        s = pd.Series(pd.to_numeric(df["close"], errors="coerce").values, index=ts).sort_index()
        closes[sym] = s
    panel = pd.DataFrame(closes).sort_index()
    return panel


def backtest_cs(panel, lookback, rebalance, topk, cost_oneway=0.0007, risk_per_leg=1.0):
    """
    lookback: 모멘텀 계산 봉수 (예 30봉=5일)
    rebalance: 몇 봉마다 재랭킹 (예 6봉=1일)
    topk: 상위/하위 각각 K개 롱/숏
    반환: 리밸런싱 구간 수익률 시계열
    """
    px = panel.copy()
    mom = px / px.shift(lookback) - 1.0
    idx = px.index
    rets = []           # 구간 수익률
    rts = []            # 타임스탬프
    prev_long, prev_short = set(), set()
    turnover_hist = []
    n = len(idx)
    for i in range(lookback, n - rebalance, rebalance):
        m = mom.iloc[i].dropna()
        # 이번 구간 유효 심볼(현재가+미래가 존재)
        fwd = px.iloc[i + rebalance] / px.iloc[i] - 1.0
        valid = m.index.intersection(fwd.dropna().index)
        m = m[valid]
        if len(m) < max(2, 2 * topk):   # 롱/숏 각 topk 뽑을 만큼은 있어야
            k = max(1, len(m) // 2)
        else:
            k = topk
        if len(m) < 2: continue
        ranked = m.sort_values()
        shorts = set(ranked.index[:k])
        longs = set(ranked.index[-k:])
        # 구간 손익 (동일가중, 시장중립)
        long_r = fwd[list(longs)].mean() if longs else 0.0
        short_r = fwd[list(shorts)].mean() if shorts else 0.0
        gross = risk_per_leg * (long_r - short_r) / 1.0
        # 회전율 기반 비용: 바뀐 포지션 비율만큼 왕복비용
        changed = len(longs.symmetric_difference(prev_long)) + len(shorts.symmetric_difference(prev_short))
        base = (len(longs) + len(shorts)) or 1
        turnover = changed / (2 * base)
        cost = turnover * cost_oneway * 2 * (len(longs) + len(shorts)) / base
        rets.append(gross - cost)
        rts.append(idx[i + rebalance])
        turnover_hist.append(turnover)
        prev_long, prev_short = longs, shorts
    return pd.Series(rets, index=pd.DatetimeIndex(rts)), np.mean(turnover_hist) if turnover_hist else 0


def metrics(r, rebalance):
    if len(r) < 5: 
        print("  구간 부족 — 판정 불가"); return
    bars_per_year = 365 * 24 / 4          # 4H 봉/년
    periods_per_year = bars_per_year / rebalance
    eq = (1 + r).cumprod() * 10000
    peak = eq.cummax(); mdd = ((peak - eq) / peak).max() * 100
    yrs = (r.index[-1] - r.index[0]).days / 365.25
    cagr = ((eq.iloc[-1] / 10000) ** (1 / yrs) - 1) * 100 if yrs > 0 else 0
    sharpe = r.mean() / r.std() * np.sqrt(periods_per_year) if r.std() > 0 else 0
    se = r.std(ddof=1) / np.sqrt(len(r)); t = r.mean() / se
    print(f"  구간수 {len(r)} | 구간평균 {r.mean()*100:+.4f}% | t={t:+.2f} | 연Sharpe {sharpe:.2f}")
    print(f"  CAGR {cagr:+.1f}% | MDD {mdd:.1f}% | 최종 ${eq.iloc[-1]:,.0f}(시작$10k) | {'*유의*' if abs(t)>2 else '미유의'}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True, help="심볼 CSV들이 든 폴더")
    ap.add_argument("--lookback", type=int, default=30, help="모멘텀 봉수 (기본30=5일)")
    ap.add_argument("--rebalance", type=int, default=6, help="재랭킹 주기 봉 (기본6=1일)")
    ap.add_argument("--topk", type=int, default=3, help="상/하위 각 K개")
    a = ap.parse_args()
    panel = load_dir(a.dir)
    print("="*64)
    print(f"단면 모멘텀 | 심볼 {panel.shape[1]}개 | {panel.index[0]} ~ {panel.index[-1]}")
    print(f"lookback={a.lookback}봉 rebalance={a.rebalance}봉 topk={a.topk}")
    print("="*64)
    if panel.shape[1] < 6:
        print("[경고] 심볼이 6개 미만 — 랭킹 엣지 검증엔 부족. 아래는 '메커니즘 작동 확인'일 뿐, 엣지 판정 아님.")
    r, tovr = backtest_cs(panel, a.lookback, a.rebalance, a.topk)
    metrics(r, a.rebalance)
    print(f"  평균 회전율 {tovr*100:.1f}%/리밸런싱")


if __name__ == "__main__":
    main()
