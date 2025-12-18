#!/usr/bin/env python3
# utils.py — Manifold Utilities 

from __future__ import annotations
import os, json, time, csv, math, requests, threading
from typing import List, Dict, Optional, Any, Tuple


# CONFIG

BASE = "https://api.manifold.markets/v0"
CONFIG_PATH = os.environ.get("MANIFOLD_BOT_CONFIG", "config.json")

CONFIG: Dict[str, Any] = {}
if os.path.exists(CONFIG_PATH):
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            CONFIG = json.load(f)
    except Exception:
        CONFIG = {}

API_KEY = str(CONFIG.get("MANIFOLD_API_KEY", "")).strip()
BOT_USERNAME = str(CONFIG.get("BOT_USERNAME", "ContestMikhailBot"))
TRADE_LOG = str(CONFIG.get("TRADE_LOG", "trades.csv"))
TRADING_MODE = str(CONFIG.get("TRADING_MODE", "paper")).lower()

TARGET_CREATOR = str(CONFIG.get("TARGET_CREATOR", "mikhailtal")).lower()
TARGET_CREATOR_ID = str(CONFIG.get("TARGET_CREATOR_ID", ""))


# SMALL HELPERS

def clipped(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, float(x)))

def normalize_username(name: Optional[str]) -> str:
    if not name:
        return ""
    return "".join(c for c in name.lower() if c.isalnum())


# SESSION

def make_session(bot_username: str) -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": f"{bot_username}-contest-bot/2.1",
        "Accept": "application/json",
    })
    return s


# SIMPLE CACHE (THREAD-SAFE)
class SimpleCache:
    def __init__(self):
        self._lock = threading.Lock()
        self.store: Dict[str, Tuple[float, Any]] = {}

    def get(self, key: str, max_age: int):
        with self._lock:
            v = self.store.get(key)
            if not v:
                return None
            ts, val = v
            if time.time() - ts > max_age:
                self.store.pop(key, None)
                return None
            return val

    def set(self, key: str, val: Any):
        with self._lock:
            self.store[key] = (time.time(), val)

_cache = SimpleCache()

# TRADE LOGGING (HARD DEDUPLICATION)

_last_trade_keys = set()

def log_trade(rec: Dict[str, Any]):
    """
    Guaranteed idempotent logging.
    """
    try:
        key = (
            rec.get("market_id"),
            rec.get("outcome"),
            float(rec.get("size", 0.0)),
            rec.get("mode"),
            rec.get("order_id"),
        )
    except Exception:
        return

    if key in _last_trade_keys:
        return
    _last_trade_keys.add(key)

    exists = os.path.exists(TRADE_LOG)
    fieldnames = [
        "timestamp", "market_id", "outcome", "size",
        "mode", "reason", "bot", "order_id", "status"
    ]

    with open(TRADE_LOG, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        if not exists:
            w.writeheader()
        w.writerow({
            "timestamp": rec.get("timestamp", time.time()),
            "market_id": rec.get("market_id"),
            "outcome": rec.get("outcome"),
            "size": rec.get("size"),
            "mode": rec.get("mode", "paper"),
            "reason": rec.get("reason", ""),
            "bot": rec.get("bot", BOT_USERNAME),
            "order_id": rec.get("order_id"),
            "status": rec.get("status", ""),
        })


# MANIFOLD API WRAPPER (STABLE)

class ManifoldAPI:
    def __init__(self, api_key: Optional[str] = None,
                 bot_username: Optional[str] = None,
                 config_mode: Optional[str] = None):

        self.api_key = (api_key if api_key else API_KEY) or None
        self.bot_username = bot_username or BOT_USERNAME
        self.mode = (config_mode or TRADING_MODE or "paper").lower()

        self.session = make_session(self.bot_username)
        if self.api_key:
            self.session.headers.update({"Authorization": f"Key {self.api_key}"})

        print(f"[ManifoldAPI] init mode={self.mode} api_key={'yes' if self.api_key else 'no'}")

   
    # HTTP GET (with caching + retry)
 
    def _get(self, url, params=None, attempts=4, timeout=8.0, cache_s=0):
        cache_key = f"G:{url}:{json.dumps(params, sort_keys=True) if params else ''}"

        if cache_s:
            cached = _cache.get(cache_key, cache_s)
            if cached is not None:
                return cached

        backoff = 0.25
        for _ in range(attempts):
            try:
                r = self.session.get(url, params=params, timeout=timeout)

                if r.status_code == 429:
                    time.sleep(backoff)
                    backoff *= 1.5
                    continue

                if 200 <= r.status_code < 300:
                    data = r.json()
                    if cache_s:
                        _cache.set(cache_key, data)
                    return data

                if 400 <= r.status_code < 500:
                    return r.json()

                time.sleep(backoff)
                backoff *= 1.4
            except:
                time.sleep(backoff)
                backoff *= 1.4

        return None

    def get(self, path, params=None, attempts=4, timeout=8.0, cache_s=0):
        url = path if path.startswith("http") else f"{BASE}/{path.lstrip('/')}"
        return self._get(url, params=params, attempts=attempts, timeout=timeout, cache_s=cache_s)


    # POST (with retry)
  
    def _post(self, url, payload, attempts=3, timeout=8.0):
        backoff = 0.3
        for _ in range(attempts):
            try:
                r = self.session.post(url, json=payload, timeout=timeout)
                return r.json()
            except:
                time.sleep(backoff)
                backoff *= 1.4

        return {"error": "connection_failed"}

    def post(self, path, payload, attempts=3, timeout=8.0):
        url = path if path.startswith("http") else f"{BASE}/{path.lstrip('/')}"
        return self._post(url, payload, attempts=attempts, timeout=timeout)


    # Get bet status (NEW — required for confirmation)

    def get_bet_status(self, bet_id):
        try:
            d = self.get(f"bets/{bet_id}", cache_s=0)
            return d if isinstance(d, dict) else None
        except:
            return None

   
    # Fetch markets by creator

    def _extract_creator_username(self, m):
        if not isinstance(m, dict): return None

        for k in ("creatorUsername","creatorusername"):
            v = m.get(k)
            if isinstance(v, str):
                return normalize_username(v)

        if isinstance(m.get("creator"), dict):
            v = m["creator"].get("username")
            if isinstance(v, str):
                return normalize_username(v)

        cid = m.get("creatorId")
        if cid:
            u = self.get(f"users/{cid}", cache_s=30)
            if isinstance(u, dict):
                v = u.get("username")
                if v:
                    return normalize_username(v)

        return None

    def fetch_markets_by_creator(self, username: str, limit=400):
        target = normalize_username(username)
        results = []
        cursor = None
        page = 0

        while page < 12 and len(results) < limit:
            params = {"limit": 100}
            if cursor:
                params["before"] = cursor

            raw = self.get("markets", params=params)
            if not raw:
                break

            if isinstance(raw, dict):
                raw = raw.get("markets") or raw.get("results") or []

            if not isinstance(raw, list):
                break

            for m in raw:
                try:
                    if self._extract_creator_username(m) == target:
                        results.append(m)
                except:
                    pass

            cursor = raw[-1].get("id")
            page += 1
            time.sleep(0.10)

        print(f"[CREATOR_FETCH] {username}: {len(results)} markets")
        return results[:limit]

 
    def get_market(self, market_id: str):
        mid = market_id.split("/")[-1]
        d = self.get(f"markets/{mid}", cache_s=3)
        return d if isinstance(d, dict) else None

    def get_orderbook(self, market_id: str):
        mid = market_id.split("/")[-1]
        d = self.get(f"markets/{mid}/orderbook", cache_s=3)
        if not d:
            return {"bids": [], "asks": [], "best_bid": None, "best_ask": None}

        def parse(arr):
            out = []
            for x in arr or []:
                try:
                    if isinstance(x, dict) and "price" in x:
                        out.append(float(x["price"]))
                except:
                    pass
            return out

        bids = parse(d.get("bids"))
        asks = parse(d.get("asks"))

        return {
            "bids": bids,
            "asks": asks,
            "best_bid": max(bids) if bids else None,
            "best_ask": min(asks) if asks else None
        }

    def get_market_bets(self, market_id, limit=200):
        mid = market_id.split("/")[-1]
        d = self.get(f"markets/{mid}/bets", params={"limit": limit})
        if isinstance(d, list): return d
        if isinstance(d, dict): return d.get("bets") or d.get("results") or []
        return []


    # place_order
   
    def place_order(self, market_id, outcome, amount, reason=""):
      ts = time.time()
      outcome = (outcome or "YES").upper()
      amount = float(max(1.0, round(amount, 2)))

      # ---------------- PAPER MODE ----------------
      if self.mode != "live" or not self.api_key:
        rec = {
            "timestamp": ts,
            "market_id": market_id,
            "outcome": outcome,
            "size": amount,
            "mode": "paper",
            "reason": reason,
            "order_id": f"paper-{int(ts)}",
            "status": "paper",
            "raw": None,
            "error": None,
        }
        log_trade(rec)
        return rec

    # ---------------- SEND ORDER ----------------
      payload = {
        "contractId": market_id,
        "outcome": outcome,
        "amount": amount,
    }

      try:
        initial = self.post("bet", payload)
      except Exception as e:
        rec = {
            "timestamp": ts,
            "market_id": market_id,
            "outcome": outcome,
            "size": amount,
            "mode": "live",
            "reason": reason,
            "order_id": f"error-{int(ts)}",
            "status": "failed",
            "raw": None,
            "error": str(e),
        }
        log_trade(rec)
        return rec

    #  ORDER ID 
      bet_id = None
      if isinstance(initial, dict):
        bet_id = initial.get("betId") or initial.get("id")

      if not bet_id:
        rec = {
            "timestamp": ts,
            "market_id": market_id,
            "outcome": outcome,
            "size": amount,
            "mode": "live",
            "reason": reason,
            "order_id": f"pending-{int(ts)}",
            "status": "submitted",
            "raw": initial,
            "error": None,
        }
        log_trade(rec)
        return rec

    #  CONFIRMATION POLL 
      status = "submitted"
      final = initial

      for _ in range(5):
        time.sleep(0.4)
        try:
            st = self.get_bet_status(bet_id)
            if st:
                final = st
                if st.get("isFilled") or st.get("amount"):
                    status = "filled"
                    break
                status = "pending"
        except Exception:
            pass

      rec = {
        "timestamp": ts,
        "market_id": market_id,
        "outcome": outcome,
        "size": amount,
        "mode": "live",
        "reason": reason,
        "order_id": bet_id,
        "status": status,
        "raw": final,
        "error": None,
    }

      log_trade(rec)
      return rec


    def place_bet(self, *a, **k):
      return self.place_order(*a, **k)

# SIZING HELPERS

def kelly_bet(bankroll, model_p, market_p, fraction=0.25, max_bet=200):
    edge = model_p - market_p
    if edge <= 0:
        return 0.0
    f = edge / max(0.01, (model_p * (1 - model_p)))
    f = max(0.0, min(1.0, f)) * fraction
    stake = min(bankroll * f, max_bet)
    return round(max(1.0, stake), 2)


# MARKET HELPERS

def get_market_current_prob(market_id):
    api = ManifoldAPI()
    d = api.get_market(market_id)
    if not d: return 0.5
    for k in ("probability","p","prob","pYes","lastPrice"):
        if k in d:
            try:
                p = float(d[k])
                return p if p <= 1 else p/100
            except:
                pass
    return 0.5

def get_recent_market_probs(market_id, n=5):
    api = ManifoldAPI()
    bets = api.get_market_bets(market_id, limit=200)
    out = []

    for b in bets:
        for k in ("probAfter","probabilityAfter","prob","probability"):
            if k in b:
                try:
                    p = float(b[k])
                    out.append(p if p <= 1 else p/100)
                except:
                    pass
                break

    if not out:
        return [get_market_current_prob(market_id)] * n

    return out[-n:]

def safe_ema(vals):
    if not vals: return None
    a = 2.0/(len(vals)+1)
    v = vals[0]
    for x in vals[1:]:
        v = a*x + (1-a)*v
    return v

def compute_volatility(vals, window=8):
    if len(vals) < 2:
        return 0.0
    x = vals[-window:]
    diffs = [abs(x[i]-x[i-1]) for i in range(1,len(x))]
    return sum(diffs)/max(1,len(diffs))

def clipped(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, x))

def pseudo_llm_predict(q: str):
    h = abs(hash(q)) % 1_000_000
    base = 0.35 + (h % 350000) / 1_000_000
    jit = (h % 100) / 1000.0 - 0.05
    return clipped(base + jit), clipped(0.55 + jit)

def llm_estimate_probability(q, recent_changes=None):
    return pseudo_llm_predict(q)

def get_market_score(m):
    prob = float(m.get("probability", 0.5))
    mid = float(m.get("mid_price", prob))
    spread = float(m.get("spread", 0.02))
    vol = float(m.get("volatility", 0.02))
    liq = float(m.get("liquidity", 0))
    mis = abs(mid - prob)
    return mis * (vol + 0.0001) * math.log1p(max(0,liq)) / max(spread, 1e-8)
    