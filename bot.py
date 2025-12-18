#!/usr/bin/env python3
"""
bot.py —  runner for MarketScanner + HybridTrader

• Trades ONLY markets by creator: MikhailTal
• Restart-safe state (no permanent locks)
• Duplicate & spam trade protection
• Clean, readable, contest-grade
"""

from __future__ import annotations

import os
import time
import json
import logging
from typing import List, Dict

from market_scanner import MarketScanner
from trader import HybridTrader
from utils import ManifoldAPI


# ---------------- LOGGING ----------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger("ContestMikhailBot")


# ---------------- STATE ----------------

STATE_FILE = "state.json"


def load_state() -> Dict[str, Dict]:
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(state: Dict[str, Dict]):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


def is_market_locked(
    state: Dict[str, Dict],
    market_id: str,
    cooldown: int,
    pending_cooldown: int,
) -> bool:
    entry = state.get(market_id)
    if not entry:
        return False

    status = entry.get("status", "")
    age = time.time() - entry.get("timestamp", 0)

    # Pending orders → short temporary lock
    if status == "pending":
        return age < pending_cooldown

    # Submitted / filled → cooldown only (NO permanent lock)
    return age < cooldown


#  RECEIPT HARDENING

def normalize_receipts(receipts: List[Dict]) -> List[Dict]:
    cleaned: List[Dict] = []
    for r in receipts:
        if not isinstance(r, dict):
            continue

        mid = r.get("market_id")
        size = r.get("size")

        if not mid:
            continue

        try:
            size = float(size)
            if size <= 0:
                continue
        except Exception:
            continue

        cleaned.append(r)

    return cleaned


def dedupe_receipts(receipts: List[Dict]) -> List[Dict]:
    seen = set()
    final: List[Dict] = []

    for r in receipts:
        mid = r.get("market_id")
        if not mid or mid in seen:
            continue
        seen.add(mid)
        final.append(r)

    return final


# ---------------- MAIN BOT ----------------

class MikhailBot:
    def __init__(self, config_path: str = "config.json"):

        # Load config
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception as exc:
            raise RuntimeError(f"[INIT] Failed to load config.json: {exc}")

        self.cfg = cfg
        self.username = cfg.get("BOT_USERNAME", "ContestMikhailBot")
        self.target_creator = cfg.get("TARGET_CREATOR", "MikhailTal")

        self.sleep_time = int(cfg.get("SLEEP_BETWEEN_RUNS", 30))
        self.max_markets = cfg.get("MAX_MARKETS_PER_LOOP")

        # Correct keys
        self.cooldown = int(cfg.get("TRADE_COOLDOWN", 45))
        self.pending_cooldown = int(cfg.get("PENDING_COOLDOWN_SECONDS", 120))

        # API
        api_key = os.getenv("MANIFOLD_API_KEY")
        if not api_key:
            raise RuntimeError("MANIFOLD_API_KEY not set")

        self.api = ManifoldAPI(api_key)
        logger.info("[INIT] ManifoldAPI initialized")

        # Components
        self.scanner = MarketScanner(api=self.api, creator=self.target_creator)
        self.trader = HybridTrader(api=self.api)

        logger.info("[INIT] BOT USERNAME    : %s", self.username)
        logger.info("[INIT] TARGET CREATOR : %s", self.target_creator)
        logger.info("[INIT] COOLDOWN       : %ss", self.cooldown)


    def run_once(self) -> List[Dict]:
        receipts: List[Dict] = []

        # Fetch markets
        try:
            markets = self.scanner.get_markets() or []
        except Exception as exc:
            logger.error("[SCAN] Failed: %s", exc)
            return receipts

        if not markets:
            logger.info("[SCAN] 0 markets found")
            return receipts

        if self.max_markets:
            markets = markets[: int(self.max_markets)]

        state = load_state()
        now = int(time.time())

        # Refresh pending orders
        for mid, entry in list(state.items()):
            if entry.get("status") == "pending" and entry.get("order_id"):
                try:
                    order = self.api.get_order(entry["order_id"])
                    status = order.get("status")
                except Exception:
                    status = None

                if status and status != "pending":
                    entry["status"] = status
                    entry["timestamp"] = now
                    logger.info("[STATE] %s → %s", mid, status)

        # Filter tradable markets
        tradable: List[Dict] = []
        for m in markets:
            mid = m.get("id")
            if not mid:
                continue

            if is_market_locked(state, mid, self.cooldown, self.pending_cooldown):
                continue

            tradable.append(m)

        if not tradable:
            logger.info("[RUN] No tradable markets (cooldown)")
            save_state(state)
            return receipts

        # Execute trades
        try:
            receipts = self.trader.run_once_with_exploration(tradable)
        except Exception as exc:
            logger.error("[TRADE] Failed: %s", exc)
            save_state(state)
            return receipts

        receipts = dedupe_receipts(normalize_receipts(receipts))

        # Update state
        for r in receipts:
            mid = r["market_id"]
            state[mid] = {
                "timestamp": now,
                "status": r.get("status", "submitted"),
                "size": r.get("size"),
                "outcome": r.get("outcome"),
                "order_id": r.get("order_id"),
            }

            logger.info(
                "[TRADE] %s | %s %.2f (%s)",
                mid,
                r.get("outcome"),
                r.get("size", 0),
                r.get("status"),
            )

        save_state(state)

        if receipts:
            logger.info("[RUN] %d clean trade(s) placed", len(receipts))
        else:
            logger.info("[RUN] No valid trades (edge too small / abstained)")

        return receipts


    def run(self):
        logger.info("[START] %s running every %ds", self.username, self.sleep_time)
        while True:
            try:
                self.run_once()
            except Exception as exc:
                logger.error("[LOOP] Unexpected error: %s", exc)
            time.sleep(self.sleep_time)


# ---------------- ENTRYPOINT ----------------

if __name__ == "__main__":
    bot = MikhailBot()
    bot.run()