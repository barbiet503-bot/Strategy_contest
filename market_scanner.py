# market_scanner.py
# Contest-grade market scanner (creator-only, low-noise, stable)

from typing import List, Dict
import time


def normalize(x: str) -> str:
    return str(x).strip().lower() if x else ""


class MarketScanner:
    def __init__(
        self,
        api,
        creator: str = "MikhailTal",
        min_liquidity: float = 3.0,
        max_markets: int = 8,
        cache_ttl: float = 8.0,
    ):
        self.api = api
        self.creator = normalize(creator)
        self.min_liquidity = float(min_liquidity)
        self.max_markets = int(max_markets)
        self.cache_ttl = float(cache_ttl)

        self._cache: List[Dict] = []
        self._cache_ts: float = 0.0

        print(
            f"[INIT] MarketScanner | creator={creator} | "
            f"min_liq={min_liquidity} | max={max_markets}"
        )

    # --------------------
    def _creator_matches(self, m: Dict) -> bool:
        creator = None
        if isinstance(m.get("creator"), dict):
            creator = m["creator"].get("username")
        else:
            creator = m.get("creatorUsername")

        return normalize(creator) == self.creator

    # --------------------
    def _liquidity(self, m: Dict) -> float:
        """
        Contest-safe liquidity metric:
        - volume is the most reliable signal across Manifold binaries
        """
        return float(m.get("volume") or 0.0)

    # --------------------
    def get_markets(self) -> List[Dict]:
        now = time.time()

        # cache hit
        if self._cache and (now - self._cache_ts) < self.cache_ttl:
            return list(self._cache)

        try:
            markets = self.api.fetch_markets_by_creator(self.creator)
        except Exception as e:
            print("[SCAN] fetch failed:", e)
            return []

        selected: List[Dict] = []

        for m in markets:
            if not isinstance(m, dict):
                continue
            if not self._creator_matches(m):
                continue
            if m.get("isResolved"):
                continue
            if m.get("outcomeType") != "BINARY":
                continue
            if self._liquidity(m) < self.min_liquidity:
                continue

            selected.append(m)

        # prioritize highest-signal markets
        selected.sort(key=self._liquidity, reverse=True)
        selected = selected[: self.max_markets]

        print(f"[SCAN] {len(selected)} markets passed scanner")

        self._cache = list(selected)
        self._cache_ts = now
        return selected