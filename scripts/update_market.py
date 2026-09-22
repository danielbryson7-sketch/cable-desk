#!/usr/bin/env python3
"""Build the static market briefing consumed by the GitHub Pages dashboard."""

from __future__ import annotations

import json
import math
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "data" / "market.json"
CHICAGO = ZoneInfo("America/Chicago")
UTC = timezone.utc
YAHOO = "https://query1.finance.yahoo.com/v8/finance/chart/GBPUSD=X"
CALENDAR = "https://nfs.faireconomy.media/ff_calendar_thisweek.xml"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; CableDesk/1.0)"}


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


def daily_trading_date(stamp: int):
    """Yahoo timestamps FX daily bars near the prior evening's UTC rollover."""
    moment = datetime.fromtimestamp(stamp, UTC)
    return (moment + timedelta(days=1)).date() if moment.hour >= 22 else moment.date()


def previous_week(rows: list[dict], now: datetime) -> dict:
    this_monday = (now - timedelta(days=now.weekday())).date()
    last_monday = this_monday - timedelta(days=7)
    last_sunday = this_monday - timedelta(days=1)
    selected = []
    for row in rows:
        day = daily_trading_date(row["t"])
        if last_monday <= day <= last_sunday:
            selected.append(row)
    if not selected:
        selected = rows[-5:]
    high = max(row["h"] for row in selected)
    low = min(row["l"] for row in selected)
    return {
        "start": last_monday.isoformat(),
        "end": last_sunday.isoformat(),
        "open": selected[0]["o"],
        "high": high,
        "low": low,
        "close": selected[-1]["c"],
        "midpoint": round((high + low) / 2, 6),
        "rangePips": round((high - low) * 10000, 1),
    }


def prior_day(rows: list[dict], now: datetime) -> dict:
    candidates = []
    today = now.date()
    for row in rows:
        day = daily_trading_date(row["t"])
        if day < today:
            candidates.append((day, row))
    day = max(item[0] for item in candidates)
    row = [item[1] for item in candidates if item[0] == day][-1]
    return {"date": day.isoformat(), **{key: row[key] for key in ("o", "h", "l", "c")}}


def parse_calendar(now: datetime) -> list[dict]:
    root = ET.fromstring(fetch(CALENDAR))
    events = []
    for node in root.findall(".//event"):
        item = {child.tag: (child.text or "").strip() for child in node}
        if item.get("country") not in {"GBP", "USD"}:
            continue
        raw_time = item.get("time", "")
        if not raw_time or raw_time.lower() in {"all day", "tentative"}:
            stamp = None
            chicago_stamp = item.get("date", "")
        else:
            naive = datetime.strptime(f"{item['date']} {raw_time}", "%m-%d-%Y %I:%M%p")
            # The public weekly XML feed uses UTC.
            stamp_dt = naive.replace(tzinfo=UTC)
            stamp = stamp_dt.isoformat()
            chicago_stamp = stamp_dt.astimezone(CHICAGO).isoformat()
        events.append({
            "title": item.get("title", "Scheduled event"),
            "currency": item.get("country", ""),
            "impact": item.get("impact", "Low"),
            "forecast": item.get("forecast", ""),
            "previous": item.get("previous", ""),
            "url": item.get("url", ""),
            "timeUtc": stamp,
            "timeChicago": chicago_stamp,
        })
    return events


def build_bias(last_week: dict, prior: dict, price: float, events: list[dict], now: datetime) -> dict:
    score = 0
    evidence = []

    weekly_up = last_week["close"] > last_week["open"]
    score += 2 if weekly_up else -2
    evidence.append({
        "label": "Last week",
        "value": f"closed {'higher' if weekly_up else 'lower'}",
        "tone": "bull" if weekly_up else "bear",
    })

    above_mid = price >= last_week["midpoint"]
    score += 1 if above_mid else -1
    evidence.append({
        "label": "Location",
        "value": f"{'premium' if above_mid else 'discount'} to last week’s midpoint",
        "tone": "bull" if above_mid else "bear",
    })

    prior_up = prior["c"] > prior["o"]
    score += 1 if prior_up else -1
    evidence.append({
        "label": "Prior day",
        "value": f"closed {'higher' if prior_up else 'lower'}",
        "tone": "bull" if prior_up else "bear",
    })

    dist_high = abs(last_week["high"] - price)
    dist_low = abs(price - last_week["low"])
    draw = "Last week high" if dist_high <= dist_low else "Last week low"
    draw_price = last_week["high"] if dist_high <= dist_low else last_week["low"]

    today = now.date()
    today_events = []
    for event in events:
        if not event["timeChicago"]:
            continue
        event_dt = datetime.fromisoformat(event["timeChicago"])
        if event_dt.date() == today:
            today_events.append(event)
    disruptive = [event for event in today_events if event["impact"] in {"High", "Medium"}]
    risk = "high" if any(event["impact"] == "High" for event in disruptive) else "medium" if disruptive else "low"

    if score >= 3:
        direction = "Bullish"
    elif score <= -3:
        direction = "Bearish"
    else:
        direction = "Neutral"
    confidence = "moderate" if abs(score) == 4 and risk == "low" else "guarded" if abs(score) >= 3 else "low"
    if disruptive:
        confidence = "event-risk"

    opposing = prior["l"] if direction == "Bullish" else prior["h"] if direction == "Bearish" else last_week["midpoint"]
    confirmation = (
        "Sell-side sweep, then bullish displacement and a 5m structure shift"
        if direction == "Bullish"
        else "Buy-side sweep, then bearish displacement and a 5m structure shift"
        if direction == "Bearish"
        else "A sweep of either side followed by displacement; do not predict the first break"
    )
    return {
        "direction": direction,
        "score": score,
        "confidence": confidence,
        "newsRisk": risk,
        "draw": draw,
        "drawPrice": draw_price,
        "invalidation": opposing,
        "confirmation": confirmation,
        "evidence": evidence,
        "todayEventCount": len(today_events),
        "disruptiveEventCount": len(disruptive),
        "method": "Directional context only—not an entry signal.",
    }


def main() -> None:
    now = datetime.now(UTC).astimezone(CHICAGO)
    daily = yahoo("1d", "3mo")
    intraday = yahoo("5m", "5d")
    calendar = parse_calendar(now)
    last_week = previous_week(daily, now)
    prior = prior_day(daily, now)
    price = intraday[-1]["c"]
    payload = {
        "meta": {
            "symbol": "GBP/USD",
            "updatedAt": now.isoformat(),
            "timezone": "America/Chicago",
            "priceSource": "Yahoo Finance indicative GBPUSD=X",
            "calendarSource": "Forex Factory public weekly calendar",
        },
        "quote": {"price": price, "asOf": intraday[-1]["t"]},
        "lastWeek": last_week,
        "priorDay": prior,
        "bias": build_bias(last_week, prior, price, calendar, now),
        "candles": intraday[-576:],
        "calendar": calendar,
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {OUTPUT} with {len(payload['candles'])} candles and {len(calendar)} GBP/USD events")


if __name__ == "__main__":
    main()
