#!/usr/bin/env python3
"""
Polymarket: find current round for "BTC Up/Down – 15 Minutes" (Up token),
then continuously print latest:
- best bid (top of bids)
- best ask (top of asks)
- last trade price (from /book response if present)

Public endpoints only:
- Gamma public search: https://gamma-api.polymarket.com/public-search
- CLOB order book:     https://clob.polymarket.com/book?token_id=...

Docs:
- Gamma public-search: https://docs.polymarket.com/api-reference/search/search-markets-events-and-profiles
- CLOB get order book: https://docs.polymarket.com/api-reference/market-data/get-order-book
"""

import time
import json
import datetime as dt
from typing import Any, Dict, List, Optional, Tuple

import requests


GAMMA_PUBLIC_SEARCH = "https://gamma-api.polymarket.com/public-search"
GAMMA_EVENTS = "https://gamma-api.polymarket.com/events"
CLOB_BOOK = "https://clob.polymarket.com/book"


QUERY = "BTC Up/Down - 15 Minutes"  # try this first; adjust if Polymarket uses slightly different title
POLL_SEC = 1.0
HTTP_TIMEOUT = 15


def iso_to_unix(ts: str) -> int:
    # handles "...Z" and offsets like "+00:00"
    return int(dt.datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp())


def _as_list(v: Any) -> List[Any]:
    return v if isinstance(v, list) else []


def _json_loads_maybe(v: Any) -> Any:
    if isinstance(v, str):
        try:
            return json.loads(v)
        except Exception:
            return v
    return v


def find_current_round_up_token():
    """
    更穩定版本：
    - 不依賴精確 QUERY
    - 直接抓較多 market
    - 用時間 + 關鍵詞過濾
    """

    markets = []
    # public-search 只回少量相關結果，抓不到當前輪次；改為掃描 open events 分頁
    for offset in range(0, 6000, 500):
        r = requests.get(
            GAMMA_EVENTS,
            params={"active": "true", "closed": "false", "limit": 500, "offset": offset},
            timeout=HTTP_TIMEOUT,
        )
        r.raise_for_status()
        data = r.json()
        if not isinstance(data, list) or not data:
            break
        for ev in data:
            for m in ev.get("markets", []) or []:
                markets.append(m)

    if not markets:
        raise RuntimeError("Gamma returned no markets")

    now = int(time.time())
    candidates = []

    for m in markets:
        title = (m.get("question") or m.get("title") or "").lower()
        slug = (m.get("slug") or "").lower()

        if "btc" not in title and "bitcoin" not in title and "btc" not in slug and "bitcoin" not in slug:
            continue

        is_15m = ("15" in title and "minute" in title) or "15m" in title or "-15m-" in slug
        if not is_15m:
            continue

        if m.get("closed") is True:
            continue

        end_iso = m.get("endDate") or m.get("endDateIso")
        if not end_iso:
            continue

        try:
            end_ts = iso_to_unix(str(end_iso))
        except Exception:
            continue

        # 只看未來 30 分鐘內
        if 0 < (end_ts - now) < 1800:
            candidates.append((end_ts, m))

    if not candidates:
        raise RuntimeError("No BTC 15m round found (check live title format)")

    # 最近結束時間的那輪
    candidates.sort(key=lambda x: x[0])
    end_ts, m = candidates[0]

    # 解析 token_id
    token_ids_raw = m.get("clobTokenIds")
    token_ids_raw = _json_loads_maybe(token_ids_raw)

    if isinstance(token_ids_raw, list):
        token_ids = [str(x) for x in token_ids_raw]
    else:
        raise RuntimeError("Cannot parse clobTokenIds")

    outcomes_raw = _json_loads_maybe(
        m.get("outcomes") or m.get("shortOutcomes")
    )

    token_up = token_ids[0]

    if isinstance(outcomes_raw, list) and len(outcomes_raw) == len(token_ids):
        for i, o in enumerate(outcomes_raw):
            if "up" in str(o).lower() or str(o).lower() == "yes":
                token_up = token_ids[i]
                break

    title = (m.get("question") or m.get("title") or "").strip()

    return token_up, end_ts, title

def get_book(token_id: str) -> Dict[str, Any]:
    r = requests.get(CLOB_BOOK, params={"token_id": token_id}, timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    return r.json()


def top_price(levels: Any, side_key: str) -> Optional[float]:
    """
    book["bids"] / book["asks"] usually are arrays of objects with price/size.
    We treat index 0 as best.
    """
    if not isinstance(levels, list) or not levels:
        return None
    lvl0 = levels[0]
    if isinstance(lvl0, dict) and "price" in lvl0:
        try:
            return float(lvl0["price"])
        except Exception:
            return None
    return None


def main():
    token_id, end_ts, title = find_current_round_up_token()
    print(f"Market: {title}")
    print(f"Up token_id: {token_id}")
    print(f"end_time(UTC): {dt.datetime.utcfromtimestamp(end_ts).isoformat()}Z")
    print("Streaming: best_bid, best_ask, last_trade_price (if present)\n")

    last_line = None
    while True:
        now = int(time.time())
        t_remain = end_ts - now

        try:
            book = get_book(token_id)
            best_bid = top_price(book.get("bids"), "bids")
            best_ask = top_price(book.get("asks"), "asks")

            # Docs mention last trade price is included in order book response. :contentReference[oaicite:0]{index=0}
            last_trade = book.get("last_trade_price") or book.get("lastTradePrice") or book.get("last_trade")

            line = (
                f"t_remain={t_remain:>4}s | "
                f"bid={best_bid if best_bid is not None else 'NA'} | "
                f"ask={best_ask if best_ask is not None else 'NA'} | "
                f"last={last_trade if last_trade is not None else 'NA'}"
            )

            # print only if changed (reduces spam)
            if line != last_line:
                print(line)
                last_line = line

        except Exception as e:
            print(f"[warn] {type(e).__name__}: {e}")

        # if round ended, re-discover next round
        if t_remain <= 0:
            try:
                token_id, end_ts, title = find_current_round_up_token()
                print(f"\n[rollover] Market: {title}")
                print(f"[rollover] Up token_id: {token_id}")
                print(f"[rollover] end_time(UTC): {dt.datetime.utcfromtimestamp(end_ts).isoformat()}Z\n")
                last_line = None
            except Exception as e:
                print(f"[warn] rollover failed: {e}")

        time.sleep(POLL_SEC)


if __name__ == "__main__":
    main()
