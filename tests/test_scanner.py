"""Offline tests for the pure parts of scanner.py. Run: python3 -m unittest"""
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import scanner  # noqa: E402


def end_in(days: float) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()


def market(i: int, days: float = 30) -> dict:
    return {
        "id": str(i),
        "question": f"Leg {i}?",
        "outcomes": '["Yes", "No"]',
        "clobTokenIds": f'["yes{i}", "no{i}"]',
        "endDate": end_in(days),
    }


def book(ask: float, bid: float, size: float = 100.0) -> dict:
    # The API lists both sides worst-first, so the top of book is the last entry.
    return {
        "asks": [{"price": "0.99", "size": "1"}, {"price": str(ask), "size": str(size)}],
        "bids": [{"price": "0.01", "size": "1"}, {"price": str(bid), "size": str(size)}],
    }


class TestPrimitives(unittest.TestCase):
    def test_best_takes_last_level(self):
        self.assertEqual(scanner.best([{"price": "0.9", "size": "1"}, {"price": "0.3", "size": "7"}]), (0.3, 7.0))
        self.assertEqual(scanner.best([]), (None, 0.0))

    def test_yes_token_follows_outcome_position(self):
        m = {"outcomes": '["No", "Yes"]', "clobTokenIds": '["n", "y"]'}
        self.assertEqual(scanner.yes_token(m), "y")
        self.assertIsNone(scanner.yes_token({"outcomes": '["Yes"]', "clobTokenIds": '["a", "b"]'}))

    def test_annualize(self):
        # 1% over 365 days is 1% a year; the same gap over 36.5 days compounds to about 10.5%.
        self.assertAlmostEqual(scanner.annualize(0.01, 1.0, 365), 0.01, places=4)
        self.assertAlmostEqual(scanner.annualize(0.01, 1.0, 36.5), 0.1046, places=3)
        self.assertIsNone(scanner.annualize(-0.01, 1.0, 30))
        self.assertIsNone(scanner.annualize(0.01, 1.0, 0))


class TestScanGroup(unittest.TestCase):
    def test_buy_gap_sized_by_thinnest_leg(self):
        ms = [market(i) for i in range(3)]
        books = {"yes0": book(0.30, 0.25, 50), "yes1": book(0.30, 0.25, 10), "yes2": book(0.30, 0.25, 80)}
        row = scanner.scan_group("g", ms, books)
        self.assertAlmostEqual(row["buy_gap"], 0.10)
        self.assertEqual(row["min_ask_size"], 10.0)
        self.assertAlmostEqual(row["buy_profit_usd"], 1.0)
        self.assertIsNotNone(row["buy_apy"])

    def test_missing_leg_is_not_an_arbitrage(self):
        # Two of three legs priced at 0.3 sum to 0.6: a fake 40c gap if the missing leg is ignored.
        ms = [market(i) for i in range(3)]
        books = {"yes0": book(0.30, 0.25), "yes1": book(0.30, 0.25)}
        row = scanner.scan_group("g", ms, books)
        self.assertFalse(row["complete_ask"])
        self.assertIsNone(row["buy_gap"])
        self.assertEqual(row["buy_profit_usd"], 0.0)

    def test_expired_group_is_skipped(self):
        ms = [market(i, days=-10) for i in range(3)]
        books = {f"yes{i}": book(0.10, 0.05) for i in range(3)}
        self.assertIsNone(scanner.scan_group("g", ms, books))


if __name__ == "__main__":
    unittest.main()
