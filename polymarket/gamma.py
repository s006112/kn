#!/usr/bin/env python3

import datetime as dt
import json
import time
from typing import Any

import requests


def _as_ts(iso: str) -> int:
    return int(dt.datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp())


def _loads(v: Any) -> Any:
    if isinstance(v, str):
        try:
            return json.loads(v)
        except Exception:
            return v
    return v


def _f(v: Any):
    try:
        return float(v)
    except Exception:
        return None


def _last_trade(book: dict) -> Any:
    return book.get("last_trade_price") or book.get("lastTradePrice") or book.get("last_trade") or "NA"


def find_current_round():
    now = int(time.time())
    candidates = []

    for offset in range(0, 6000, 500):
        r = requests.get(
            "https://gamma-api.polymarket.com/events",
            params={"active": "true", "closed": "false", "limit": 500, "offset": offset},
            timeout=15,
        )
        r.raise_for_status()
        events = r.json()
        if not isinstance(events, list) or not events:
            break
        for ev in events:
            for m in ev.get("markets", []) or []:
                title = (m.get("question") or m.get("title") or "").strip()
                if "bitcoin up or down" not in title.lower():
                    continue
                slug = str(m.get("slug") or "").lower()
                if "-15m-" not in slug:
                    continue
                end_iso = m.get("endDate") or m.get("endDateIso")
                if not end_iso:
                    continue
                try:
                    end_ts = _as_ts(str(end_iso))
                except Exception:
                    continue
                if 0 < (end_ts - now) < 1800:
                    candidates.append((end_ts, m, title))

    if not candidates:
        raise RuntimeError("No live Bitcoin Up or Down market found")

    candidates.sort(key=lambda x: x[0])
    end_ts, m, title = candidates[0]

    token_ids = _loads(m.get("clobTokenIds"))
    outcomes = _loads(m.get("outcomes") or m.get("shortOutcomes"))
    if not isinstance(token_ids, list) or len(token_ids) < 2:
        raise RuntimeError("Cannot parse clobTokenIds")
    token_ids = [str(x) for x in token_ids]

    up_token = token_ids[0]
    if isinstance(outcomes, list) and len(outcomes) == len(token_ids):
        for i, o in enumerate(outcomes):
            x = str(o).lower()
            if x in ("up", "yes"):
                up_token = token_ids[i]

    return title, up_token, end_ts


def get_book(token_id: str) -> dict:
    r = requests.get("https://clob.polymarket.com/book", params={"token_id": token_id}, timeout=15)
    r.raise_for_status()
    return r.json()


def main() -> None:
    title, up_token, end_ts = find_current_round()
    print(f"Market: {title}")
    print(f"Up token_id: {up_token}")
    print(f"end_time(UTC): {dt.datetime.utcfromtimestamp(end_ts).isoformat()}Z")
    print("Streaming: up price, down price, last_trade_price (if present)")
    while True:
        tick_start = time.time()
        now = int(time.time())
        t_remain = end_ts - now
        up_book = get_book(up_token)
        up_price = _last_trade(up_book)
        up_val = _f(up_price)
        down_price = f"{(1.0 - up_val):.3f}" if up_val is not None else "NA"
        print(f"t_remain={t_remain:>4}s | up={up_price} | down={down_price} | last={up_price}")
        if t_remain <= 0:
            title, up_token, end_ts = find_current_round()
            print(f"\n[rollover] Market: {title}")
            print(f"[rollover] Up token_id: {up_token}")
            print(f"[rollover] end_time(UTC): {dt.datetime.utcfromtimestamp(end_ts).isoformat()}Z")
        time.sleep(max(0, 1 - (time.time() - tick_start)))


if __name__ == "__main__":
    main()
