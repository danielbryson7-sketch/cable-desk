#!/usr/bin/env python3
"""Build the static GBP/USD top-down briefing consumed by Cable Desk."""

from __future__ import annotations

import json
import math
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from copy import deepcopy
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "data" / "market.json"
FORECASTS = ROOT / "data" / "forecasts.json"
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


def trade_day_news(events: list[dict], trade_day: date) -> tuple[list[dict], str]:
    selected = []
    for event in events:
        if not event.get("timeUtc"):
            continue
        if datetime.fromisoformat(event["timeUtc"]).astimezone(NEW_YORK).date() == trade_day:
            selected.append(event)
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


def build_levels(month: dict, week: dict, day: dict, sessions: dict, btmm: dict | None = None) -> list[dict]:
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
    if btmm:
        levels.extend([
            {"key": "EMA13", "label": "BTMM fast EMA", "price": btmm["emas"]["ema13"], "side": "buy", "group": "btmm"},
            {"key": "EMA50", "label": "BTMM 50 EMA", "price": btmm["emas"]["ema50"], "side": "mid", "group": "btmm"},
            {"key": "MAYO", "label": "BTMM 200 EMA", "price": btmm["emas"]["mayo200"], "side": "open", "group": "btmm"},
        ])
    return levels


def ema_series(values: list[float], period: int) -> list[float]:
    if not values:
        return []
    alpha = 2 / (period + 1)
    output = [values[0]]
    for value in values[1:]:
        output.append(value * alpha + output[-1] * (1 - alpha))
    return output


def rsi_series(values: list[float], period: int = 13) -> list[float | None]:
    output: list[float | None] = [None] * len(values)
    if len(values) <= period:
        return output
    gains, losses = [], []
    for index in range(1, period + 1):
        change = values[index] - values[index - 1]
        gains.append(max(change, 0))
        losses.append(max(-change, 0))
    average_gain, average_loss = sum(gains) / period, sum(losses) / period
    output[period] = 100 if average_loss == 0 else 100 - 100 / (1 + average_gain / average_loss)
    for index in range(period + 1, len(values)):
        change = values[index] - values[index - 1]
        average_gain = (average_gain * (period - 1) + max(change, 0)) / period
        average_loss = (average_loss * (period - 1) + max(-change, 0)) / period
        output[index] = 100 if average_loss == 0 else 100 - 100 / (1 + average_gain / average_loss)
    return output


def swing_points(rows: list[dict], radius: int = 2) -> tuple[list[tuple[int, dict]], list[tuple[int, dict]]]:
    highs, lows = [], []
    for index in range(radius, len(rows) - radius):
        sample = rows[index - radius:index + radius + 1]
        if rows[index]["h"] == max(row["h"] for row in sample):
            highs.append((index, rows[index]))
        if rows[index]["l"] == min(row["l"] for row in sample):
            lows.append((index, rows[index]))
    return highs, lows


def reversal_pattern(rows: list[dict]) -> dict:
    sample = rows[-180:]
    highs, lows = swing_points(sample)
    candidates = []
    for name, points, price_key, direction in (("M", highs, "h", "bearish"), ("W", lows, "l", "bullish")):
        for (first_index, first), (second_index, second) in zip(points, points[1:]):
            spacing = second_index - first_index
            if not 6 <= spacing <= 48:
                continue
            difference = (second[price_key] - first[price_key]) * 10000
            absolute_difference = abs(difference)
            between = sample[first_index:second_index + 1]
            apex = min(row["l"] for row in between) if name == "M" else max(row["h"] for row in between)
            excursion = (min(first[price_key], second[price_key]) - apex) * 10000 if name == "M" else (apex - max(first[price_key], second[price_key])) * 10000
            if excursion < 5:
                continue
            after = sample[second_index + 1:]
            shifted = any(row["c"] < apex for row in after) if name == "M" else any(row["c"] > apex for row in after)
            if absolute_difference <= 4:
                pattern_name = f"{name} pattern"
            elif 5 <= absolute_difference <= 12 and ((name == "M" and difference < 0) or (name == "W" and difference > 0)):
                pattern_name = "Half Batman"
            else:
                continue
            candidates.append({
                "name": pattern_name, "shape": name, "direction": direction,
                "status": "confirmed" if shifted else "forming",
                "firstExtreme": first[price_key], "secondExtreme": second[price_key],
                "apex": apex, "gapPips": round(absolute_difference, 1), "at": second["t"],
                "rule": "Two swing extremes 6–48 candles apart; Half Batman misses the first extreme by 5–12 pips; confirmation requires an apex close.",
            })
    if not candidates:
        return {"name": "No qualified M/W", "shape": None, "direction": "neutral", "status": "none", "rule": "No recent structure passed the fixed swing, spacing, distance, and apex rules."}
    return max(candidates, key=lambda item: item["at"])


def railroad_tracks(rows: list[dict]) -> dict:
    if len(rows) < 22:
        return {"active": False, "direction": "neutral"}
    first, second = rows[-2], rows[-1]
    bodies = [abs(row["c"] - row["o"]) for row in rows[-22:-2]]
    median = sorted(bodies)[len(bodies) // 2] or .00001
    first_body, second_body = abs(first["c"] - first["o"]), abs(second["c"] - second["o"])
    opposite = (first["c"] > first["o"]) != (second["c"] > second["o"])
    balanced = .6 <= second_body / (first_body or .00001) <= 1.67
    active = opposite and balanced and min(first_body, second_body) >= median * 1.5
    return {
        "active": active,
        "direction": "bullish" if active and second["c"] > second["o"] else "bearish" if active else "neutral",
        "note": "Two opposing bodies, each at least 1.5× the recent median and within a 0.6–1.67 size ratio." if active else "No qualified two-candle railroad-track reversal.",
    }


def average_daily_range(rows: list[dict], now: datetime, periods: int = 5) -> float:
    completed = [row for row in rows if daily_trading_date(row["t"]) < now.astimezone(NEW_YORK).date()]
    selected = completed[-periods:]
    return round(sum((row["h"] - row["l"]) * 10000 for row in selected) / len(selected), 1) if selected else 0


def build_btmm(daily: list[dict], rows: list[dict], sessions: dict, now: datetime) -> dict:
    closes = [row["c"] for row in rows]
    ema13_values, ema50_values, ema200_values = ema_series(closes, 13), ema_series(closes, 50), ema_series(closes, 200)
    ema13, ema50, mayo = ema13_values[-1], ema50_values[-1], ema200_values[-1]
    price = closes[-1]
    mayo_distance = round((price - mayo) * 10000, 1)
    recent_mayo_touch = any(row["l"] <= ema <= row["h"] for row, ema in zip(rows[-12:], ema200_values[-12:]))
    rsi_values = rsi_series(closes, 13)
    valid_rsi = [value for value in rsi_values if value is not None]
    rsi = valid_rsi[-1] if valid_rsi else 50
    signal = sum(valid_rsi[-2:]) / min(2, len(valid_rsi)) if valid_rsi else 50
    tdi_state = "bullish" if rsi > signal and rsi > 50 else "bearish" if rsi < signal and rsi < 50 else "mixed"
    adr = average_daily_range(daily, now)
    trade_day = date.fromisoformat(sessions["tradeDate"])
    day_start = datetime.combine(trade_day, time(0, 0), NEW_YORK)
    today_rows = window_rows(rows, day_start.astimezone(UTC), now.astimezone(UTC) + timedelta(minutes=1))
    day_range = round((max(row["h"] for row in today_rows) - min(row["l"] for row in today_rows)) * 10000, 1) if today_rows else 0
    asia = sessions.get("asia", {})
    level_label, level_direction, distance = "Level 0 · accumulation", "inside", 0.0
    if asia.get("high") is not None:
        if price > asia["high"]:
            distance = round((price - asia["high"]) * 10000, 1)
            level_label, level_direction = f"Level {min(3, int(distance // 25) + 1)} rise", "above"
        elif price < asia["low"]:
            distance = round((asia["low"] - price) * 10000, 1)
            level_label, level_direction = f"Level {min(3, int(distance // 25) + 1)} drop", "below"
    london = sessions.get("london", {})
    if london.get("sweptAsiaHigh") and price < asia.get("high", -math.inf):
        hod_lod = "HOD candidate · high swept and rejected"
    elif london.get("sweptAsiaLow") and price > asia.get("low", math.inf):
        hod_lod = "LOD candidate · low swept and reclaimed"
    else:
        hod_lod = "Unconfirmed · no completed rejection"
    pattern = reversal_pattern(rows)
    expected_pattern = "W / Half Batman below ARL" if sessions.get("playbook", {}).get("expectedRaid") == "Asian low" else "M / Half Batman above ARH" if sessions.get("playbook", {}).get("expectedRaid") == "Asian high" else "M or W after the first stop hunt"
    return {
        "separateMethod": "Steve Mauro BTMM observation layer; it does not alter the ICT score.",
        "expectedPattern": expected_pattern,
        "pattern": pattern,
        "asiaStopHunt": london.get("firstSweep") or "None recorded",
        "hodLod": hod_lod,
        "levelCount": {"label": level_label, "direction": level_direction, "distancePips": distance, "bandPips": 25, "note": "Mechanical observation bands, not an assertion that price must reverse at Level 3."},
        "adr": {"averagePips": adr, "usedPips": day_range, "usedPercent": round(day_range / adr * 100, 1) if adr else None},
        "emas": {"ema13": round(ema13, 6), "ema50": round(ema50, 6), "mayo200": round(mayo, 6), "alignment": "bullish" if ema13 > ema50 else "bearish", "mayoDistancePips": mayo_distance, "recentMayoTouch": recent_mayo_touch},
        "tdiProxy": {"rsi13": round(rsi, 1), "signal2": round(signal, 1), "state": tdi_state, "note": "Transparent RSI-13 / 2-period signal proxy; not a proprietary TDI feed."},
        "railroadTracks": railroad_tracks(rows),
    }


def price_relation(price: float, asia: dict) -> str:
    if price > asia["high"]:
        return "above the Asian high"
    if price < asia["low"]:
        return "below the Asian low"
    if price >= asia["midpoint"]:
        return "inside the upper half of Asia"
    return "inside the lower half of Asia"


def phase_story(rows: list[dict], start: datetime, end: datetime, now: datetime, asia: dict, title: str, label: str) -> dict | None:
    actual_end = min(end, now.astimezone(NEW_YORK))
    if actual_end <= start:
        return None
    selected = window_rows(rows, start.astimezone(UTC), actual_end.astimezone(UTC) + timedelta(minutes=1))
    if not selected:
        return None
    opening, closing = selected[0]["o"], selected[-1]["c"]
    high, low = max(row["h"] for row in selected), min(row["l"] for row in selected)
    move = round((closing - opening) * 10000, 1)
    swept = []
    if title != "Asian range" and high > asia["high"]:
        swept.append("Asian high")
    if title != "Asian range" and low < asia["low"]:
        swept.append("Asian low")
    direction = "rose" if move > 0 else "fell" if move < 0 else "finished flat"
    sweep_text = f" It traded through {' and '.join(swept)}." if swept else " It did not clear an Asian edge."
    return {
        "title": title, "time": label,
        "status": "complete" if now.astimezone(NEW_YORK) >= end else "live",
        "open": opening, "high": high, "low": low, "close": closing,
        "rangePips": round((high - low) * 10000, 1), "movePips": move,
        "text": f"Price {direction} {abs(move):.1f} pips from {opening:.4f} to {closing:.4f}; the phase covered {round((high-low)*10000,1):.1f} pips.{sweep_text} It ended {price_relation(closing, asia)}.",
    }


def build_day_story(rows: list[dict], sessions: dict, bias: dict, btmm: dict, now: datetime) -> dict:
    asia = sessions.get("asia", {})
    if asia.get("high") is None:
        return {"status": "waiting", "headline": "Waiting for the Asian range", "summary": "The rolling account begins when the 20:00 New York candle opens.", "timeline": []}
    now_ny = now.astimezone(NEW_YORK)
    trade_day = date.fromisoformat(sessions["tradeDate"])
    asia_start = datetime.fromisoformat(asia["start"])
    midnight = datetime.combine(trade_day, time(0, 0), NEW_YORK)
    london_start, london_end = datetime.combine(trade_day, time(2, 0), NEW_YORK), datetime.combine(trade_day, time(5, 0), NEW_YORK)
    ny_start, ny_end = datetime.combine(trade_day, time(7, 0), NEW_YORK), datetime.combine(trade_day, time(10, 0), NEW_YORK)
    close_time = datetime.combine(trade_day, time(16, 0), NEW_YORK)
    phases = [
        (asia_start, midnight, "Asian range", "20:00–00:00 NY"),
        (midnight, london_start, "Midnight handoff", "00:00–02:00 NY"),
        (london_start, london_end, "London window", "02:00–05:00 NY"),
        (london_end, ny_start, "Post-London handoff", "05:00–07:00 NY"),
        (ny_start, ny_end, "New York AM", "07:00–10:00 NY"),
        (ny_end, close_time, "After New York AM", "10:00–16:00 NY"),
    ]
    timeline = [phase for start, end, title, label in phases if (phase := phase_story(rows, start, end, now, asia, title, label))]
    available = window_rows(rows, asia_start.astimezone(UTC), now_ny.astimezone(UTC) + timedelta(minutes=1))
    midnight_rows = window_rows(rows, midnight.astimezone(UTC), now_ny.astimezone(UTC) + timedelta(minutes=1))
    latest = rows[-1]["c"]
    start_price = available[0]["o"] if available else asia["open"]
    midnight_open = asia.get("midnightOpen") or (midnight_rows[0]["o"] if midnight_rows else latest)
    since_asia = round((latest - start_price) * 10000, 1)
    since_midnight = round((latest - midnight_open) * 10000, 1)
    high_row = max(available, key=lambda row: row["h"]) if available else None
    low_row = min(available, key=lambda row: row["l"]) if available else None
    relation = price_relation(latest, asia)
    headline = "Sell-side delivery is holding below Asia" if latest < asia["low"] else "Buy-side delivery is holding above Asia" if latest > asia["high"] else "Price is rotating back inside Asia"
    sign_asia, sign_midnight = "+" if since_asia >= 0 else "", "+" if since_midnight >= 0 else ""
    summary = f"From the Asian open, GBP/USD is {sign_asia}{since_asia:.1f} pips. From the midnight open, it is {sign_midnight}{since_midnight:.1f} pips and currently sits {relation}. The live ICT reading is {bias['direction'].lower()}; the BTMM scanner shows {btmm['pattern']['name'].lower()} ({btmm['pattern']['status']})."
    return {
        "status": "live", "headline": headline, "summary": summary,
        "asOf": datetime.fromtimestamp(rows[-1]["t"], UTC).astimezone(NEW_YORK).isoformat(),
        "metrics": {
            "sinceAsiaPips": since_asia, "sinceMidnightPips": since_midnight,
            "high": high_row["h"] if high_row else None,
            "highAt": datetime.fromtimestamp(high_row["t"], UTC).astimezone(NEW_YORK).isoformat() if high_row else None,
            "low": low_row["l"] if low_row else None,
            "lowAt": datetime.fromtimestamp(low_row["t"], UTC).astimezone(NEW_YORK).isoformat() if low_row else None,
            "currentRelation": relation,
        },
        "timeline": timeline,
        "method": "Deterministic five-minute candle summary from 20:00 New York through the latest available candle; no AI generation is used during refreshes.",
    }


def load_forecasts() -> dict:
    if not FORECASTS.exists():
        return {"modelVersion": "1.0", "records": {}}
    try:
        saved = json.loads(FORECASTS.read_text(encoding="utf-8"))
        if not isinstance(saved.get("records"), dict):
            raise ValueError("forecast records must be an object")
        return saved
    except (json.JSONDecodeError, OSError, ValueError):
        return {"modelVersion": "1.0", "records": {}}


def scheduled_checkpoint(now: datetime) -> str | None:
    """Return the New York checkpoint represented by this scheduled run."""
    local = now.astimezone(NEW_YORK)
    if local.minute < 10:
        return None
    hour = local.hour
    return {0: "midnight", 5: "london", 10: "newYork", 16: "final"}.get(hour)


def event_digest(events: list[dict], trade_day: date) -> dict:
    selected, risk = trade_day_news(events, trade_day)
    disruptive = [event for event in selected if event["impact"] in {"High", "Medium"}]
    return {
        "risk": risk,
        "eventCount": len(selected),
        "disruptiveCount": len(disruptive),
        "events": [{"title": item["title"], "currency": item["currency"], "impact": item["impact"], "timeUtc": item["timeUtc"]} for item in disruptive],
    }


def frozen_prediction(bias: dict, sessions: dict) -> dict:
    asia = sessions["asia"]
    direction = bias["direction"]
    if direction == "Bullish":
        expected_raid = "Asian low"
        target_label = bias["draw"] if bias["drawPrice"] > asia["midnightOpen"] else "Asian high"
        target_price = bias["drawPrice"] if bias["drawPrice"] > asia["midnightOpen"] else asia["high"]
        invalidation_label, invalidation_price = "Top-down sell-side invalidation", bias["invalidation"]
    elif direction == "Bearish":
        expected_raid = "Asian high"
        target_label = bias["draw"] if bias["drawPrice"] < asia["midnightOpen"] else "Asian low"
        target_price = bias["drawPrice"] if bias["drawPrice"] < asia["midnightOpen"] else asia["low"]
        invalidation_label, invalidation_price = "Top-down buy-side invalidation", bias["invalidation"]
    else:
        expected_raid = "Either edge"
        target_label, target_price = "No directional target", None
        invalidation_label, invalidation_price = "Wait for London confirmation", None
    return {
        "direction": direction,
        "score": bias["score"],
        "maxScore": bias["maxScore"],
        "confidence": bias["confidence"],
        "expectedFirstRaid": expected_raid,
        "targetLabel": target_label,
        "targetPrice": target_price,
        "invalidationLabel": invalidation_label,
        "invalidationPrice": invalidation_price,
        "confirmation": bias["confirmation"],
        "reason": f"Frozen top-down score {bias['score']:+d}/15; {bias['location'].lower()}.",
    }


def compact_session(session: dict) -> dict:
    keys = ("start", "end", "open", "high", "low", "close", "rangePips", "firstSweep", "sweptAsiaHigh", "sweptAsiaLow")
    return {key: session[key] for key in keys if key in session}


def first_level_touch(rows: list[dict], start: datetime, end: datetime, asia: dict) -> tuple[str | None, int | None]:
    for row in window_rows(rows, start.astimezone(UTC), end.astimezone(UTC)):
        hit_high, hit_low = row["h"] > asia["high"], row["l"] < asia["low"]
        if hit_high and hit_low:
            return "Both in one candle", row["t"]
        if hit_high:
            return "Asian high", row["t"]
        if hit_low:
            return "Asian low", row["t"]
    return None, None


def first_target_touch(rows: list[dict], start: datetime, end: datetime, price: float | None, direction: str) -> int | None:
    if price is None:
        return None
    for row in window_rows(rows, start.astimezone(UTC), end.astimezone(UTC)):
        if (direction == "Bullish" and row["h"] >= price) or (direction == "Bearish" and row["l"] <= price):
            return row["t"]
    return None


def grade_forecast(record: dict, rows: list[dict], trade_day: date) -> dict:
    forecast, asia = record["forecast"], record["asianRange"]
    start = datetime.combine(trade_day, time(0, 0), NEW_YORK)
    end = datetime.combine(trade_day, time(16, 0), NEW_YORK)
    selected = window_rows(rows, start.astimezone(UTC), end.astimezone(UTC))
    if not selected:
        return {"grade": "unresolved", "note": "No post-midnight candles were available."}
    first_raid, first_raid_at = first_level_touch(rows, start, end, asia)
    direction = forecast["direction"]
    final_close = selected[-1]["c"]
    if direction == "Neutral":
        return {
            "grade": "no-trade", "directionCorrect": None, "expectedRaidCorrect": None,
            "firstRaid": first_raid, "firstRaidAt": first_raid_at, "finalClose": final_close,
            "note": "The frozen model made no directional commitment.",
        }
    direction_correct = final_close > asia["midnightOpen"] if direction == "Bullish" else final_close < asia["midnightOpen"]
    expected_raid_correct = first_raid == forecast["expectedFirstRaid"]
    target_at = first_target_touch(rows, start, end, forecast.get("targetPrice"), direction)
    invalidation_direction = "Bearish" if direction == "Bullish" else "Bullish"
    invalidated_at = first_target_touch(rows, start, end, forecast.get("invalidationPrice"), invalidation_direction)
    target_first = bool(target_at and (not invalidated_at or target_at < invalidated_at))
    favorable = (max(row["h"] for row in selected) - asia["midnightOpen"]) if direction == "Bullish" else (asia["midnightOpen"] - min(row["l"] for row in selected))
    adverse = (asia["midnightOpen"] - min(row["l"] for row in selected)) if direction == "Bullish" else (max(row["h"] for row in selected) - asia["midnightOpen"])
    if direction_correct and expected_raid_correct:
        grade = "right"
    elif not direction_correct and not target_first:
        grade = "wrong"
    else:
        grade = "mixed"
    return {
        "grade": grade, "directionCorrect": direction_correct, "expectedRaidCorrect": expected_raid_correct,
        "firstRaid": first_raid, "firstRaidAt": first_raid_at, "targetReached": bool(target_at),
        "targetReachedAt": target_at, "invalidated": bool(invalidated_at), "invalidatedAt": invalidated_at,
        "targetBeforeInvalidation": target_first, "finalClose": final_close,
        "favorablePips": round(max(0, favorable) * 10000, 1), "adversePips": round(max(0, adverse) * 10000, 1),
        "note": "Right requires both the closing direction and the predicted first Asian-edge raid to match.",
    }


def compact_btmm(btmm: dict) -> dict:
    return deepcopy({
        "expectedPattern": btmm["expectedPattern"], "pattern": btmm["pattern"],
        "asiaStopHunt": btmm["asiaStopHunt"], "hodLod": btmm["hodLod"],
        "levelCount": btmm["levelCount"], "adr": btmm["adr"], "emas": btmm["emas"],
        "tdiProxy": btmm["tdiProxy"], "railroadTracks": btmm["railroadTracks"],
    })


def update_forecast_history(history: dict, checkpoint: str | None, now: datetime, bias: dict, sessions: dict, events: list[dict], rows: list[dict], btmm: dict) -> dict | None:
    trade_day = date.fromisoformat(sessions["tradeDate"])
    key = trade_day.isoformat()
    records = history["records"]
    if checkpoint == "midnight" and key not in records and sessions.get("asia", {}).get("complete") and sessions["asia"].get("midnightOpen") is not None:
        records[key] = {
            "tradeDate": key,
            "capturedAt": now.astimezone(NEW_YORK).isoformat(),
            "locked": True,
            "forecast": frozen_prediction(bias, sessions),
            "asianRange": {**compact_session(sessions["asia"]), "midpoint": sessions["asia"]["midpoint"], "midnightOpen": sessions["asia"]["midnightOpen"], "quality": sessions["asia"]["quality"]},
            "topDown": bias["stack"],
            "btmmAtCapture": compact_btmm(btmm),
            "news": event_digest(events, trade_day),
            "checkpoints": {"midnight": {"observedAt": now.astimezone(NEW_YORK).isoformat(), "status": "forecast locked"}},
            "result": None,
        }
    record = records.get(key)
    if not record:
        return None
    checkpoints = record.setdefault("checkpoints", {})
    if checkpoint == "london" and "london" not in checkpoints:
        checkpoints["london"] = {"observedAt": now.astimezone(NEW_YORK).isoformat(), "status": "recorded", **compact_session(sessions["london"]), "btmm": compact_btmm(btmm)}
    elif checkpoint == "newYork" and "newYork" not in checkpoints:
        checkpoints["newYork"] = {"observedAt": now.astimezone(NEW_YORK).isoformat(), "status": "recorded", **compact_session(sessions["newYork"]), "btmm": compact_btmm(btmm)}
    elif checkpoint == "final" and "final" not in checkpoints:
        checkpoints["final"] = {"observedAt": now.astimezone(NEW_YORK).isoformat(), "status": "graded", "btmm": compact_btmm(btmm)}
        record["result"] = grade_forecast(record, rows, trade_day)
    return record


def forecast_stats(history: dict) -> dict:
    results = [record.get("result") for record in history["records"].values() if record.get("result")]
    directional = [item for item in results if item.get("grade") in {"right", "wrong", "mixed"}]
    right = sum(item["grade"] == "right" for item in directional)
    return {
        "completed": len(results), "directional": len(directional), "right": right,
        "wrong": sum(item["grade"] == "wrong" for item in directional),
        "mixed": sum(item["grade"] == "mixed" for item in directional),
        "noTrade": sum(item["grade"] == "no-trade" for item in results),
        "strictHitRate": round(right / len(directional) * 100, 1) if directional else None,
    }


def main() -> None:
    now = datetime.now(UTC).astimezone(CHICAGO)
    daily, hourly, intraday, events = yahoo("1d", "1y"), yahoo("1h", "1mo"), yahoo("5m", "5d"), parse_calendar()
    month, week, day = previous_month(daily, now), previous_week(daily, now), prior_day(daily, now)
    four_hour, one_hour = four_hour_frame(hourly, now), one_hour_frame(hourly, now)
    price = intraday[-1]["c"]
    bias = build_top_down(month, week, day, four_hour, one_hour, price, events, now)
    sessions = build_sessions(intraday, now, bias)
    btmm = build_btmm(daily, intraday, sessions, now)
    day_story = build_day_story(intraday, sessions, bias, btmm, now)
    checkpoint = scheduled_checkpoint(now)
    history = load_forecasts()
    current_forecast = update_forecast_history(history, checkpoint, now, bias, sessions, events, intraday, btmm)
    history["lastUpdatedAt"] = now.isoformat()
    history["records"] = dict(sorted(history["records"].items())[-500:])
    payload = {
        "meta": {"symbol": "GBP/USD", "updatedAt": now.isoformat(), "timezone": "America/Chicago", "priceSource": "Yahoo Finance indicative GBPUSD=X", "calendarSource": "Forex Factory public weekly calendar", "modelVersion": "3.2 rolling day tape", "checkpoint": checkpoint or "refresh"},
        "quote": {"price": price, "asOf": intraday[-1]["t"]},
        "previousMonth": month, "lastWeek": week, "priorDay": day, "fourHour": four_hour, "oneHour": one_hour,
        "bias": bias, "sessions": sessions, "btmm": btmm, "dayStory": day_story,
        "forecastAudit": current_forecast, "forecastStats": forecast_stats(history),
        "levels": build_levels(month, week, day, sessions, btmm), "candles": intraday[-576:], "calendar": events,
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    FORECASTS.write_text(json.dumps(history, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote market data at {checkpoint or 'refresh'} checkpoint with {len(history['records'])} frozen forecast(s)")


if __name__ == "__main__":
    main()
