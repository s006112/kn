## Contract Scope

This document describes the current implemented behavior of the `hyper` single-pair REST-polling grid engine.

The engine manages one live `SYMBOL`, classifies open orders into a paired grid state or a one-sided residual state, decides whether to keep, rebuild, or stop, and executes rebuilds through the Hyperliquid SDK.

This contract does not define multi-pair trading, ladder grids, websocket behavior, portfolio management, automatic recovery outside the implemented paths, or intended future behavior.

## Authoritative Files

Primary source files:

- `hyper/grid.py`
- `hyper/grid_config.py`
- `hyper/grid_decision.py`
- `hyper/grid_execution.py`

`hyper/test.py` is secondary evidence only. `archive/hyperliquid_recursive.py` is out of scope.

## Runtime Configuration

The engine behavior is parameterized by names defined in `hyper/grid_config.py`. This contract refers to those names directly so the document remains aligned when values change.

Key runtime inputs:

- `SYMBOL`
- `BTC_MID_KEY`
- `BUDGET_USDC`
- `GRID_STEP`
- `BUY_GRID_FACTOR`
- `SELL_GRID_FACTOR`
- `TICK_COUNT`
- `MAIN_LOOP_POLL_INTERVAL_SEC`
- `CONSECUTIVE_READS`
- `ALLOW_BUY_ONLY_WHEN_NO_BTC`
- `MAX_RETRIES`
- `RETRY_SEC`
- `WAIT_NO_OPEN_ORDERS_INTERVAL_SEC`

Derived configuration:

- `GRID_GAP = (BUY_GRID_FACTOR + SELL_GRID_FACTOR) * GRID_STEP`
- `ORDER_ZONE`, derived from `GRID_GAP` in `grid_config.py`

## Pipeline

`bootstrap()`:

- Creates `Info(constants.MAINNET_API_URL)`.
- Creates `Exchange(Account.from_key(API_WALLET_KEY), constants.MAINNET_API_URL, account_address=ACCOUNT_ADDRESS)`.
- Calls `read_orders(info)`, `summarize_orders(orders)`, and `classify_order_mode(orders)`.
- Accepts live `PAIR` directly and returns `(info, trader, state)`.
- Accepts live `BUY_ONLY` and `SELL_ONLY` directly without replacing the residual order.
- For empty `ABNORMAL`, builds `live_snapshot = {"orders": orders, "btc_mid": None, **state}` and calls `rebuild(info, trader, live_snapshot)`.
- For non-empty `ABNORMAL`, logs the unsafe layout and halts without cancelling or replacing orders.
- If rebuild returns `None`, logs `Bootstrap Rebuild Failed` and returns `(info, trader, None)`.

`run_cycle(info, trader, saved_state, tick_count, live_poll_interval_sec)`:

- Reads `btc_mid = read_btc_mid(info)` every cycle.
- Refreshes live orders when any of the following is true:
- `btc_mid is None`
- `tick_count % TICK_COUNT == 0`
- `PAIR`: price is near either saved BUY or SELL.
- `BUY_ONLY`: price is near the residual BUY.
- `SELL_ONLY`: price is near the residual SELL.
- If no refresh is needed, returns `(saved_state, btc_mid)`.
- On refresh, reads and summarizes orders, builds `live_snapshot = {"orders": orders, "btc_mid": btc_mid, **classify_order_mode(orders)}`, and calls `decide_cycle_action(live_snapshot, saved_state)`.
- On `"keep"`, returns `(saved_state, btc_mid)`.
- On `"rebuild"`, calls `rebuild(info, trader, live_snapshot, strategy, rebuild_price)` and returns `(new_state, btc_mid)`.
- On any other action, summarizes live orders, logs `abnormal`, and returns `(None, btc_mid)`.

`main()`:

- Calls `bootstrap()` and exits if `saved_state is None`.
- Sleeps for `live_poll_interval_sec`, increments `tick_count`, and calls `run_cycle`.
- Exits when `run_cycle` returns `saved_state is None`.
- Uses adaptive polling based on repeated identical non-`None` BTC mid reads:
- Base interval starts at `MAIN_LOOP_POLL_INTERVAL_SEC`.
- When identical reads accumulate, the interval scales as `MAIN_LOOP_POLL_INTERVAL_SEC * (1 + (same_btc_mid_reads // CONSECUTIVE_READS))`.
- When `btc_mid is None`, adaptive polling resets to `MAIN_LOOP_POLL_INTERVAL_SEC`.
- When price is inside the order zone, the computed interval is halved.

## State Model

Mode constants:

- `PAIR_MODE = "PAIR"`
- `BUY_ONLY_MODE = "BUY_ONLY"`
- `SELL_ONLY_MODE = "SELL_ONLY"`
- `ABNORMAL_MODE = "ABNORMAL"`

Live classification states from `classify_order_mode(orders)`:

- `PAIR`: `{"mode": PAIR_MODE, "buy_price": buy_price, "sell_price": sell_price}`
- `BUY_ONLY`: `{"mode": BUY_ONLY_MODE, "buy_price": buy_price}`
- `SELL_ONLY`: `{"mode": SELL_ONLY_MODE, "sell_price": sell_price}`
- `ABNORMAL`: `{"mode": ABNORMAL_MODE}`

`classify_order_mode` returns `PAIR` only when there are exactly two orders, one `BUY`, one `SELL`, `buy_price < sell_price`, and `price_gap_matches(buy_price, sell_price, GRID_GAP)` is true.

If there is exactly one order, a lone `BUY` becomes `BUY_ONLY` and a lone `SELL` becomes `SELL_ONLY`. Any other shape becomes `ABNORMAL`.

`live_snapshot` contains `orders`, `btc_mid`, and the fields returned by the live classification.

Successful rebuild states returned by `finish_rebuild` include:

- `mode`
- `buy_price`
- `sell_price`
- `price`

A `PAIR` accepted directly by `bootstrap()` uses the live classification shape and does not include `price`.

`ABNORMAL` is only a live classification. It is not returned as a successful rebuild state.

## Read and Price Helpers

`normalize_order(order)`:

- Converts raw side `"B"` to `"BUY"` and `"A"` to `"SELL"`.
- Raises `ValueError` for any other raw side.
- Copies the original order fields.
- Sets `price = normalize_price(order["limitPx"])`.

`read_orders(info)`:

- Calls `retry_read("open-orders", lambda: info.open_orders(ACCOUNT_ADDRESS))`.
- Normalizes every returned order with `normalize_order`.

`read_btc_mid(info)`:

- Calls `retry_read("btc-mid", info.all_mids)`.
- Reads `BTC_MID_KEY`.
- Returns `None` when the value is missing or non-positive.
- Otherwise returns `normalize_price(btc_mid)`.

`read_btc_grid(info)`:

- Calls `read_btc_mid(info)`.
- Raises `ValueError("Failed to get BTC mid price")` when that returns `None`.
- Rounds the mid-derived anchor onto the `GRID_STEP` grid.

`normalize_price(price)` returns `float(round(float(price)))`.

`format_price(price)` returns `str(int(normalize_price(price)))`.

`price_gap_matches(buy_price, sell_price, expected_gap)` returns whether normalized `sell_price - buy_price` equals normalized `expected_gap`.

`retry_read(name, fn, retries=MAX_RETRIES, delay=RETRY_SEC)` retries:

- HTTP statuses `429`, `502`, `503`, and `504`
- request timeout and connection exceptions
- `TimeoutError`
- `ConnectionResetError`
- `socket.timeout`
- selected `OSError` cases whose message indicates timeout, connection, or temporary failure

Non-retryable exceptions are raised immediately.

## Decision Rules

`decide_cycle_action(live_snapshot, saved_state)` returns `(action, strategy, rebuild_price)`.

Possible `action` values:

- `"keep"`
- `"rebuild"`
- `"abnormal"`

Possible `strategy` values:

- `"done_deal"`
- `None`

When `saved_state["mode"] == PAIR_MODE`:

- Live `SELL_ONLY` logs a filled BUY and returns `("rebuild", "done_deal", saved_state["buy_price"])`.
- Live `BUY_ONLY` logs a filled SELL and returns `("rebuild", "done_deal", saved_state["sell_price"])`.
- Live `PAIR` with both prices equal to `saved_state` returns `("keep", None, None)`.
- Any other live state returns `("abnormal", None, None)`.

When `saved_state["mode"] == BUY_ONLY_MODE`:

- Empty `live_snapshot["orders"]` returns `("rebuild", "done_deal", saved_state["buy_price"])`.
- Live `BUY_ONLY` returns `("keep", None, None)`.
- Any other non-empty live state returns `("abnormal", None, None)`.

When `saved_state["mode"] == SELL_ONLY_MODE`:

- Empty `live_snapshot["orders"]` returns `("rebuild", "done_deal", saved_state["sell_price"])`.
- Live `SELL_ONLY` returns `("keep", None, None)`.
- Any other non-empty live state returns `("abnormal", None, None)`.

`BUY_ONLY` and `SELL_ONLY` keep paths do not compare the live one-sided price against the saved price.

## Rebuild and Execution

Execution helpers are `build_pair`, `classify_order_result`, `place_limit_order`, `log_rebuild`, `wait_until`, `cleanup_orders`, `finish_rebuild`, and `rebuild`.

`build_pair(price)`:

- Raises `ValueError("Invalid price")` when `price <= 0`.
- Builds a BUY at `price - (BUY_GRID_FACTOR * GRID_STEP)`.
- Builds a SELL at `price + (SELL_GRID_FACTOR * GRID_STEP)`.
- Raises `ValueError("Invalid buy price")` when the computed BUY price is non-positive.
- Sizes each side as `round(BUDGET_USDC / side_price, 5)`.

`classify_order_result(result)`:

- Reads the first item in `result["response"]["data"]["statuses"]`.
- Returns `"ok"` for `"resting"` or `"filled"`.
- Returns `"insufficient_spot_balance"` only when an error contains `Insufficient spot balance`.
- Returns `"error"` for missing statuses, other errors, or unknown status shapes.

`place_limit_order(trader, action)` calls `trader.order(SYMBOL, action["side"] == "BUY", float(action["size"]), float(action["price"]), {"limit": {"tif": "Gtc"}}, False)` and returns `classify_order_result(result)`.

`wait_until(info, done, max_tries=MAX_RETRIES, interval_sec=WAIT_NO_OPEN_ORDERS_INTERVAL_SEC)` polls `read_orders(info)` until `done(orders)` is true or attempts are exhausted.

`cleanup_orders(info, trader, orders=None)`:

- Reads orders when `orders is None`.
- Calls `trader.bulk_cancel` for each `oid`.
- Uses `wait_until` until no open orders remain.
- Returns `True` on confirmation and `False` after logging cleanup failure.

`rebuild(info, trader, live_snapshot, strategy="reset", rebuild_price=None)`:

- When `strategy == "done_deal"`, uses `rebuild_price` when provided, otherwise reads a fresh `read_btc_grid(info)`.
- A one-sided live state treats `live_snapshot["orders"][0]` as the remaining old order.
- An empty live order list rebuilds directly at the done-deal price.
- A non-empty live state outside `BUY_ONLY` or `SELL_ONLY` logs an abnormal done-deal rebuild and returns `None` without cleanup or placement.
- In the general path, cancels existing `live_snapshot["orders"]` when present, ignores explicit `rebuild_price`, reads a fresh `read_btc_grid(info)`, and rebuilds from that price.

General rebuild path:

- Calls `build_pair(price)` and `log_rebuild`.
- Places BUY first.
- If BUY is not `"ok"`, returns `finish_rebuild(..., buy_action, buy_status)`.
- Only if BUY is `"ok"`, places SELL and returns `finish_rebuild(..., sell_action, sell_status)`.

One-sided replacement path:

- Calls `build_pair(price)` and `log_rebuild`.
- If the remaining order is `SELL`, places BUY first, cancels the old SELL, verifies that the old `oid` is gone, then places SELL.
- If the remaining order is `BUY`, places SELL first, cancels the old BUY, verifies that the old `oid` is gone, then places BUY.
- If the first placement is not `"ok"`, returns `None`.
- Cancel or verification exceptions trigger cleanup and return `None`.
- Failed old-order absence verification triggers cleanup and return `None`.
- The second placement is finalized through `finish_rebuild`.

`finish_rebuild(info, trader, price, buy_action, sell_action, action, status)`:

- `"ok"` returns `PAIR`.
- SELL `insufficient_spot_balance` with `ALLOW_BUY_ONLY_WHEN_NO_BTC` returns `BUY_ONLY`.
- BUY failure never degrades to `SELL_ONLY`; it logs partial placement failure, calls `cleanup_orders(info, trader)`, and returns `None`.
- Any other status also cleans up and returns `None`.

## Failure Semantics

`decide_cycle_action` returns `"abnormal"` when no accepted branch matches.

`run_cycle` stops the engine by returning a `None` state on abnormal action or rebuild failure.

`main` exits when `saved_state is None`.

`bootstrap` exits with a `None` state when its rebuild fails.

`bootstrap` preserves a valid one-sided residual order across process restarts instead of cancelling and re-anchoring it at the current market price.

`bootstrap` rebuilds only from an empty order book. A non-empty `ABNORMAL` order layout halts for manual intervention.

There is no global exception shell in `grid.py`.

Uncaught exceptions from reads, price helpers, `build_pair`, order placement outside local handling, missing `oid`, and `read_btc_grid` propagate to the caller.

The one-sided replacement path catches exceptions only around cancel and absence verification.

## Known Limitations

- `classify_order_mode` does not validate order size, symbol, `oid`, or notional.
- `PAIR` residual one-sided decisions do not validate the remaining live one-sided price against `saved_state`.
- `BUY_ONLY` keep does not validate live `buy_price` against `saved_state["buy_price"]`.
- `SELL_ONLY` keep does not validate live `sell_price` against `saved_state["sell_price"]`.
- `cleanup_orders` assumes every order being cancelled has `oid`.
- The engine has no global exception recovery loop.
