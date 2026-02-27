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


def find_current_rounds():
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
    print("Market candidates:")
    for ts, _, candidate_title in candidates:
        print(f"- {dt.datetime.utcfromtimestamp(ts).isoformat()}Z | {candidate_title}")
    rounds = []
    for end_ts, m, title in candidates:
        token_ids = _loads(m.get("clobTokenIds"))
        outcomes = _loads(m.get("outcomes") or m.get("shortOutcomes"))
        if not isinstance(token_ids, list) or len(token_ids) < 2:
            continue
        token_ids = [str(x) for x in token_ids]

        up_token = token_ids[0]
        down_token = token_ids[1]

        if isinstance(outcomes, list) and len(outcomes) == len(token_ids):
            for i, o in enumerate(outcomes):
                x = str(o).lower()
                if x == "up":
                    up_token = token_ids[i]
                elif x == "down":
                    down_token = token_ids[i]

        rounds.append(
            {
                "title": title,
                "up_token": up_token,
                "down_token": down_token,
                "end_ts": end_ts,
            }
        )

    if not rounds:
        raise RuntimeError("Cannot parse clobTokenIds for live market(s)")
    return rounds


def get_book(token_id: str) -> dict:
    r = requests.get("https://clob.polymarket.com/book", params={"token_id": token_id}, timeout=15)
    r.raise_for_status()
    return r.json()


def main() -> None:
    rounds = find_current_rounds()
    for i, r in enumerate(rounds, start=1):
        print(f"Market {i}: {r['title']}")
        print(f"Up token_id: {r['up_token']}")
        print(f"Down token_id: {r['down_token']}")
        print(f"end_time(UTC): {dt.datetime.utcfromtimestamp(r['end_ts']).isoformat()}Z")
    print("Streaming: up price, down price, last_trade_price (if present)")

    while True:
        tick_start = time.time()
        now = int(time.time())
        parts = []
        need_rollover = False

        for r in rounds:
            t_remain = r["end_ts"] - now
            up_book = get_book(r["up_token"])
            down_book = get_book(r["down_token"])

            # best ask = asks[-1] (asks are descending)
            up_price = _f(up_book.get("asks", [{}])[-1].get("price")) if up_book.get("asks") else None
            down_price = _f(down_book.get("asks", [{}])[-1].get("price")) if down_book.get("asks") else None

            last_trade_price = up_book.get("last_trade_price") or "NA"
            parts.append(
                f"t_remain={t_remain:>4}s | "
                f"Up={up_price:.3f} | Down={down_price:.3f} | "
                f"last={last_trade_price}"
            )
            if t_remain <= 0:
                need_rollover = True

        print("; ".join(parts))

        if need_rollover:
            rounds = find_current_rounds()
            for i, r in enumerate(rounds, start=1):
                print(f"\n[rollover] Market {i}: {r['title']}")
                print(f"[rollover] Up token_id: {r['up_token']}")
                print(f"[rollover] Down token_id: {r['down_token']}")
                print(f"[rollover] end_time(UTC): {dt.datetime.utcfromtimestamp(r['end_ts']).isoformat()}Z")

        time.sleep(max(0, 0.5 - (time.time() - tick_start)))


if __name__ == "__main__":
    main()
