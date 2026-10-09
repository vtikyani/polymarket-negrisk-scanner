#!/usr/bin/env python3
"""negRisk consistency scanner: read only, measurement only.

Polymarket groups mutually exclusive outcomes under a shared negRiskMarketID.
Exactly one leg of a group resolves to $1, so the Yes legs must price to 1.00.
When the asks across every leg sum to less than 1.00 you can buy the whole set
and be paid $1 no matter who wins; when the bids sum to more than 1.00 the
inverse holds. Neither requires a view on the event.

This process places NO orders. It imports no signing library and holds no key.
It records what the book looked like so we can answer, from data rather than
intuition: how often does a real gap exist, how large, for how long, and is
there size behind it.

Writes one JSON row per group per scan to data/scans.jsonl.
"""
from __future__ import annotations  # keeps `X | None` hints valid on 3.9

import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from urllib.error import URLError
from urllib.request import Request, urlopen

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, "data")
SCANS = os.path.join(DATA, "scans.jsonl")
CONFIG = os.path.join(ROOT, "config.json")

EVENTS = "https://gamma-api.polymarket.com/events"
BOOKS = "https://clob.polymarket.com/books"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("negrisk")

DEFAULTS = {
    "scan_interval_sec": 120,
    "gamma_page_size": 500,
    "gamma_max_pages": 20,
    "http_timeout_sec": 20,
    "min_legs": 3,
    "book_batch_size": 100,
    "log_all_groups": True,
}


def load_config() -> dict:
    cfg = dict(DEFAULTS)
    if os.path.exists(CONFIG):
        try:
            with open(CONFIG) as f:
                cfg.update(json.load(f))
        except Exception as e:
            log.warning(f"config unreadable, using defaults: {e}")
    return cfg


def get_json(url: str, timeout: int, payload=None):
    data = None
    headers = {"User-Agent": "negrisk-scanner/1.0"}
    if payload is not None:
        data = json.dumps(payload).encode()
        headers["Content-Type"] = "application/json"
    req = Request(url, data=data, headers=headers)
    with urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def fetch_negrisk_groups(cfg: dict) -> dict:
    """group_id -> every leg of that group, via /events.

    MUST come from /events, not the paginated /markets list. The markets list
    hands back an arbitrary slice of a group, and a partial group understates
    the sum of asks, which fabricates an arbitrage that does not exist. The
    first build did exactly that: it reported the 2028 presidential group at
    35 legs on one pass and 50 on the next, and showed a 20-leg Nobel Prize
    group priced at 0.55, an "82% risk-free return" that was purely missing
    legs. /events returned 128 legs for a group /markets called 51.

    Gamma caps limit at 100, so a short page is normal; only an empty page
    ends pagination.
    """
    groups: dict[str, list] = {}
    offset = 0
    for _ in range(cfg["gamma_max_pages"]):
        url = (
            f"{EVENTS}?closed=false&active=true"
            f"&limit={cfg['gamma_page_size']}&offset={offset}"
        )
        try:
            page = get_json(url, cfg["http_timeout_sec"])
        except (URLError, TimeoutError, json.JSONDecodeError) as e:
            log.warning(f"events page at offset {offset} failed: {e}")
            break
        if not page:
            break
        for ev in page:
            gid = ev.get("negRiskMarketID")
            if not gid:
                continue
            legs = [
                m for m in (ev.get("markets") or [])
                if m.get("negRisk") and not m.get("closed")
            ]
            if not legs:
                continue
            groups.setdefault(gid, [])
            seen = {m.get("id") for m in groups[gid]}
            groups[gid].extend(m for m in legs if m.get("id") not in seen)
        offset += len(page)
    return groups


def yes_token(market: dict):
    """clobTokenIds is positionally aligned with outcomes; take the Yes leg."""
    try:
        toks = market.get("clobTokenIds")
        toks = json.loads(toks) if isinstance(toks, str) else toks
        outs = market.get("outcomes")
        outs = json.loads(outs) if isinstance(outs, str) else outs
    except (json.JSONDecodeError, TypeError):
        return None
    if not toks or not outs or len(toks) != len(outs):
        return None
    for i, o in enumerate(outs):
        if str(o).strip().lower() == "yes":
            return str(toks[i])
    return None


def fetch_books(tokens: list, cfg: dict) -> dict:
    """token_id -> book. Batched; a failed batch yields no entries, not a crash."""
    books = {}
    size = cfg["book_batch_size"]
    for i in range(0, len(tokens), size):
        chunk = tokens[i:i + size]
        try:
            res = get_json(
                BOOKS, cfg["http_timeout_sec"],
                payload=[{"token_id": t} for t in chunk],
            )
        except (URLError, TimeoutError, json.JSONDecodeError) as e:
            log.warning(f"book batch {i // size} failed: {e}")
            continue
        for b in res or []:
            tid = b.get("asset_id")
            if tid:
                books[str(tid)] = b
    return books


def best(levels: list):
    """Best price and its size. Both sides arrive worst-first, so the last
    entry is the top of book. Verified against the live API 2026-07-26; do not
    'simplify' to [0] without re-checking."""
    if not levels:
        return None, 0.0
    top = levels[-1]
    try:
        return float(top["price"]), float(top["size"])
    except (KeyError, TypeError, ValueError):
        return None, 0.0


def days_to_resolve(markets: list) -> float | None:
    """Latest endDate across the group's legs, in days from now.

    A gap is meaningless without this. The first live scan found a 5.5 cent
    edge on the 2028 presidential group: real, and worth about 2.2% a year
    once you account for capital locked until 2028, which loses to T-bills.
    Long-dated gaps are the price of tying money up, not an inefficiency.
    """
    latest = None
    for m in markets:
        end = m.get("endDate")
        if not end:
            continue
        try:
            ts = datetime.fromisoformat(str(end).replace("Z", "+00:00"))
        except ValueError:
            continue
        if latest is None or ts > latest:
            latest = ts
    if latest is None:
        return None
    return round((latest - datetime.now(timezone.utc)).total_seconds() / 86400.0, 2)


def annualize(gap: float | None, cost: float | None, days: float | None):
    """Return on capital, annualized. None when it can't be computed."""
    if not gap or gap <= 0 or not cost or cost <= 0 or not days or days <= 0:
        return None
    period_return = gap / cost
    try:
        return round(((1.0 + period_return) ** (365.0 / days)) - 1.0, 4)
    except (OverflowError, ValueError):
        return None


def scan_group(gid: str, markets: list, books: dict) -> dict | None:
    # A group past its end date still shows books, but nothing can be bought
    # into a resolved event. The archived run's largest "profit" ($14.9k) was a
    # group whose end date was 213 days in the past.
    days = days_to_resolve(markets)
    if days is not None and days <= 0:
        return None

    legs, missing = [], 0
    for m in markets:
        tok = yes_token(m)
        book = books.get(tok) if tok else None
        if not book:
            missing += 1
            continue
        ask, ask_sz = best(book.get("asks") or [])
        bid, bid_sz = best(book.get("bids") or [])
        legs.append({
            "q": (m.get("question") or "")[:80],
            "ask": ask, "ask_size": ask_sz,
            "bid": bid, "bid_size": bid_sz,
        })
    if not legs:
        return None

    asks = [l["ask"] for l in legs if l["ask"] is not None]
    bids = [l["bid"] for l in legs if l["bid"] is not None]
    # An incomplete set cannot be arbitraged: a leg with no ask is a leg you
    # cannot buy, so the sum understates the true cost and would fake a gap.
    complete_ask = len(asks) == len(legs) and missing == 0
    complete_bid = len(bids) == len(legs) and missing == 0

    sum_ask = round(sum(asks), 6) if asks else None
    sum_bid = round(sum(bids), 6) if bids else None
    # Equal shares of every leg are required, so the tradeable quantity is the
    # thinnest leg, not the average.
    min_ask_size = min((l["ask_size"] for l in legs if l["ask"] is not None), default=0.0)
    min_bid_size = min((l["bid_size"] for l in legs if l["bid"] is not None), default=0.0)

    buy_gap = round(1.0 - sum_ask, 6) if (complete_ask and sum_ask is not None) else None
    sell_gap = round(sum_bid - 1.0, 6) if (complete_bid and sum_bid is not None) else None

    return {
        "ts": datetime.now(timezone.utc).isoformat(),
        "group": gid,
        "legs": len(legs),
        "days_to_resolve": days,
        "buy_apy": annualize(buy_gap, sum_ask, days),
        "sell_apy": annualize(sell_gap, sum_bid, days),
        "legs_missing_book": missing,
        "neg_risk_other": any(m.get("negRiskOther") for m in markets),
        "sum_best_ask": sum_ask,
        "sum_best_bid": sum_bid,
        "complete_ask": complete_ask,
        "complete_bid": complete_bid,
        "buy_gap": buy_gap,
        "sell_gap": sell_gap,
        "min_ask_size": min_ask_size,
        "min_bid_size": min_bid_size,
        "buy_profit_usd": round(buy_gap * min_ask_size, 4) if buy_gap and buy_gap > 0 else 0.0,
        "sell_profit_usd": round(sell_gap * min_bid_size, 4) if sell_gap and sell_gap > 0 else 0.0,
        "title": legs[0]["q"] if legs else "",
    }


def scan_once(cfg: dict) -> tuple[int, int]:
    groups = fetch_negrisk_groups(cfg)
    groups = {g: ms for g, ms in groups.items() if len(ms) >= cfg["min_legs"]}
    if not groups:
        log.info("no negRisk groups met the leg minimum")
        return 0, 0

    tokens = []
    for ms in groups.values():
        tokens.extend(t for t in (yes_token(m) for m in ms) if t)
    books = fetch_books(tokens, cfg)

    os.makedirs(DATA, exist_ok=True)
    hits = 0
    with open(SCANS, "a") as f:
        for gid, ms in groups.items():
            row = scan_group(gid, ms, books)
            if not row:
                continue
            edge = (row["buy_profit_usd"] or 0) + (row["sell_profit_usd"] or 0)
            if edge > 0:
                hits += 1
                apy = row["buy_apy"] or row["sell_apy"]
                apy_s = f"{apy * 100:.1f}%/yr" if apy is not None else "apy=?"
                log.info(
                    f"GAP {row['title'][:40]} legs={row['legs']} "
                    f"ask_sum={row['sum_best_ask']} profit=${edge:.2f} "
                    f"in {row['days_to_resolve']}d = {apy_s}"
                )
            if cfg["log_all_groups"] or edge > 0:
                f.write(json.dumps(row) + "\n")
    return len(groups), hits


def main():
    once = "--once" in sys.argv[1:]
    log.info(f"negRisk scanner starting (read only), writes {SCANS}")
    while True:
        cfg = load_config()
        t0 = time.time()
        try:
            n, hits = scan_once(cfg)
            log.info(
                f"scan complete: {n} groups, {hits} with a positive gap "
                f"({time.time() - t0:.1f}s)"
            )
        except Exception as e:
            log.error(f"scan failed: {e}")
        if once:
            return
        time.sleep(max(10, cfg["scan_interval_sec"]))


if __name__ == "__main__":
    main()
