# Cable Desk

A public, observation-only GBP/USD top-down and Asian-range dashboard. It combines:

- previous-month direction and liquidity;
- last week's open, high, low, close, midpoint, and range;
- prior-day direction and range;
- completed four-hour and one-hour directional structure;
- the 20:00–00:00 New York Asian range, midpoint, and midnight open;
- London and New York session delivery and Asian-edge sweeps;
- current five-minute indicative candles;
- scheduled GBP and USD economic events; and
- a transparent monthly → weekly → daily directional score with an Asia-led confirmation plan.
- an immutable midnight forecast followed by London, New York, and end-of-day checkpoints.
- a separate, non-voting BTMM lens for M/W and Half Batman structures, Asian stop hunts, HOD/LOD candidates, three-level distance, ADR use, EMA 13/50, the 200 EMA “Mayo” line, a transparent TDI proxy, and railroad-track candles.

The dashboard is context, not an automated trade signal.

BTMM observations are deliberately kept separate from the ICT directional score. The pattern scanner uses published mechanical thresholds so the labels can be backtested instead of assigned by hindsight.

## Refresh locally

```bash
python3 scripts/update_market.py
python3 -m http.server 8000
```

Open `http://localhost:8000`.

## Publish with GitHub Pages

1. Create a public GitHub repository and push this folder to its `main` branch.
2. In **Settings → Pages**, set **Source** to **GitHub Actions**.
3. Run **Refresh and publish Cable Desk** from the repository's Actions tab.

The included workflow checks four New York-time checkpoints each weekday: 00:05 freezes the Asian range and forecast, 05:05 records London, 10:05 records New York AM, and 16:15 grades the day. Paired UTC schedules keep those local times stable through daylight-saving changes. The refresh uses public, no-key endpoints; no repository secrets are required.

## Data notes

- Price data: Yahoo Finance's indicative `GBPUSD=X` chart feed.
- Calendar data: Forex Factory's public weekly XML calendar.
- Calendar times are converted from UTC to `America/Chicago`.
- Indicative quotes can differ from executable broker prices.
