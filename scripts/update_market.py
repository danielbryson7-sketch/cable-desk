#!/usr/bin/env python3
"""Build the static GBP/USD top-down briefing consumed by Cable Desk."""

from __future__ import annotations

import json
import math
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "data" / "market.json"
CHICAGO = ZoneInfo("America/Chicago")
NEW_YORK = ZoneInfo("America/New_York")
UTC = timezone.utc
YAHOO = "https://query1.finance.yahoo.com/v8/finance/chart/GBPUSD=X"
CALENDAR = "https://nfs.faireconomy.media/ff_calendar_thisweek.xml"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; CableDesk/2.0)"}


def fetch(url: str) -> bytes:
    request = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read()


def yahoo(interval: str, period: str) -> list[dict]:
    payload = json.loads(fetch(f"{YAHOO}?interval={interval}&range={period}"))
    result = payload["chart"]["result"][0]
    timestamps = result.get("timestamp", [])
    quote = result["indicators"]["quote"][0]
    rows = []
    for index, stamp in enumerate(timestamps):
        values = {key: quote[key][index] for key in ("open", "high", "low", "close")}
        if any(value is None or not math.isfinite(value) for value in values.values()):
            continue
        rows.append({"t": stamp, **{key[0]: round(value, 6) for key, value in values.items()}})
    return rows


def daily_trading_date(stamp: int) -> date:
    moment = datetime.fromtimestamp(stamp, UTC)
    return (moment + timedelta(days=1)).date() if moment.hour >= 22 else moment.date()


def summarize(rows: list[dict], start: date, end: date) -> dict:
    selected = [row for row in rows if start <= daily_trading_date(row["t"]) <= end]
    if not selected:
        raise RuntimeError(f"No daily prices found from {start} through {end}")
    high = max(row["h"] for row in selected)
    low = min(row["l"] for row in selected)
    return {
        "start": start.isoformat(), "end": end.isoformat(),
        "open": selected[0]["o"], "high": high, "low": low, "close": selected[-1]["c"],
        "midpoint": round((high + low) / 2, 6),
        "rangePips": round((high - low) * 10000, 1),
        "direction": "bullish" if selected[-1]["c"] > selected[0]["o"] else "bearish",
    }


def previous_month(rows: list[dict], now: datetime) -> dict:
    first_this_month = now.date().replace(day=1)
    last_previous_month = first_this_month - timedelta(days=1)
    return summarize(rows, last_previous_month.replace(day=1), last_previous_month)


def previous_week(rows: list[dict], now: datetime) -> dict:
    this_monday = now.date() - timedelta(days=now.weekday())
    return summarize(rows, this_monday - timedelta(days=7), this_monday - timedelta(days=1))


def prior_day(rows: list[dict], now: datetime) -> dict:
    candidates = [(daily_trading_date(row["t"]), row) for row in rows if daily_trading_date(row["t"]) < now.date()]
    day = max(item[0] for item in candidates)
    row = [item[1] for item in candidates if item[0] == day][-1]
    midpoint = round((row["h"] + row["l"]) / 2, 6)
    return {
        "date": day.isoformat(), "start": day.isoformat(), "end": day.isoformat(),
        "o": row["o"], "h": row["h"], "l": row["l"], "c": row["c"],
        "open": row["o"], "high": row["h"], "low": row["l"], "close": row["c"],
        "midpoint": midpoint, "rangePips": round((row["h"] - row["l"]) * 10000, 1),
        "direction": "bullish" if row["c"] > row["o"] else "bearish",
    }


def one_hour_frame(rows: list[dict], now: datetime) -> dict:
    cutoff = now.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
    completed = [row for row in rows if datetime.fromtimestamp(row["t"], UTC) < cutoff]
    row = completed[-1]
    start = datetime.fromtimestamp(row["t"], UTC)
    return {
        "start": start.isoformat(), "end": (start + timedelta(hours=1)).isoformat(),
        "open": row["o"], "high": row["h"], "low": row["l"], "close": row["c"],
        "midpoint": round((row["h"] + row["l"]) / 2, 6),
        "rangePips": round((row["h"] - row["l"]) * 10000, 1),
        "direction": "bullish" if row["c"] > row["o"] else "bearish",
    }


def four_hour_frame(rows: list[dict], now: datetime) -> dict:
    groups: dict[datetime, list[dict]] = {}
    for row in rows:
        moment = datetime.fromtimestamp(row["t"], UTC).astimezone(NEW_YORK)
        block = moment.replace(hour=(moment.hour // 4) * 4, minute=0, second=0, microsecond=0)
        groups.setdefault(block, []).append(row)
    completed = [(start, block_rows) for start, block_rows in groups.items() if start + timedelta(hours=4) <= now.astimezone(NEW_YORK)]
    start, selected = max(completed, key=lambda item: item[0])
    high, low = max(row["h"] for row in selected), min(row["l"] for row in selected)
    return {
        "start": start.isoformat(), "end": (start + timedelta(hours=4)).isoformat(),
        "open": selected[0]["o"], "high": high, "low": low, "close": selected[-1]["c"],
        "midpoint": round((high + low) / 2, 6), "rangePips": round((high - low) * 10000, 1),
        "direction": "bullish" if selected[-1]["c"] > selected[0]["o"] else "bearish",
    }


def parse_calendar() -> list[dict]:
    try:
        root = ET.fromstring(fetch(CALENDAR))
    except urllib.error.HTTPError as error:
        if error.code == 429 and OUTPUT.exists():
            cached = json.loads(OUTPUT.read_text(encoding="utf-8")).get("calendar", [])
            if cached:
                return cached
        raise
    events = []
    for node in root.findall(".//event"):
        item = {child.tag: (child.text or "").strip() for child in node}
        if item.get("country") not in {"GBP", "USD"}:
            continue
        raw_time = item.get("time", "")
        if not raw_time or raw_time.lower() in {"all day", "tentative"}:
            stamp, chicago_stamp = None, item.get("date", "")
        else:
            naive = datetime.strptime(f"{item['date']} {raw_time}", "%m-%d-%Y %I:%M%p")
            stamp_dt = naive.replace(tzinfo=UTC)
            stamp, chicago_stamp = stamp_dt.isoformat(), stamp_dt.astimezone(CHICAGO).isoformat()
        events.append({
            "title": item.get("title", "Scheduled event"), "currency": item.get("country", ""),
            "impact": item.get("impact", "Low"), "forecast": item.get("forecast", ""),
            "previous": item.get("previous", ""), "url": item.get("url", ""),
            "timeUtc": stamp, "timeChicago": chicago_stamp,
        })
    return events


def window_rows(rows: list[dict], start: datetime, end: datetime) -> list[dict]:
    return [row for row in rows if start <= datetime.fromtimestamp(row["t"], UTC) < end]


def session_summary(rows: list[dict], start: datetime, end: datetime, name: str) -> dict:
    selected = window_rows(rows, start.astimezone(UTC), end.astimezone(UTC))
    if not selected:
        return {"name": name, "start": start.isoformat(), "end": end.isoformat(), "complete": False}
    high, low = max(row["h"] for row in selected), min(row["l"] for row in selected)
    return {
        "name": name, "start": start.isoformat(), "end": end.isoformat(),
        "open": selected[0]["o"], "high": high, "low": low,
        "midpoint": round((high + low) / 2, 6), "close": selected[-1]["c"],
        "rangePips": round((high - low) * 10000, 1),
        "complete": datetime.now(UTC) >= end.astimezone(UTC), "count": len(selected),
    }


def current_trade_date(now_ny: datetime) -> date:
    return now_ny.date() + timedelta(days=1) if now_ny.time() >= time(20, 0) else now_ny.date()


def build_sessions(rows: list[dict], now: datetime, bias: dict) -> dict:
    direction = bias["direction"]
    now_ny = now.astimezone(NEW_YORK)
    trade_day = current_trade_date(now_ny)
    prior_date = trade_day - timedelta(days=1)
    asia_start = datetime.combine(prior_date, time(20, 0), NEW_YORK)
    asia_end = datetime.combine(trade_day, time(0, 0), NEW_YORK)
    london_start = datetime.combine(trade_day, time(2, 0), NEW_YORK)
    london_end = datetime.combine(trade_day, time(5, 0), NEW_YORK)
    ny_start = datetime.combine(trade_day, time(7, 0), NEW_YORK)
    ny_end = datetime.combine(trade_day, time(10, 0), NEW_YORK)

    asia = session_summary(rows, asia_start, asia_end, "Asian range")
    london = session_summary(rows, london_start, london_end, "London kill zone")
    new_york = session_summary(rows, ny_start, ny_end, "New York AM")
    midnight = next((row["o"] for row in rows if datetime.fromtimestamp(row["t"], UTC) >= asia_end.astimezone(UTC)), None)
    if "high" not in asia:
        return {"tradeDate": trade_day.isoformat(), "asia": asia, "london": london, "newYork": new_york, "playbook": {"state": "Awaiting Asian range", "steps": []}}

    london_rows = window_rows(rows, london_start.astimezone(UTC), london_end.astimezone(UTC))
    high_sweeps = [row for row in london_rows if row["h"] > asia["high"]]
    low_sweeps = [row for row in london_rows if row["l"] < asia["low"]]
    swept_high, swept_low = bool(high_sweeps), bool(low_sweeps)
    candidates = []
    if high_sweeps: candidates.append((high_sweeps[0]["t"], "Asian high"))
    if low_sweeps: candidates.append((low_sweeps[0]["t"], "Asian low"))
    first_sweep = min(candidates)[1] if candidates else None
    london.update({"sweptAsiaHigh": swept_high, "sweptAsiaLow": swept_low, "firstSweep": first_sweep})

    width = asia["rangePips"]
    if width < 8:
        quality, quality_note = "compressed", "Very narrow range; a news release can dominate the pattern."
    elif width <= 30:
        quality, quality_note = "clean", "Compact accumulation range; suitable for watching a one-sided raid."
    elif width <= 40:
        quality, quality_note = "expanded", "Wide Asian range; require unusually clean confirmation."
    else:
        quality, quality_note = "overextended", "Range exceeds 40 pips; the classic London Judas profile is degraded."
    asia.update({"quality": quality, "qualityNote": quality_note, "midnightOpen": midnight})

    latest_price = rows[-1]["c"]
    if swept_high and swept_low:
        state, verdict = "Both sides raided", "Stand aside: the Asian box has already been cleared on both sides."
    elif direction == "Bullish":
        state, verdict = "Bullish AMD watch", "Prefer an Asian-low raid and reclaim before expansion higher."
    elif direction == "Bearish":
        state, verdict = "Bearish AMD watch", "Prefer an Asian-high raid and rejection before expansion lower."
    elif first_sweep == "Asian low" and latest_price < asia["low"]:
        state, verdict = "Asian-low break holding", "Bearish continuation candidate: price remains below ARL after the first raid."
    elif first_sweep == "Asian low":
        state, verdict = "Asian-low raid reclaimed", "Bullish reversal candidate: ARL has been reclaimed after the sell-side raid."
    elif first_sweep == "Asian high" and latest_price > asia["high"]:
        state, verdict = "Asian-high break holding", "Bullish continuation candidate: price remains above ARH after the first raid."
    elif first_sweep == "Asian high":
        state, verdict = "Asian-high raid rejected", "Bearish reversal candidate: ARH has been rejected after the buy-side raid."
    else:
        state, verdict = "Two-sided observation", "Let London reveal the first raid; trade only a reclaim plus structure shift."

    if direction == "Neutral" and state == "Asian-low break holding":
        expected_raid, opposing_target, action = "ARL retest from below", bias["draw"], "bearish"
        flip = "Flip condition: reclaim ARL and PDL with bullish displacement; then target AREQ and ARH."
    elif direction == "Neutral" and state == "Asian-low raid reclaimed":
        expected_raid, opposing_target, action = "ARL support retest", "Asian high", "bullish"
        flip = "Flip condition: lose ARL again with bearish acceptance; then target lower external sell-side."
    elif direction == "Neutral" and state == "Asian-high break holding":
        expected_raid, opposing_target, action = "ARH support retest", bias["draw"], "bullish"
        flip = "Flip condition: lose ARH with bearish displacement; then target AREQ and ARL."
    elif direction == "Neutral" and state == "Asian-high raid rejected":
        expected_raid, opposing_target, action = "ARH resistance retest", "Asian low", "bearish"
        flip = "Flip condition: reclaim ARH with bullish acceptance; then target higher external buy-side."
    else:
        expected_raid = "Asian low" if direction == "Bullish" else "Asian high" if direction == "Bearish" else "Either edge"
        opposing_target = "Asian high" if direction == "Bullish" else "Asian low" if direction == "Bearish" else "Opposite Asian edge"
        action = "bullish" if direction == "Bullish" else "bearish" if direction == "Bearish" else "directional"
        flip = "Flip condition: the expected raid fails to reclaim and price accepts beyond the opposite side."
    confirmation_context = "away from the rejected edge" if "break holding" in state else "back through the range"
    return {
        "tradeDate": trade_day.isoformat(), "asia": asia, "london": london, "newYork": new_york,
        "playbook": {
            "state": state, "verdict": verdict, "expectedRaid": expected_raid, "firstTarget": opposing_target,
            "steps": [
                f"Liquidity condition: watch {expected_raid}.",
                f"Confirmation: require a {action} 5m displacement and market-structure shift {confirmation_context}.",
                f"Distribution: use {opposing_target} as the first objective, then the higher-timeframe draw.",
                flip,
            ],
            "noTradeIf": "Both Asian edges are swept, price never reclaims the raided edge, or high-impact news is imminent.",
        },
    }


def today_news(events: list[dict], now: datetime) -> tuple[list[dict], str]:
    selected = [event for event in events if event["timeChicago"] and datetime.fromisoformat(event["timeChicago"]).date() == now.date()]
    risk = "high" if any(event["impact"] == "High" for event in selected) else "medium" if any(event["impact"] == "Medium" for event in selected) else "low"
    return selected, risk


def build_top_down(month: dict, week: dict, day: dict, four_hour: dict, one_hour: dict, price: float, events: list[dict], now: datetime) -> dict:
    weights = [
        ("Previous month", month, 5), ("Previous week", week, 4), ("Prior day", day, 3),
        ("Completed 4H", four_hour, 2), ("Completed 1H", one_hour, 1),
    ]
    score = sum(weight if frame["direction"] == "bullish" else -weight for _, frame, weight in weights)
    direction = "Bullish" if score >= 5 else "Bearish" if score <= -5 else "Neutral"
    stack = [{"label": label, "direction": frame["direction"], "weight": weight, "range": f"{frame['low']:.4f}–{frame['high']:.4f}", "close": frame["close"]} for label, frame, weight in weights]
    if direction == "Bullish":
        candidates = [("Prior-day high", day["high"]), ("Previous-week high", week["high"]), ("Previous-month high", month["high"])]
        unswept = [(label, value) for label, value in candidates if value > price]
        draw_label, draw_price = min(unswept or candidates, key=lambda item: abs(item[1] - price))
        location = "Weekly discount supports longs" if price <= week["midpoint"] else "Weekly premium: do not chase longs"
        invalidation, confirmation = day["low"], "Sweep sell-side liquidity, reclaim it, then print bullish 5m displacement"
    elif direction == "Bearish":
        candidates = [("Prior-day low", day["low"]), ("Previous-week low", week["low"]), ("Previous-month low", month["low"])]
        unswept = [(label, value) for label, value in candidates if value < price]
        draw_label, draw_price = min(unswept or candidates, key=lambda item: abs(item[1] - price))
        location = "Weekly premium supports shorts" if price >= week["midpoint"] else "Weekly discount: do not chase shorts"
        invalidation, confirmation = day["high"], "Sweep buy-side liquidity, reject it, then print bearish 5m displacement"
    else:
        candidates = [
            ("Prior-day high", day["high"], "buy"), ("Prior-day low", day["low"], "sell"),
            ("Previous-week high", week["high"], "buy"), ("Previous-week low", week["low"], "sell"),
        ]
        unswept = [item for item in candidates if (item[2] == "buy" and item[1] > price) or (item[2] == "sell" and item[1] < price)]
        draw_label, draw_price, _ = min(unswept or candidates, key=lambda item: abs(item[1] - price))
        location, invalidation = "Higher timeframes disagree", week["midpoint"]
        confirmation = "Wait for one side to be swept; the reclaim and displacement define direction"
    today_events, news_risk = today_news(events, now)
    confidence = "aligned" if len({frame["direction"] for _, frame, _ in weights}) == 1 else "mixed"
    if news_risk in {"high", "medium"}: confidence = "event-risk"
    return {
        "direction": direction, "score": score, "maxScore": 15, "confidence": confidence, "newsRisk": news_risk,
        "draw": draw_label, "drawPrice": draw_price, "invalidation": invalidation, "confirmation": confirmation,
        "location": location, "stack": stack, "todayEventCount": len(today_events),
        "disruptiveEventCount": sum(event["impact"] in {"High", "Medium"} for event in today_events),
        "method": "Monthly → weekly → daily → 4H → 1H narrative; Asia supplies the intraday manipulation map and 5m confirms execution.",
    }


def build_levels(month: dict, week: dict, day: dict, sessions: dict) -> list[dict]:
    levels = [
        {"key": "PMH", "label": "Previous month high", "price": month["high"], "side": "buy", "group": "htf"},
        {"key": "PML", "label": "Previous month low", "price": month["low"], "side": "sell", "group": "htf"},
        {"key": "PWH", "label": "Previous week high", "price": week["high"], "side": "buy", "group": "htf"},
        {"key": "PWL", "label": "Previous week low", "price": week["low"], "side": "sell", "group": "htf"},
        {"key": "PWM", "label": "Previous week midpoint", "price": week["midpoint"], "side": "mid", "group": "htf"},
        {"key": "PDH", "label": "Prior-day high", "price": day["high"], "side": "buy", "group": "both"},
        {"key": "PDL", "label": "Prior-day low", "price": day["low"], "side": "sell", "group": "both"},
    ]
    asia = sessions.get("asia", {})
    if "high" in asia:
        levels.extend([
            {"key": "ARH", "label": "Asian range high", "price": asia["high"], "side": "buy", "group": "session"},
            {"key": "AREQ", "label": "Asian equilibrium", "price": asia["midpoint"], "side": "mid", "group": "session"},
            {"key": "ARL", "label": "Asian range low", "price": asia["low"], "side": "sell", "group": "session"},
        ])
        if asia.get("midnightOpen") is not None:
            levels.append({"key": "MO", "label": "Midnight open", "price": asia["midnightOpen"], "side": "open", "group": "session"})
    return levels


def main() -> None:
    now = datetime.now(UTC).astimezone(CHICAGO)
    daily, hourly, intraday, events = yahoo("1d", "1y"), yahoo("1h", "1mo"), yahoo("5m", "5d"), parse_calendar()
    month, week, day = previous_month(daily, now), previous_week(daily, now), prior_day(daily, now)
    four_hour, one_hour = four_hour_frame(hourly, now), one_hour_frame(hourly, now)
    price = intraday[-1]["c"]
    bias = build_top_down(month, week, day, four_hour, one_hour, price, events, now)
    sessions = build_sessions(intraday, now, bias)
    payload = {
        "meta": {"symbol": "GBP/USD", "updatedAt": now.isoformat(), "timezone": "America/Chicago", "priceSource": "Yahoo Finance indicative GBPUSD=X", "calendarSource": "Forex Factory public weekly calendar", "modelVersion": "2.0 top-down + Asian range"},
        "quote": {"price": price, "asOf": intraday[-1]["t"]},
        "previousMonth": month, "lastWeek": week, "priorDay": day, "fourHour": four_hour, "oneHour": one_hour,
        "bias": bias, "sessions": sessions,
        "levels": build_levels(month, week, day, sessions), "candles": intraday[-576:], "calendar": events,
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {OUTPUT} with {len(payload['candles'])} candles, {len(payload['levels'])} levels, and {len(events)} events")


if __name__ == "__main__":
    main()
