#!/usr/bin/env python3
# trader.py - HybridTrader

from __future__ import annotations
import os
import time
import json
import math
import random
import logging
from typing import Dict, List, Optional, Any, Tuple
import numpy as np


# Logging setup 

logger = logging.getLogger("HybridTrader")

if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter("%(asctime)s | %(levelname)s | %(message)s")
    )
    logger.addHandler(handler)

logger.setLevel(logging.INFO) 


# Config load (HARDENED & SAFE)

CONFIG_PATH = os.environ.get("MANIFOLD_BOT_CONFIG", "config.json")
CONFIG: Dict[str, Any] = {}

if os.path.exists(CONFIG_PATH):
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
            raw = fh.read().strip()

        if not raw:
            raise ValueError("config.json is empty")

        CONFIG = json.loads(raw)

        if not isinstance(CONFIG, dict):
            raise ValueError("config.json root must be an object")

        logger.info("Config loaded from %s", CONFIG_PATH)

    except json.JSONDecodeError as e:
        logger.error("Invalid JSON in config.json: %s (using defaults)", e)
        CONFIG = {}

    except Exception as e:
        logger.error("Failed to load config.json: %s (using defaults)", e)
        CONFIG = {}
else:
    logger.warning("Config file not found: %s (using defaults)", CONFIG_PATH)


# Identity & hyperparameters (BOUNDED & SAFE)

# ================= BOT IDENTITY =================

PENDING_COOLDOWN_SECONDS: int = int(
    CONFIG.get("PENDING_COOLDOWN_SECONDS", 120)
)

BOT_USERNAME: str = str(
    CONFIG.get("BOT_USERNAME", "ContestMikhailBot")
)


# ================= EMA TUNING =================

EMA_WINDOW: int = max(3, int(CONFIG.get("EMA_WINDOW", 4)))
SHORT_EMA: int = max(2, int(CONFIG.get("SHORT_EMA", 2)))


# ================= EDGE THRESHOLDS =================
# Conservative baseline, adaptive by liquidity

BASE_EDGE_THRESHOLD: float = max(
    0.0012,
    float(CONFIG.get("BASE_EDGE_THRESHOLD", 0.0015))
)

LOW_LIQ_EDGE: float = max(
    0.0010,
    float(CONFIG.get("MIN_EDGE_LOW_LIQUIDITY", 0.0012))
)

MIN_EDGE_FOR_MOMENTUM: float = max(
    BASE_EDGE_THRESHOLD * 1.05,
    float(CONFIG.get("MIN_EDGE_FOR_MOMENTUM", BASE_EDGE_THRESHOLD * 1.1))
)


# ================= BET SIZING =================

BASE_BET: float = max(0.5, float(CONFIG.get("BASE_BET", 3.0)))
MAX_BET: float = max(BASE_BET, float(CONFIG.get("MAX_BET", 12.0)))

RISK_SCALE: float = min(
    1.2, max(0.4, float(CONFIG.get("RISK_SCALE", 0.85)))
)


# ================= EXPLORATION =================

USE_EXPLORATION: bool = bool(CONFIG.get("ENABLE_EXPLORATION", True))

EXPLORATION_RATE: float = min(
    0.40, max(0.10, float(CONFIG.get("EXPLORATION_RATE", 0.30)))
)

EXPLORATION_SIZE: float = max(
    1.1, float(CONFIG.get("EXPLORATION_SIZE", 1.25))
)


# ================= COOLDOWN =================

TRADE_COOLDOWN: float = max(
    30.0, float(CONFIG.get("TRADE_COOLDOWN", 30.0))
)


# ================= LLM (SOFT SIGNAL ONLY) =================

OPENAI_API_KEY: str = str(CONFIG.get("OPENAI_API_KEY", "")).strip()
OPENAI_MODEL: str = str(CONFIG.get("OPENAI_MODEL", "gpt-4o-mini"))

LLM_WEIGHT: float = min(
    0.45, max(0.15, float(CONFIG.get("LLM_WEIGHT", 0.25)))
)

LLM_CONFIDENCE_THRESHOLD: float = min(
    0.6, max(0.25, float(CONFIG.get("LLM_CONFIDENCE_THRESHOLD", 0.32)))
)


# ================= DEBUG =================

DEBUG_MODE: bool = bool(CONFIG.get("DEBUG_MODE", False))
logger.setLevel(logging.DEBUG if DEBUG_MODE else logging.INFO)


# ================= DYNAMIC EDGE LOGIC =================

def effective_edge_threshold(market: dict) -> float:
    """
    Dynamic edge threshold based on liquidity.
    Conservative by default, permissive only where safe.
    """
    volume = market.get("volume", 0) or 0

    if volume < 200:
        return LOW_LIQ_EDGE
    elif volume < 500:
        return BASE_EDGE_THRESHOLD * 0.9
    else:
        return BASE_EDGE_THRESHOLD
        
openai = None
if OPENAI_API_KEY:
    try:
        import openai  # type: ignore

        openai.api_key = OPENAI_API_KEY
    except Exception as e:
        logger.warning("OpenAI disabled: %s", e)
        openai = None

# External helpers from utils.py 

try:
    from utils import (
        get_market_current_prob,
        get_recent_market_probs,
        place_order,
        log_trade,
        safe_ema,
        compute_volatility,
        pseudo_llm_predict,
        llm_estimate_probability,
        clipped,
    )
except ImportError:
    
    
    #  paper-mode fallbacks 
    def get_market_current_prob(market_id: str) -> Optional[float]:
        return None

    def get_recent_market_probs(market_id: str, n: int = 50) -> List[float]:
        return []

    def place_order(
        market_id: str,
        outcome: str,
        size: float,
        reason: str = "",
    ) -> Dict[str, Any]:
        now = time.time()
        return {
            "order_id": f"paper-{int(now * 1000)}",
            "status": "filled",
            "mode": "paper",
            "market_id": market_id,
            "outcome": outcome,
            "size": size,
            "reason": reason,
            "timestamp": now,
        }

    def log_trade(record: dict):
        TRADES_CSV = os.path.join(os.getcwd(), "trades.csv")
        header = [
            "timestamp",
            "market_id",
            "outcome",
            "size",
            "mode",
            "reason",
            "bot",
            "order_id",
            "status",
        ]
        exists = os.path.exists(TRADES_CSV)

        try:
            import csv

            with open(TRADES_CSV, "a", newline="", encoding="utf-8") as fh:
                writer = csv.DictWriter(fh, fieldnames=header)
                if not exists:
                    writer.writeheader()
                writer.writerow(
                    {
                        "timestamp": record.get("timestamp", time.time()),
                        "market_id": record.get("market_id"),
                        "outcome": record.get("outcome"),
                        "size": record.get("size"),
                        "mode": record.get("mode", "paper"),
                        "reason": record.get("reason"),
                        "bot": record.get("bot"),
                        "order_id": record.get("order_id", ""),
                        "status": record.get("status", ""),
                    }
                )
        except Exception as e:
            logger.debug("Failed to write trades.csv: %s", e)

    def safe_ema(values: List[float], default: float = 0.5) -> float:
        try:
            if not values:
                return float(default)
            n = len(values)
            if n == 1:
                return float(values[0])
            alpha = 2.0 / (n + 1.0)
            v = float(values[0])
            for x in values[1:]:
                v = alpha * float(x) + (1.0 - alpha) * v
            return float(v)
        except Exception:
            return float(default)

    def compute_volatility(series: List[float], window: int = 8) -> float:
        try:
            if not series:
                return 0.0
            w = max(1, min(window, len(series)))
            arr = np.array(series[-w:], dtype=float)
            return float(np.std(arr, ddof=0))
        except Exception:
            return 0.0

    def pseudo_llm_predict(question: str) -> Tuple[float, float]:
        h = abs(hash(question)) % 1000000
        base = 0.35 + ((h % 350_000) / 1_000_000.0)
        conf = 0.55 + ((h % 200_000) / 1_000_000.0)
        jitter = ((h % 97) - 48) / 1000.0
        return max(0.01, min(0.99, base + jitter)), max(0.2, min(0.99, conf))

    def llm_estimate_probability(question: str, recent_changes: Optional[List[float]] = None) -> Tuple[Optional[float], float]:
        return pseudo_llm_predict(question)

    def clipped(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
        try:
            return max(lo, min(hi, float(x)))
        except Exception:
            return float(lo)

#  Small numeric helpers(HARDENED) 
def logistic(x: float) -> float:
    try:
        if x >= 0:
            z = math.exp(-x)
            return 1.0 / (1.0 + z)
        else:
            z = math.exp(x)
            return z / (1.0 + z)
    except Exception:
        return 1.0 / (1.0 + math.exp(-max(-50.0, min(50.0, x))))

def series_is_flat(series: List[float], n: int = 3, tol: float = 0.001) -> bool:
    try:
        if len(series) < n:
            return True
        tail = series[-n:]
        base = tail[0]
        return all(abs(x - base) <= tol for x in tail)
    except Exception:
        return True

# Core HybridTrader 
class HybridTrader:
    def __init__(self, api: Optional[Any] = None, seed: Optional[int] = None):
        self.api = api
        self.bot = BOT_USERNAME

        if seed is None:
            seed = getattr(api, "seed", None) or int(CONFIG.get("SEED", 42))
        self.seed = int(seed)
        self._rng = random.Random(self.seed)

        # internal state
        self._history: Dict[str, List[float]] = {}
        self._last_trade_ts: Dict[str, float] = {}
        self._last_trade_dir: Dict[str, str] = {}
        self._positions: Dict[str, float] = {}

        # pending order tracking
        self._pending_markets: set[str] = set()

        if DEBUG_MODE:
            logger.debug("HybridTrader initialized (seed=%d)", self.seed)

    # history helpers 
    def _add_history(self, market_id: str, prob: float, max_len: int = EMA_WINDOW + 12):
        arr = self._history.get(market_id, [])
        arr.append(float(prob))
        if len(arr) > max_len:
            arr = arr[-max_len:]
        self._history[market_id] = arr

    def _last_history(self, market_id: str) -> List[float]:
      return self._history.get(market_id, [])

    # trade cooldown & duplicate protection
    def _compute_dynamic_cooldown(
    self,
    features: Optional[Dict[str, float]]
) -> float:
      base = float(CONFIG.get("TRADE_COOLDOWN", 180.0))

      if not features:
        return base

      vol = float(features.get("vol", 0.0))
      scaled = base * (1.0 + min(1.5, vol * 4.0))

      return float(min(600.0, max(45.0, scaled)))

    #trade cooldown & duplicate protection 
    def _can_trade(
    self,
    market_id: str,
    direction: str,
    features: Optional[Dict[str, float]] = None,
) -> bool:
      now = time.time()

      last_ts = self._last_trade_ts.get(market_id)
      last_dir = self._last_trade_dir.get(market_id)

      # dynamic cooldown
      cooldown = self._compute_dynamic_cooldown(features)

      if last_ts is not None:
        elapsed = now - last_ts

        if elapsed < cooldown:
            if DEBUG_MODE:
                logger.debug(
                    "[COOLDOWN] %s blocked %.1fs < %.1fs",
                    market_id, elapsed, cooldown
                )
            return False

        if last_dir == direction and elapsed < cooldown * 2.2:
            if DEBUG_MODE:
                logger.debug(
                    "[DUPLICATE] %s %s blocked (%.1fs)",
                    market_id, direction, elapsed
                )
            return False

      exposure = float(self._positions.get(market_id, 0.0))
      max_exp = float(CONFIG.get("MAX_EXPOSURE_PER_MARKET", 300.0))

      if exposure >= max_exp:
        if DEBUG_MODE:
            logger.debug(
                "[EXPOSURE] %s %.1f >= %.1f",
                market_id, exposure, max_exp
            )
        return False

      return True
    #  trade bookkeeping
    def _record_trade(
    self,
    market_id: str,
    direction: str,
    size: float,
    confirmed: bool = False,
):
      """
    Record trade exposure safely.

    Rules:
    - Pending trades → temporary lock (NO cooldown yet)
    - Confirmed trades → permanent lock + cooldown
    - One-position-per-market enforced
      """

      if not market_id or size <= 0:
        return

      now = time.time()

      #  Absolute exposure (one position only) 
      if market_id in self._positions:
        logger.debug(
            "[RECORD] %s already has exposure → skip update",
            market_id
        )
        return

      #  Hard exposure cap 
      max_exposure = float(CONFIG.get("MAX_BET", 50.0)) * 2.0
      if size > max_exposure:
        logger.warning(
            "[RISK] Trade rejected %s size %.2f > cap %.2f",
            market_id, size, max_exposure
        )
        return

      #  Record exposure 
      self._positions[market_id] = float(size)

    #  Pending lock ALWAYS 
      self._pending_markets.add(market_id)

      #  Cooldown ONLY if confirmed 
      if confirmed:
        self._last_trade_ts[market_id] = now
        self._last_trade_dir[market_id] = direction

        # confirmed → no longer pending
        self._pending_markets.discard(market_id)

        logger.debug(
            "[RECORD] Confirmed %s %s %.2f",
            market_id, direction, size
        )
      else:
        logger.debug(
            "[RECORD] Pending %s %s %.2f",
            market_id, direction, size
        )
          
    def compute_model_p(
    self,
    market: Dict,
    recent: List[float],
    market_orderbook: Optional[Dict] = None,
) -> Tuple[float, Dict[str, float]]:

    # -------- market probability --------
      try:
        market_p = market.get("probability")
        if market_p is None and recent:
            market_p = recent[-1]
        if market_p is None:
            market_p = get_market_current_prob(market.get("id") or "")
        market_p = clipped(float(market_p), 0.001, 0.999)
      except Exception:
        market_p = clipped(float(recent[-1]) if recent else 0.5, 0.001, 0.999)

    # -------- history safety --------
      if not recent or len(recent) < 3:
        recent = [market_p] * (EMA_WINDOW + 2)

      # -------- signals --------
      momentum = recent[-1] - recent[-2]
      ema_short = safe_ema(recent[-SHORT_EMA:], default=market_p)
      ema_long = safe_ema(recent[-EMA_WINDOW:], default=market_p)
      mean_rev = ema_short - ema_long

      vol = compute_volatility(recent, window=min(len(recent), EMA_WINDOW))
      vol = max(vol, 1e-4)

      # -------- orderbook --------
      ob_imb = 0.0
      if market_orderbook:
        try:
            bid = market_orderbook.get("bid")
            ask = market_orderbook.get("ask")
            if bid is not None and ask is not None:
                spread = max(1e-6, abs(ask - bid))
                ob_imb = clipped((bid - ask) / spread, -0.5, 0.5)
        except Exception:
            pass

      # -------- liquidity --------
      volume = float(market.get("volume") or 0.0)
      liquidity = float(market.get("liquidity") or volume)

      # -------- LLM (SOFT) --------
      question = market.get("question") or ""
      llm_p, llm_conf = llm_estimate_probability(question, recent_changes=recent)
      if llm_p is None:
        llm_p, llm_conf = pseudo_llm_predict(question)

      llm_p = clipped(llm_p, 0.001, 0.999)
      llm_conf = clipped(llm_conf, 0.0, 1.0)

      # -------- weighted delta --------
      raw_delta = (
        0.28 * momentum +
        0.45 * mean_rev +
        0.10 * ob_imb -
        0.18 * vol +
        0.30 * (llm_p - market_p) * llm_conf * LLM_WEIGHT
    )

      # -------- bounded shift (IMPORTANT CHANGE) --------
      delta = math.tanh(raw_delta * 1.8) * 0.18
      model_p = clipped(market_p + delta, 0.001, 0.999)

      features = {
        "momentum": momentum,
        "meanrev": mean_rev,
        "vol": vol,
        "liquidity": liquidity,
        "raw_delta": raw_delta,
        "delta": delta,
        "llm_conf": llm_conf,
    }

      # SOFT anti-noise (ALLOW SMALL EDGE)
      if abs(delta) < 0.0006:
        model_p = clipped(
            market_p + math.copysign(0.0006, raw_delta),
            0.001,
            0.999
        )

      return float(model_p), features
    #  bet sizing: Kelly-like but capped (FIXED)
    def compute_bet_size(
    self,
    market_p: float,
    model_p: float,
    edge: float,
    features: Dict[str, float],
) -> float:

      abs_edge = abs(float(edge))

      # allow tiny edges (exploration handled upstream)
      if abs_edge < 0.0008:
        return 0.0

      market_p = clipped(float(market_p), 0.02, 0.98)
      denom = max(0.03, market_p * (1.0 - market_p))

      # Tempered Kelly
      kelly_frac = (abs_edge / denom) * 0.14 * RISK_SCALE
      kelly_frac = min(0.35, max(0.025, kelly_frac))

      size = BASE_BET * (1.0 + kelly_frac * 4.0)

      # Volatility dampener
      vol = float(features.get("vol", 0.0))
      vol_penalty = 1.0 - min(0.45, vol * 1.2)
      size *= max(0.65, vol_penalty)

      # Liquidity adjustment
      liq = float(features.get("liquidity", 0.0))
      if liq > 180:
        size *= 1.10
      elif liq < 30:
        size *= 0.90

      size = min(size, MAX_BET)
      size = round(size, 2)

      if DEBUG_MODE:
        logger.debug(
            "[SIZE] edge=%.4f k=%.3f vol=%.3f liq=%.1f size=%.2f",
            abs_edge, kelly_frac, vol, liq, size
        )

      return float(size)
      
    def _evaluate_market(self, market: Dict) -> Optional[Dict]:
      try:
        # -------- market id --------
        mid = (
            market.get("id")
            or market.get("slug")
            or market.get("market_id")
            or (market.get("raw") or {}).get("id")
        )
        if not mid:
            return None

        # -------- market probability --------
        market_p = market.get("probability")
        if market_p is None:
            market_p = get_market_current_prob(mid)
        if market_p is None:
            return None

        market_p = clipped(float(market_p), 0.001, 0.999)

        # -------- history --------
        recent: List[float] = []

        if isinstance(market.get("history"), list):
            recent = [float(x) for x in market["history"] if x is not None]

        if not recent:
            recent = get_recent_market_probs(mid, n=EMA_WINDOW + 6) or []

        if not recent:
            recent = self._last_history(mid) or []

        if not recent:
            recent = [market_p] * (EMA_WINDOW + 2)

        if recent[-1] != market_p:
            recent.append(market_p)

        self._add_history(mid, market_p, max_len=EMA_WINDOW + 12)

        # -------- volatility & liquidity --------
        vol = compute_volatility(recent, window=min(len(recent), EMA_WINDOW))
        vol = max(1e-4, vol)

        liquidity = float(
            market.get("liquidity")
            or market.get("totalLiquidity")
            or market.get("volume")
            or 0.0
        )

        # -------- orderbook --------
        ob = {}
        try:
            if market.get("bestBid") is not None:
                ob["bid"] = float(market["bestBid"])
            if market.get("bestAsk") is not None:
                ob["ask"] = float(market["bestAsk"])
        except Exception:
            ob = {}

        # -------- model --------
        model_p, features = self.compute_model_p(
            market,
            recent,
            market_orderbook=ob,
        )
        model_p = clipped(model_p, 0.001, 0.999)

        features["vol"] = vol
        features["liquidity"] = liquidity

        edge = model_p - market_p
        abs_edge = abs(edge)

        # DYNAMIC EDGE THRESHOLD (KEY FIX)
        base_edge = effective_edge_threshold(market)

        # soften volatility effect (NO EDGE KILL)
        vol_factor = 1.0 + min(0.20, vol * 0.5)

        # liquidity adjustment
        if liquidity < 20:
            liq_factor = 1.10
        elif liquidity > 200:
            liq_factor = 0.90
        else:
            liq_factor = 1.0

        dynamic_thresh = base_edge * vol_factor * liq_factor

        # exploration (EARLIER + SAFER) 
        exploring = (
            USE_EXPLORATION
            and liquidity > 10
            and self._rng.random() < EXPLORATION_RATE
        )

        if abs_edge < dynamic_thresh and not exploring:
            return None

        # -------- momentum assist --------
        momentum = float(features.get("momentum", 0.0))
        if abs(momentum) > 0.002:
            abs_edge *= 1.15

        abs_edge = min(abs_edge, 0.30)

        side = "BUY" if edge > 0 else "SELL"

        # -------- size --------
        size = self.compute_bet_size(market_p, model_p, edge, features)

        if exploring:
            size = max(1.0, size * EXPLORATION_SIZE)

        if size < 0.9:
            return None

        return {
            "market_id": mid,
            "side": side,
            "size": float(size),
            "model_p": model_p,
            "market_p": market_p,
            "edge": edge,
            "explore": exploring,
            "reason": "edge+adaptive-momentum" + ("+explore" if exploring else ""),
        }

      except Exception as exc:
        logger.debug(
            "Exception in _evaluate_market(%s): %s",
            market.get("id"), exc
        )
        return None
    
    def _execute_decision(self, decision: Dict) -> Optional[Dict]:
      mid = decision.get("market_id")
      side = decision.get("side")
      size = float(decision.get("size", 0.0))
      reason = decision.get("reason", "hybrid-strategy")

      # -------- basic validation --------
      if not mid or side not in ("BUY", "SELL") or size <= 0:
        logger.debug("[EXEC] Invalid decision skipped: %s", decision)
        return None

      now = time.time()

      # -------- pending expiry guard --------
      if not hasattr(self, "_pending_expiry"):
        self._pending_expiry = {}

      if mid in self._pending_markets:
        ts = self._pending_expiry.get(mid, 0.0)

        if now - ts < PENDING_COOLDOWN_SECONDS:
            if DEBUG_MODE:
                logger.debug(
                    "[PENDING] %s blocked %.1fs < %.1fs",
                    mid, now - ts, PENDING_COOLDOWN_SECONDS
                )
            return None

        self._pending_markets.discard(mid)
        self._pending_expiry.pop(mid, None)

        if DEBUG_MODE:
            logger.debug("[UNLOCK] Pending expired for %s", mid)

      # -------- exposure guard --------
      max_exposure = float(CONFIG.get("MAX_EXPOSURE_PER_MARKET", 300.0))
      exposure = float(self._positions.get(mid, 0.0))

      if exposure >= max_exposure:
        if DEBUG_MODE:
            logger.debug(
                "[EXPOSURE] %s %.1f >= %.1f",
                mid, exposure, max_exposure
            )
        return None

      outcome = "YES" if side == "BUY" else "NO"

      logger.info(
        "[EXEC] Submit %s %.2f on %s (%s)",
        outcome, size, mid, reason
    )

      # -------- place order --------
      try:
        if self.api and hasattr(self.api, "make_bet"):
            raw = self.api.make_bet(
                market_id=mid,
                outcome=outcome,
                amount=size,
            )
        elif self.api and hasattr(self.api, "place_order"):
            raw = self.api.place_order(
                mid, outcome, size, reason=reason
            )
        else:
            raw = place_order(mid, outcome, size, reason=reason)

        if not isinstance(raw, dict):
            raise RuntimeError("Invalid API response")

      except Exception as exc:
        logger.error(
            "[EXEC-FAIL] %s %s %.2f → %s",
            mid, outcome, size, exc
        )
        return None

      # -------- normalize receipt --------
      status = str(raw.get("status", "pending")).lower()
      order_id = raw.get("id") or raw.get("order_id") or raw.get("betId")

      self._pending_markets.add(mid)
      self._pending_expiry[mid] = now

      receipt = {
        "timestamp": now,
        "market_id": mid,
        "outcome": outcome,
        "side": side,
        "size": size,
        "reason": reason,
        "mode": raw.get("mode", "live"),
        "order_id": order_id,
        "status": status,
    }

      confirmed = status in ("filled", "success", "ok")

      self._record_trade(
        market_id=mid,
        direction=side,
        size=size,
        confirmed=confirmed,
      )

      logger.info(
        "[TRADE] %s %.2f on %s | status=%s order_id=%s",
        side, size, mid, status, order_id
    )

      return receipt
    
    def run_once_with_exploration(self, markets: List[Dict]) -> List[Dict]:
      receipts: List[Dict] = []
      if not markets:
        return receipts

      now = time.time()

      # Shuffle only for tie-breaking (NOT randomness)
      self._rng.shuffle(markets)

      decisions: List[Tuple[float, Dict]] = []
      seen_markets: set[str] = set()

      #  Evaluation pass 
      for market in markets:
        mid = market.get("id") or market.get("slug") or market.get("market_id")
        if not mid or mid in seen_markets:
            continue
        seen_markets.add(mid)

        # Hard block only pending orders
        if mid in self._pending_markets:
            continue

        try:
            decision = self._evaluate_market(market)
            if not decision:
                continue

            # FINAL authority for cooldown + duplicate protection
            if not self._can_trade(
                mid,
                decision["side"],
                decision.get("features"),
            ):
                continue

            edge = abs(float(decision["edge"]))
            size = float(decision["size"])
            confidence = abs(decision["model_p"] - decision["market_p"])

            # Stronger exploration so bot doesn’t stagnate
            explore_bonus = 1.35 if decision.get("explore") else 1.0

            priority = edge * size * (1.0 + confidence) * explore_bonus
            decisions.append((priority, decision))

        except Exception as exc:
            logger.debug("[EVAL ERROR] %s: %s", mid, exc)

      if not decisions:
        return receipts

      # Strongest signals first
      decisions.sort(key=lambda x: x[0], reverse=True)

      MAX_TRADES_PER_RUN = int(CONFIG.get("MAX_TRADES_PER_RUN", 3))
      MAX_RUN_RISK = float(CONFIG.get("MAX_RUN_RISK", 120.0))

      total_risk = 0.0

      # Execution 
      for _, decision in decisions:
        mid = decision["market_id"]
        size = float(decision["size"])

        if mid in self._pending_markets:
            continue

        if total_risk + size > MAX_RUN_RISK:
            break

        receipt = self._execute_decision(decision)

        if isinstance(receipt, dict):
            receipts.append(receipt)
            total_risk += size

            logger.info(
                "[TRADE] %s %s %.2f edge=%.4f explore=%s",
                mid,
                decision["side"],
                size,
                decision["edge"],
                decision.get("explore", False),
            )

        if len(receipts) >= MAX_TRADES_PER_RUN:
            break

      if DEBUG_MODE:
        logger.debug(
            "[RUN COMPLETE] trades=%d risk=%.1f pending=%d",
            len(receipts),
            total_risk,
            len(self._pending_markets),
        )

      return receipts

#  CLI smoke test 
if __name__ == "__main__":
    t = HybridTrader()
    test_file = "test_markets.json"

    if os.path.exists(test_file):
        try:
            with open(test_file, "r", encoding="utf-8") as fh:
                markets = json.load(fh)
        except Exception:
            markets = []
    else:
        markets = [
            {"id": "demo-market-1", "probability": 0.45, "question": "Demo: Will X happen?"},
            {"id": "demo-market-2", "probability": 0.60, "question": "Demo: Will Y happen?"},
        ]

    results = t.run_once_with_exploration(markets)
    print("Smoke test receipts:", results)