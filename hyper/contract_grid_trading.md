## Contract Scope

This contract describes the current implemented behavior of the `hyper` single-pair REST polling grid engine.

The engine manages one live `SYMBOL`, classifies open orders into one grid pair or one-sided residual states, decides whether to keep, rebuild, or stop, and executes rebuilds through the Hyperliquid SDK.

This contract does not define multi-pair trading, ladder grids, websocket behavior, portfolio state, automatic recovery outside the implemented paths, or intended future behavior.

## Authoritative Files

Authoritative source files:

- `hyper/grid.py`
- `hyper/grid_config.py`
- `hyper/grid_decision.py`
- `hyper/grid_execution.py`

`hyper/test.py` is secondary evidence only. `archive/hyperliquid_recursive.py` is outside this contract.

## Runtime Configuration

Configuration names are defined in `grid_config.py` and used by this contract:

- `SYMBOL = "UBTC/USDC"`
- `BTC_MID_KEY = "@142"`
- `GRID_STEP = 200.0`
- `BUDGET_USDC = 250.0`
- `BUY_GRID_FACTOR = 0.9`
- `SELL_GRID_FACTOR = 1.1`
- `TICK_COUNT = 40`
- `MAIN_LOOP_POLL_INTERVAL_SEC = 1.0`
- `CONSECUTIVE_READS = 2`
- `GRID_GAP = (BUY_GRID_FACTOR + SELL_GRID_FACTOR) * GRID_STEP`
- `ORDER_ZONE = GRID_GAP * 0.03`
- `ALLOW_BUY_ONLY_WHEN_NO_BTC = True`
- `ALLOW_SELL_ONLY_WHEN_NO_USDC = True`
- `REANCHOR_BREAK = True`
- `REANCHOR_BREAK_STEPS = 2`
- `REANCHOR_DISTANCE = (BUY_GRID_FACTOR + REANCHOR_BREAK_STEPS) * GRID_STEP`
- `MAX_RETRIES = 4`
- `RETRY_SEC = 0.5`
- `WAIT_NO_OPEN_ORDERS_INTERVAL_SEC = 0.5`

Derived values from the standard configuration are `GRID_GAP = 400.0`, `ORDER_ZONE = 12.0`, and `REANCHOR_DISTANCE = 580.0`.

## Pipeline

`bootstrap()`:

- Creates `Info(constants.MAINNET_API_URL)`.
- Creates `Exchange(Account.from_key(API_WALLET_KEY), constants.MAINNET_API_URL, account_address=ACCOUNT_ADDRESS)`.
- Calls `read_orders(info)`, `summarize_orders(orders)`, and `classify_order_mode(orders)`.
- Accepts only live `PAIR` directly and returns `(info, trader, state)`.
- For any non-`PAIR` live classification, builds `live_snapshot = {"orders": orders, "btc_mid": None, **state}` and calls `rebuild(info, trader, live_snapshot)`.
- If rebuild returns `None`, logs `Bootstrap Rebuild Failed` and returns `(info, trader, None)`.

`run_cycle(info, trader, saved_state, tick_count, live_poll_interval_sec)`:

- Reads `btc_mid = read_btc_mid(info)` every cycle.
- Refreshes live orders only when `btc_mid is None`, `tick_count % TICK_COUNT == 0`, `btc_mid <= saved_state["buy_price"] + ORDER_ZONE`, or `btc_mid >= saved_state["sell_price"] - ORDER_ZONE`.
- If no refresh is needed, returns `(saved_state, btc_mid)`.
- On refresh, reads and summarizes orders, builds `live_snapshot = {"orders": orders, "btc_mid": btc_mid, **classify_order_mode(orders)}`, and calls `decide_cycle_action(live_snapshot, saved_state)`.
- On `"keep"`, returns `(saved_state, btc_mid)`.
- On `"rebuild"`, calls `rebuild(info, trader, live_snapshot, strategy, rebuild_price)` and returns `(new_state, btc_mid)`.
- On any other action, summarizes live orders, logs `abnormal`, and returns `(None, btc_mid)`.

`main()`:

- Calls `bootstrap()` and exits if `saved_state is None`.
- Sleeps for `live_poll_interval_sec`, increments `tick_count`, and calls `run_cycle`.
- Exits when `run_cycle` returns `saved_state is None`.
- Uses adaptive polling: consecutive identical non-`None` BTC mid reads increase `live_poll_interval_sec` by `MAIN_LOOP_POLL_INTERVAL_SEC * (1 + same_btc_mid_reads // CONSECUTIVE_READS)`.
- Resets adaptive polling to `MAIN_LOOP_POLL_INTERVAL_SEC` when `btc_mid is None`.

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

`classify_order_mode` returns `PAIR` only for two orders with one BUY, one SELL, and `buy_price < sell_price`. One BUY returns `BUY_ONLY`; one SELL returns `SELL_ONLY`; all other shapes return `ABNORMAL`. `price_gap_matches` is called for `PAIR` but currently always returns `True`.

`live_snapshot` contains `orders`, `btc_mid`, and the live classification fields.

Rebuild states returned by `finish_rebuild` for `PAIR`, `BUY_ONLY`, and `SELL_ONLY` include:

- `mode`
- `buy_price`
- `sell_price`
- `price`

A `PAIR` accepted directly by `bootstrap()` uses the live classification shape and does not include `price`.

`ABNORMAL` is a live classification and is not returned as a successful rebuild state.

## Read and Price Helpers

`normalize_order(order)`:

- Converts raw side `"B"` to `"BUY"` and `"A"` to `"SELL"`.
- Raises `ValueError` for any other raw side.
- Copies the original order fields.
- Sets `price = normalize_price(order["limitPx"])`.

`read_orders(info)` calls `retry_read("open-orders", lambda: info.open_orders(ACCOUNT_ADDRESS))` and normalizes every returned order.

`read_btc_mid(info)` calls `retry_read("btc-mid", info.all_mids)`, reads `BTC_MID_KEY`, returns `None` when the value is missing or `<= 0`, otherwise returns `normalize_price(btc_mid)`.

`read_btc_grid(info)` calls `read_btc_mid(info)`, raises `ValueError("Failed to get BTC mid price")` when it returns `None`, and returns `int((btc_mid / GRID_STEP) + 0.5) * GRID_STEP`.

`normalize_price(price)` returns `float(round(float(price)))`.

`format_price(price)` returns `str(int(normalize_price(price)))`.

`price_gap_matches(buy_price, sell_price, expected_gap)` currently always returns `True`.

`price_distance_at_least(high_price, low_price, distance)` compares normalized prices and returns whether `high_price - low_price >= distance`.

`retry_read(name, fn, retries=MAX_RETRIES, delay=RETRY_SEC)` retries status `429`, `502`, `503`, `504`, request timeout/connection exceptions, `TimeoutError`, `ConnectionResetError`, `socket.timeout`, and selected `OSError` messages. Non-retryable exceptions are raised immediately.

## Decision Rules

`decide_cycle_action(live_snapshot, saved_state)` returns `(action, strategy, rebuild_price)`.

Possible `action` values:

- `"keep"`
- `"rebuild"`
- `"abnormal"`

Possible `strategy` values:

- `"done_deal"`
- `"anchor_break"`
- `None`

`saved_state["mode"] == PAIR_MODE`:

- Live `SELL_ONLY` logs BUY filled and returns `("rebuild", "done_deal", saved_state["buy_price"])`.
- Live `BUY_ONLY` logs SELL filled and returns `("rebuild", "done_deal", saved_state["sell_price"])`.
- Live `PAIR` with both prices equal to `saved_state` returns `("keep", None, None)`.
- Any other live state returns `("abnormal", None, None)`.

`saved_state["mode"] == BUY_ONLY_MODE`:

- Empty `live_snapshot["orders"]` returns `("rebuild", "done_deal", saved_state["buy_price"])`.
- Live `BUY_ONLY` checks anchor break when `REANCHOR_BREAK` is true and `live_snapshot["btc_mid"] > 0`.
- Anchor break returns `("rebuild", "anchor_break", None)` when `price_distance_at_least(live_snapshot["btc_mid"], live_snapshot["buy_price"], REANCHOR_DISTANCE)` is true.
- Live `BUY_ONLY` without anchor break returns `("keep", None, None)`.
- Any other non-empty live state returns `("abnormal", None, None)`.

`saved_state["mode"] == SELL_ONLY_MODE`:

- Empty `live_snapshot["orders"]` returns `("rebuild", "done_deal", saved_state["sell_price"])`.
- Live `SELL_ONLY` returns `("keep", None, None)`.
- Any other non-empty live state returns `("abnormal", None, None)`.

`BUY_ONLY` and `SELL_ONLY` keep paths do not compare the live one-sided price to `saved_state`.

## Rebuild and Execution

Current execution helpers are `build_pair`, `classify_order_result`, `place_limit_order`, `log_rebuild`, `wait_until`, `cleanup_orders`, `finish_rebuild`, and `rebuild`.

`build_pair(price)`:

- Raises `ValueError("Invalid price")` when `price <= 0`.
- Builds BUY at `price - (BUY_GRID_FACTOR * GRID_STEP)`.
- Builds SELL at `price + (SELL_GRID_FACTOR * GRID_STEP)`.
- Raises `ValueError("Invalid buy price")` when BUY price is `<= 0`.
- Sizes each side as `round(BUDGET_USDC / side_price, 5)`.

`classify_order_result(result)`:

- Reads the first item in `result["response"]["data"]["statuses"]`.
- Returns `"ok"` for `"resting"` or `"filled"`.
- Returns `"insufficient_spot_balance"` only when an error contains `Insufficient spot balance`.
- Returns `"error"` for missing statuses, other errors, or unknown status shapes.

`place_limit_order(trader, action)` calls `trader.order(SYMBOL, action["side"] == "BUY", float(action["size"]), float(action["price"]), {"limit": {"tif": "Gtc"}}, False)` and returns `classify_order_result(result)`.

`wait_until(info, done, max_tries=MAX_RETRIES, interval_sec=WAIT_NO_OPEN_ORDERS_INTERVAL_SEC)` polls `read_orders(info)` until `done(orders)` is true or attempts are exhausted.

`cleanup_orders(info, trader, orders=None)` reads orders when `orders is None`, calls `trader.bulk_cancel` for each `oid`, then uses `wait_until` until no open orders remain. It returns `True` on confirmation and `False` after logging cleanup failure.

`rebuild(info, trader, live_snapshot, strategy="reset", rebuild_price=None)`:

- Uses the one-sided replacement path only when `strategy in ("done_deal", "anchor_break")` and `live_snapshot["mode"] in (BUY_ONLY_MODE, SELL_ONLY_MODE)`.
- In the one-sided replacement path, uses `rebuild_price` when provided, otherwise reads fresh `read_btc_grid(info)`, and treats `live_snapshot["orders"][0]` as the remaining old order.
- In the reset/general path, cancels existing `live_snapshot["orders"]` when present, ignores explicit `rebuild_price`, reads fresh `read_btc_grid(info)`, and rebuilds from that price.

General rebuild path:

- Calls `build_pair(price)` and `log_rebuild`.
- Places BUY and SELL with `place_limit_order`.
- Evaluates BUY status first; if BUY is not `"ok"`, returns `finish_rebuild(..., buy_action, buy_status)`.
- If BUY is `"ok"`, returns `finish_rebuild(..., sell_action, sell_status)`.

One-sided replacement path:

- Calls `build_pair(price)` and `log_rebuild`.
- If the remaining order is SELL, places BUY first, cancels the old SELL, verifies the old `oid` is absent, then places SELL.
- If the remaining order is BUY, places SELL first, cancels the old BUY, verifies the old `oid` is absent, then places BUY.
- If first placement is not `"ok"`, returns `None`.
- Cancel/verify exceptions trigger cleanup and return `None`.
- Failed old-order absence verification triggers cleanup and returns `None`.
- The second placement is handled by `finish_rebuild`.

`finish_rebuild(info, trader, price, buy_action, sell_action, action, status)`:

- `"ok"` returns `PAIR`.
- SELL `insufficient_spot_balance` with `ALLOW_BUY_ONLY_WHEN_NO_BTC` returns `BUY_ONLY`.
- BUY `insufficient_spot_balance` with `ALLOW_SELL_ONLY_WHEN_NO_USDC` returns `SELL_ONLY`.
- Any other status logs partial placement failure, calls `cleanup_orders(info, trader)`, and returns `None`.

## Failure Semantics

`decide_cycle_action` returns `"abnormal"` when no accepted branch matches.

`run_cycle` stops the engine by returning `None` state on abnormal action or rebuild failure.

`main` exits when `saved_state is None`.

`bootstrap` exits with `None` state when its rebuild fails.

There is no global exception shell in `grid.py`.

Uncaught exceptions from reads, price helpers, `build_pair`, order placement outside local handling, missing `oid`, and `read_btc_grid` propagate to the caller.

The one-sided replacement path catches exceptions only around cancel and absence verification.

## Known Limitations

- `price_gap_matches` always returns `True`; `classify_order_mode` does not enforce pair gap equality beyond `buy_price < sell_price`.
- `classify_order_mode` does not validate order size, symbol, `oid`, or notional.
- `PAIR` residual one-sided decisions do not validate the remaining live one-sided price against `saved_state`.
- `BUY_ONLY` keep does not validate live `buy_price` against `saved_state["buy_price"]`.
- `SELL_ONLY` keep does not validate live `sell_price` against `saved_state["sell_price"]`.
- `cleanup_orders` assumes every order being cancelled has `oid`.
- The engine has no global exception recovery loop.
