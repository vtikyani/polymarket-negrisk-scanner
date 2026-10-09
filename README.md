# polymarket-negrisk-scanner

A read-only scanner for pricing gaps in Polymarket's multi-outcome markets. It places no orders, imports no signing library, and holds no key.

Polymarket groups mutually exclusive outcomes ("Who wins the 2028 election?") under a shared `negRiskMarketID`. Exactly one leg pays $1, so the Yes prices across the group should sum to 1.00. When the best asks across every leg sum under 1.00, buying one share of each leg pays $1 whatever happens. When the best bids sum over 1.00, the reverse holds. Neither needs a view on the event.

I built this to answer one question from data before writing any trading code: how often does a real gap exist, how big is it once you account for time, and how much size sits behind it. Answer: almost never enough to trade. I ran it for six days and shut it down. The numbers are below.

## Results from the archived run

`examples/hits-archive.jsonl.gz` is every positive-gap row from July 27 to August 1, 2026 (22,910 rows, 43 groups). `python3 examples/summarize.py` reproduces this:

```
rows: 22910 (2026-07-27T00:20 to 2026-08-01T17:15 UTC), groups: 43
dropped, past end date: 177
rows with a positive gap: 22733
median annualized gap: 3.4%
rows over 20%/yr with $5+ behind them: 335, in 2 groups
  Will the Fed decrease interest rates by 50+ bps after the Ju: sell gap 0.005, 1.32d, best $134.11 (333 rows)
  Will the Labour Party win fewer than 30 seats in the New Zea: buy gap 0.068, 102.51d, best $11.90 (2 rows)
```

The median gap annualizes to 3.4%, under the T-bill rate. Of the two groups that cleared 20% a year with at least $5 behind them, one was a half-cent sell gap that needs a minted set and is eaten by fees, and the other was worth $11.90.

## Three traps, all hit on the first build

**Partial groups fabricate arbitrage.** Group legs must come from `/events`, never from the paginated `/markets` list, which returns an arbitrary slice of a group. A missing leg understates the sum and invents free money. The first build reported a 20-leg Nobel Prize group at 0.55, an apparent 82% risk-free return made entirely of absent legs. `/events` returned 128 legs for a group `/markets` called 51.

**A raw gap is not a return.** The first real hit was 5.5 cents on the 2028 presidential group, about 2.2% a year once capital is locked until 2028. Rank by `buy_apy` and `sell_apy`, never by `buy_gap`.

**Expired groups still quote.** A group past its end date can still show a book. The largest "profit" in the archive, $14,925, came from a group whose end date was 213 days in the past. `scan_group` now skips any group with `days_to_resolve <= 0`.

## Run it

Python 3.9+, standard library only.

```bash
python3 scanner.py --once     # one scan, then exit
python3 scanner.py            # scan every scan_interval_sec (config.json)
python3 -m unittest discover -s tests
python3 examples/summarize.py data/scans.jsonl
```

Each scan writes one JSON row per group to `data/scans.jsonl`. A full scan of about 730 groups takes roughly 25 seconds.

## Reading a row

- `buy_gap` > 0: asks sum under 1, buy every leg.
- `sell_gap` > 0: bids sum over 1, sell every leg (needs a minted set).
- `complete_ask` / `complete_bid`: false means a leg had no book, so the sum isn't tradeable and the gap is reported as null.
- `min_ask_size` / `min_bid_size`: you need equal shares of every leg, so the thinnest leg caps the trade. `buy_profit_usd` is the gap times that size, before fees.
- `buy_apy` / `sell_apy`: the gap as a return on capital, compounded to a year using `days_to_resolve`.

## License

MIT
