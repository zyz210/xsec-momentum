#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
backtest_4h_trend_v0.3.py  —  4H 추세추종 + 국면필터 (롱/숏 대칭, 다심볼 대응)
================================================================================
v0.1 대비 변경 (Incremental):
  - 숏 방향 추가 (--direction long|short|both). 숏은 하락추세(close<EMA200)에서
    EMA20으로의 반등 후 재하락을 진입 트리거로 사용 (롱의 정확한 거울).
  - 펀딩: 실제 펀딩 CSV(--funding) 또는 평탄 추정(--funding_est, %/8h) 지원.
    롱은 펀딩을 지불(양수일 때 비용), 숏은 수취(양수일 때 이득)로 모델링.
  - 기대값에 95% 신뢰구간 추가 (요행 여부 판단용).
검증 결과(2020-03~2025-12, BTCUSDT 4H, 무펀딩): 롱+숏 변형A 기대값 +0.131R/363트레이드,
  단 95%CI가 0을 걸침(표본 부족). 롱+숏은 펀딩 0.05%/8h에도 생존(숏이 상쇄).

*** 진짜 데이터를 넣어야 진짜 답이 나온다. 데이터 없이는 결과를 만들지 않는다. ***
사용:  python3 backtest_4h_trend_v0.3.py --ohlcv btc_4h.csv --funding btc_funding.csv --direction both
"""
import argparse, sys
import numpy as np, pandas as pd


class Params:
    EMA_TREND=200; EMA_ENTRY=20; EMA_EXIT=50
    ADX_LEN=14; ADX_MIN=20.0
    ATR_LEN=14; STOP_ATR_MULT=1.5; TP_ATR_MULT=4.0; TRAIL_ATR_MULT=3.0
    ENTRY="momentum"; ROC_LEN=12; ROC_TH=0.03
    RISK_PER_TRADE=0.01; INITIAL_BALANCE=10_000.0
    FEE_ONE_WAY=0.0005; SLIP_ONE_WAY=0.0002
    IS_FRACTION=0.70


def ema(s,n): return s.ewm(span=n,adjust=False).mean()
def wilder(s,n): return s.ewm(alpha=1.0/n,adjust=False).mean()
def true_range(df):
    pc=df.close.shift(1)
    return pd.concat([df.high-df.low,(df.high-pc).abs(),(df.low-pc).abs()],axis=1).max(axis=1)
def atr(df,n): return wilder(true_range(df),n)
def adx(df,n):
    up=df.high.diff(); dn=-df.low.diff()
    pdm=pd.Series(np.where((up>dn)&(up>0),up,0.0),index=df.index)
    ndm=pd.Series(np.where((dn>up)&(dn>0),dn,0.0),index=df.index)
    tr=wilder(true_range(df),n)
    pdi=100*wilder(pdm,n)/tr; ndi=100*wilder(ndm,n)/tr
    dx=100*(pdi-ndi).abs()/(pdi+ndi).replace(0,np.nan)
    return wilder(dx.fillna(0.0),n)


def load_ohlcv(path):
    df=pd.read_csv(path); cols={c.lower():c for c in df.columns}
    req=["timestamp","open","high","low","close","volume"]
    for r in req:
        if r not in cols: sys.exit(f"[에러] OHLCV에 '{r}' 컬럼 없음. 필요: {req}")
    df=df.rename(columns={cols[r]:r for r in req})
    df.timestamp=(pd.to_datetime(df.timestamp,unit='ms',utc=True)
                  if pd.api.types.is_numeric_dtype(df.timestamp)
                  else pd.to_datetime(df.timestamp,utc=True))
    df=df.sort_values('timestamp').reset_index(drop=True)
    for c in ["open","high","low","close","volume"]: df[c]=pd.to_numeric(df[c],errors='coerce')
    return df.dropna(subset=["open","high","low","close"]).reset_index(drop=True)


def load_funding(path):
    if path is None: return None
    f=pd.read_csv(path); cols={c.lower():c for c in f.columns}
    if "timestamp" not in cols or "funding_rate" not in cols: sys.exit("[에러] funding CSV: timestamp,funding_rate 필요")
    f=f.rename(columns={cols["timestamp"]:"timestamp",cols["funding_rate"]:"funding_rate"})
    f.timestamp=(pd.to_datetime(f.timestamp,unit='ms',utc=True)
                 if pd.api.types.is_numeric_dtype(f.timestamp)
                 else pd.to_datetime(f.timestamp,utc=True))
    f.funding_rate=pd.to_numeric(f.funding_rate,errors='coerce')
    return f.dropna().sort_values('timestamp').reset_index(drop=True)


def realized_funding(fdf, t0, t1, qty, price, side):
    """보유기간 펀딩. 롱: 양수=비용(뺌). 숏: 양수=이득(더함). 반환은 손익에 더할 값."""
    if fdf is None: return 0.0, False
    m=(fdf.timestamp>t0)&(fdf.timestamp<=t1)
    s=float(fdf.loc[m,"funding_rate"].sum())*qty*price
    return (-s if side=="long" else +s), True


def backtest(df, fdf, p, direction="both", variant="A", funding_est=0.0):
    d=df.copy()
    d["emaT"]=ema(d.close,p.EMA_TREND); d["emaE"]=ema(d.close,p.EMA_ENTRY)
    d["emaX"]=ema(d.close,p.EMA_EXIT); d["atr"]=atr(d,p.ATR_LEN); d["adx"]=adx(d,p.ADX_LEN)
    d["roc"]=d.close/d.close.shift(p.ROC_LEN)-1
    warm=max(p.EMA_TREND,p.ATR_LEN*3,p.ADX_LEN*3); n=len(d)
    dirs=("long","short") if direction=="both" else (direction,)
    bal=p.INITIAL_BALANCE; eq=[bal]; trades=[]; fmiss=(fdf is None)
    inp=False; side=""; ep=ea=stop=tp=ext=0.0; ei=0; t0=None; qty=risk=0.0
    for i in range(warm,n):
        r=d.iloc[i]
        if not inp:
            pv=d.iloc[i-1]; sig=None
            if p.ENTRY=="momentum":
                L = r.close>r.emaT and r.adx>=p.ADX_MIN and r.roc>p.ROC_TH and r.atr>0
                S = r.close<r.emaT and r.adx>=p.ADX_MIN and r.roc<-p.ROC_TH and r.atr>0
            else:  # pullback
                L = r.close>r.emaT and r.adx>=p.ADX_MIN and pv.low<=pv.emaE and r.close>r.emaE and r.atr>0
                S = r.close<r.emaT and r.adx>=p.ADX_MIN and pv.high>=pv.emaE and r.close<r.emaE and r.atr>0
            if "long" in dirs and L: sig="long"
            elif "short" in dirs and S: sig="short"
            if sig:
                inp=True; side=sig; ep=r.close; ea=r.atr; ei=i; t0=r.timestamp
                if sig=="long": stop=ep-p.STOP_ATR_MULT*ea; tp=ep+p.TP_ATR_MULT*ea; ext=r.high
                else: stop=ep+p.STOP_ATR_MULT*ea; tp=ep-p.TP_ATR_MULT*ea; ext=r.low
                risk=bal*p.RISK_PER_TRADE; qty=risk/abs(ep-stop)
            continue
        xp=None
        if variant=="A":
            if side=="long": xp=stop if r.low<=stop else (tp if r.high>=tp else None)
            else: xp=stop if r.high>=stop else (tp if r.low<=tp else None)
        else:
            if side=="long":
                ext=max(ext,r.high); es=max(stop,ext-p.TRAIL_ATR_MULT*r.atr)
                xp=es if r.low<=es else (r.close if r.close<r.emaX else None)
            else:
                ext=min(ext,r.low); es=min(stop,ext+p.TRAIL_ATR_MULT*r.atr)
                xp=es if r.high>=es else (r.close if r.close>r.emaX else None)
        if xp is None and i==n-1: xp=r.close
        if xp is not None:
            gross=qty*((xp-ep) if side=="long" else (ep-xp))
            notional=qty*ep+qty*xp; fee=notional*p.FEE_ONE_WAY; slip=notional*p.SLIP_ONE_WAY
            if fdf is not None:
                fund_pnl,had=realized_funding(fdf,t0,r.timestamp,qty,ep,side)
            else:
                per=(i-ei)/2.0; s=funding_est*per*qty*ep; fund_pnl=(-s if side=="long" else +s); had=(funding_est!=0.0); fmiss=fmiss and (funding_est==0.0)
            net=gross-fee-slip+fund_pnl; bal+=net; eq.append(bal)
            trades.append(dict(dir=side,ei=ei,t0=t0,t1=r.timestamp,bars=i-ei,net=net,R=net/risk))
            inp=False
    return trades,eq,fmiss


def metrics(trades):
    if not trades: return None
    R=np.array([t["R"] for t in trades]); net=np.array([t["net"] for t in trades])
    se=R.std(ddof=1)/np.sqrt(len(R)) if len(R)>1 else 0
    eqc=np.cumsum(net); peak=np.maximum.accumulate(eqc); dd=(peak-eqc)
    mdd=float((dd/(Params.INITIAL_BALANCE+peak)).max()*100) if len(dd) else 0
    gw=net[net>0].sum(); gl=-net[net<=0].sum()
    return dict(n=len(R),exp=float(R.mean()),ci_lo=float(R.mean()-1.96*se),ci_hi=float(R.mean()+1.96*se),
                wr=float((R>0).mean()*100),pf=(float(gw/gl) if gl>0 else float('inf')),
                net=float(net.sum()),mdd=mdd)


def block(title,m):
    print(f"\n--- {title} ---")
    if not m: print("  트레이드 없음"); return
    print(f"  n={m['n']}  기대값 {m['exp']:+.4f}R  [95%CI {m['ci_lo']:+.3f}~{m['ci_hi']:+.3f}]")
    sig="유의(0 배제)" if m['ci_lo']>0 else "미유의(0 걸침)"
    print(f"  승률 {m['wr']:.1f}%  PF {m['pf']:.3f}  순손익 {m['net']:+,.0f}  MDD {m['mdd']:.1f}%  -> {sig}")


def report(df,fdf,p,direction,funding_est):
    print("="*66); print(f"백테스트 v0.3(모멘텀) — 방향={direction}  펀딩={'실데이터' if fdf is not None else (f'추정 {funding_est*100:.4f}%/8h' if funding_est else '없음(경고)')}")
    print(f"데이터: {df.timestamp.iloc[0]} ~ {df.timestamp.iloc[-1]} ({len(df)}봉)"); print("="*66)
    for v,name in [("A","변형A 고정1:2"),("B","변형B 트레일링")]:
        tr,_,fm=backtest(df,fdf,p,direction,v,funding_est)
        cut=int(len(df)*p.IS_FRACTION)
        print(f"\n{'='*66}\n[{name}]"); block("전체",metrics(tr))
        block("In-Sample",metrics([t for t in tr if t['ei']<cut]))
        block("Out-of-Sample",metrics([t for t in tr if t['ei']>=cut]))
        if fm: print("  [경고] 펀딩 미반영 — 실제 기대값은 이보다 낮을 수 있음")


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--ohlcv",required=True); ap.add_argument("--funding",default=None)
    ap.add_argument("--direction",default="both",choices=["long","short","both"])
    ap.add_argument("--entry",default="momentum",choices=["momentum","pullback"])
    ap.add_argument("--funding_est",type=float,default=0.0,help="평탄 펀딩 추정 %%/8h (예: 0.0001)")
    a=ap.parse_args(); p=Params()
    df=load_ohlcv(a.ohlcv); fdf=load_funding(a.funding); p.ENTRY=a.entry
    if len(df)<max(p.EMA_TREND*2,300): sys.exit(f"[에러] 데이터 부족({len(df)}봉). 3년+ 권장")
    report(df,fdf,p,a.direction,a.funding_est)


if __name__=="__main__": main()
