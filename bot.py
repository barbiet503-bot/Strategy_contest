#!/usr/bin/env python3
"""
bot.py — Contest-ready runner for MarketScanner + HybridTrader
HARDENED VERSION (duplicate-order, zero-size and invalid-result safe)
"""

from __future__ import annotations
import time
import json
import sys
from typing import List, Dict, Optional

from market_scanner import MarketScanner
from trader import HybridTrader
from utils import ManifoldAPI
from strategy import Strategy

def normalize_receipts(receipts: List[Dict]) -> List[Dict]:
    """Ensure receipts have required fields & remove invalid ones."""
    cleaned = []

    for r in receipts:
        if not isinstance(r, dict):
            continue

        # must contain id + size
        if "market_id" not in r or "size" not in r:
            continue

        try:
            size = float(r["size"])
            if size <= 0:
                continue
        except:
            continue

        cleaned.append(r)

    return cleaned


def dedupe_receipts(receipts: List[Dict]) -> List[Dict]:
    """Prevent sending order twice for same market in same loop."""
    seen = set()
    final = []

    for r in receipts:
        mid = r.get("market_id")
        if not mid:
            continue
        if mid in seen:
            continue
        seen.add(mid)
        final.append(r)

    return final


class MikhailBot:
    def __init__(self, config_path: str = "config.json"):

        # Load config
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception as e:
            print("[INIT] Failed to load config.json:", e)
            raise

        api_key = cfg.get("MANIFOLD_API_KEY") or None
        target_creator = cfg.get("TARGET_CREATOR", "MikhailTal")
        self.username = cfg.get("BOT_USERNAME", "ContestMikhailBot")
        self.sleep_time = int(cfg.get("SLEEP_BETWEEN_RUNS", 30))

        # max markets
        try:
            self.max_markets_per_loop = int(cfg.get("MAX_MARKETS_PER_LOOP"))
        except:
            self.max_markets_per_loop = None

        # API
        try:
            self.api = ManifoldAPI(api_key)
            print(f"[INIT] ManifoldAPI initialized ({'REAL' if api_key else 'PAPER'})")
        except Exception as e:
            print("[INIT] API init error, falling back to PAPER:", e)
            self.api = ManifoldAPI(None)

        # Market Scanner
        self.scanner = MarketScanner(api=self.api, creator=target_creator)

        # Strategy
        strategy_creator = (
            cfg.get("TARGET_CREATOR_ID")
            or cfg.get("TARGET_CREATOR")
            or "MikhailTal"
        )
        self.strategy = Strategy(strategy_creator)

        # Trader
        try:
            self.trader = HybridTrader(api=self.api, strategy=self.strategy)
        except TypeError:
            self.trader = HybridTrader(api=self.api)
            print("[INIT] HybridTrader created without strategy (fallback)")

        print(f"[INIT] BOT = {self.username}")
        print(f"[INIT] Creator = {target_creator}")
        print(f"[INIT] Max markets = {self.max_markets_per_loop}")


    def run_once(self) -> List[Dict]:
        """Fetch → evaluate → trade with dedupe & safety filters."""
        receipts: List[Dict] = []

        try:
            markets = self.scanner.get_markets() or []
        except Exception as e:
            print("[ERROR] scanner.get_markets:", e)
            return receipts

        if not markets:
            print("[SCAN] 0 markets")
            return receipts

        if self.max_markets_per_loop:
            markets = markets[: self.max_markets_per_loop]

        print(f"[SCAN] Using {len(markets)} markets")

        # Trader execution
        try:
            # allow multiple method names
            if hasattr(self.trader, "run_once_with_exploration"):
                receipts = self.trader.run_once_with_exploration(markets)
            elif hasattr(self.trader, "run_once"):
                receipts = self.trader.run_once(markets)
            elif hasattr(self.trader, "trade"):
                receipts = self.trader.trade(markets)
            else:
                print("[ERROR] trader has no run method")
                receipts = []
        except Exception as e:
            print("[ERROR] trader failure:", e)
            receipts = []

        #        HARDENING LAYER (MAIN FIX) 

        receipts = normalize_receipts(receipts)
        receipts = dedupe_receipts(receipts)

        if receipts:
            print(f"[RUN] {len(receipts)} valid orders")
        else:
            print("[RUN] No valid orders")

        return receipts

    # -------------------------------------------------------------

    def run(self, loop_interval: Optional[int] = None):

        loop_interval = loop_interval or self.sleep_time

        print(f"[START] Running as {self.username} (every {loop_interval}s)\n")

        while True:
            try:
                self.run_once()
            except Exception as e:
                print("[ERROR] run_once exception:", e)

            print(f"[SLEEP] {loop_interval}s...\n")
            time.sleep(loop_interval)


# ------------------------------------------------------------
# One-shot for testing
# ------------------------------------------------------------

def run_once_and_exit(config_path="config.json"):
    bot = MikhailBot(config_path)
    receipts = bot.run_once()
    print("\n[ONE-RUN] Receipts:")
    for r in receipts:
        print(r)
    return receipts


if __name__ == "__main__":
    bot = MikhailBot()
    bot.run()