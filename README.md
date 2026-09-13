# xsec-momentum — 크립토 모멘텀 엣지 검증

4H 크립토 선물에서 **자동화 가능한 모멘텀 엣지**가 실재하는지 실데이터로 검증하는 리서치 저장소.
두 개의 독립 엣지(단면 랭킹 / 시계열 ROC)를 실데이터·실펀딩으로 백테스트한다.

> **온라인 Claude 채팅에서 쓰는 법:** 이 저장소는 샌드박스(거래소 접근 차단) 환경에서
> 바로 분석을 이어가도록 **CSV 데이터를 포함**한다. 새 데이터 수집(`fetch_*.py`)은
> 네트워크가 열린 로컬에서만 되지만, 이미 받아둔 `data/`·`verify/`로 모든 백테스트는
> 어디서든 재현 가능하다. `pip install ccxt pandas numpy` 후 아래 명령을 그대로 실행.

## 구조

```
fetch_universe.py      # 다심볼 유니버스 수집기 (Binance USDT-perp top-N, 4H) — 로컬 전용
backtest_xsec.py       # 단면(cross-sectional) 모멘텀 백테스트 엔진 (롱온리/롱숏, 스윕)
fetch_verify.py        # 장기 BTC/ETH 4H + 실펀딩 수집기 — 로컬 전용
data/                  # 단면 유니버스: 50종목 4H 3년 (2023-09~2026-09), ~19만봉
verify/                # 시계열 검증: BTC/ETH 4H 6.7년 (2020~2026) + 실펀딩
prev_session/          # 이전 세션 산출물(참고): 시계열 v0.3 백테스터 + 명세 문서
REPORT.md              # 단면 모멘텀 최종 검증 보고 (생존편향 전/후)
VERIFY_NOTES.md        # 이전 세션 4파일 실데이터 재현·대조 노트
```

## 재현 (데이터는 이미 포함됨)

```bash
pip install ccxt pandas numpy

# 1) 단면 모멘텀 — 파라미터 스윕 (편향 포함 원본)
python3 backtest_xsec.py --data ./data --sweep

# 2) 생존편향 제거 (풀히스토리 종목만) — CAGR 절반으로 붕괴 확인
python3 backtest_xsec.py --data ./data --sweep --min-bars 6500

# 3) 롱숏 마켓뉴트럴 (베타 제거) — 진짜 엣지
python3 backtest_xsec.py --data ./data --min-bars 6500 --lookback 84 --rebalance 6 --topk 5 --short

# 4) 시계열 v0.3 (실펀딩, 6.7년, 2022 약세장 포함)
python3 prev_session/backtest_4h_trend_v0.3.py \
    --ohlcv verify/BTCUSDT_4h.csv --funding verify/BTCUSDT_funding.csv \
    --direction both --entry momentum
```

## 핵심 결론 (요약)

- **두 엣지 다 통계적으로 실재.** 단면 롱숏 t≈3.1~3.5, 시계열 v0.3 변형A BTC/ETH 전체 유의.
- **단, 절대수익은 냉정하게 깎아야 함.** 단면 top-50 유니버스의 CAGR 600%는 ~절반이 생존편향;
  풀히스토리+롱숏으로 정화하면 Sharpe ~1.8.
- **시계열 OOS 견고성은 문서보다 약함** — 구간당 표본이 작아 significance가 흔들림. 다심볼 확대 필요.
- 상세는 `REPORT.md`, `VERIFY_NOTES.md` 참조.

## 데이터 출처·주의

- Binance USDT-M 무기한(perp), ccxt로 수집. 실펀딩 포함.
- 유니버스는 "오늘 기준 거래량 top-N" → **잔존 생존편향 있음**(point-in-time 아님).
- 백테스트 수치는 슬리피지 일부만 반영. 실전 진입 전 포워드/페이퍼 검증 필수.
- 리서치·교육용. 투자 권유 아님.
