#!/usr/bin/env python3
"""Summarize a scan log: how often a gap shows up, how big it is once annualized,
and how much size sits behind it.

    python3 examples/summarize.py examples/hits-archive.jsonl.gz
    python3 examples/summarize.py data/scans.jsonl
"""
import gzip
import json
import statistics
import sys


def rows(path):
    opener = gzip.open if path.endswith(".gz") else open
    with opener(path, "rt") as f:
        for line in f:
            if line.strip():
                yield json.loads(line)


def main(path):
    all_rows = list(rows(path))
    live = [r for r in all_rows if (r.get("days_to_resolve") or 0) > 0]
    hits = [r for r in live if (r["buy_profit_usd"] or 0) + (r["sell_profit_usd"] or 0) > 0]
    apys = sorted(a for a in ((r["buy_apy"] or r["sell_apy"]) for r in hits) if a is not None)
    ts = sorted(r["ts"] for r in all_rows)
    print(f"rows: {len(all_rows)} ({ts[0][:16]} to {ts[-1][:16]} UTC), groups: {len({r['group'] for r in all_rows})}")
    print(f"dropped, past end date: {len(all_rows) - len(live)}")
    print(f"rows with a positive gap: {len(hits)}")
    if apys:
        print(f"median annualized gap: {statistics.median(apys):.1%}")
    worth = [r for r in hits
             if (r["buy_apy"] or r["sell_apy"] or 0) > 0.20
             and (r["buy_profit_usd"] or 0) + (r["sell_profit_usd"] or 0) >= 5]
    groups = {}
    for r in worth:
        groups.setdefault(r["group"], []).append(r)
    print(f"rows over 20%/yr with $5+ behind them: {len(worth)}, in {len(groups)} groups")
    for rs in groups.values():
        r = max(rs, key=lambda r: (r["buy_profit_usd"] or 0) + (r["sell_profit_usd"] or 0))
        side = "buy" if (r["buy_profit_usd"] or 0) > 0 else "sell"
        gap = r["buy_gap"] if side == "buy" else r["sell_gap"]
        profit = r["buy_profit_usd"] if side == "buy" else r["sell_profit_usd"]
        print(f"  {r['title'][:60]}: {side} gap {gap:.3f}, {r['days_to_resolve']}d, best ${profit:.2f} ({len(rs)} rows)")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "examples/hits-archive.jsonl.gz")
