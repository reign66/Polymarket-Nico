"""
tools/opportunity_scanner.py — Détecteur de failles structurelles sur Polymarket

Faille 1: Marchés à résolution imminente (<7j) mal pricés
  → Un marché à 85% qui résout dans 3j mais pricé à 70% = edge de 15% quasi certain

Faille 2: Marchés sous-tradés mal pricés
  → Volume $500-5000, prix éloigné de la moyenne historique

Faille 3: Corrélation non pricée
  → Si marché A est à 80%, marchés corrélés à A encore à 30%
"""

import time
import logging
import requests
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)

GAMMA_API = "https://gamma-api.polymarket.com"
HEADERS = {"User-Agent": "PolymarketBot/2.0 Research"}


class OpportunityScanner:
    def __init__(self):
        self._cache = {}
        self._cache_ttl = 300  # 5 min

    def _request(self, url, params=None):
        try:
            r = requests.get(url, params=params, headers=HEADERS, timeout=15)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            logger.debug(f"OpportunityScanner request failed: {e}")
            return None

    def scan_imminent_resolution(self, max_days: int = 7) -> list:
        """
        Faille 1: Marchés qui résolvent bientôt mais prix encore mal ajusté.
        Cherche: marchés < max_days jours, yes_price entre 0.70 et 0.95
        (devrait être à ~0.95+ si vraiment certain, ou <0.50 si incertain)
        → Edge = écart entre prix actuel et probabilité réelle estimée
        """
        now = datetime.now(timezone.utc)
        cutoff = now + timedelta(days=max_days)

        opportunities = []
        offset = 0
        while offset < 1500:
            data = self._request(f"{GAMMA_API}/markets", params={
                "active": "true", "closed": "false",
                "limit": 100, "offset": offset
            })
            if not data:
                break
            markets = data if isinstance(data, list) else data.get("markets", data.get("data", []))
            if not markets:
                break

            for m in markets:
                end_date = m.get("endDate", "")
                if not end_date:
                    continue
                try:
                    dt = datetime.fromisoformat(end_date.replace("Z", "+00:00"))
                    hours_left = (dt - now).total_seconds() / 3600
                    days_left = hours_left / 24
                    if days_left <= 0 or days_left > max_days:
                        continue
                except Exception:
                    continue

                import json as _json
                prices = m.get("outcomePrices", "")
                try:
                    if isinstance(prices, str):
                        prices = _json.loads(prices)
                    yes_price = float(prices[0])
                    no_price = float(prices[1])
                except Exception:
                    continue

                vol = float(m.get("volumeNum", m.get("volume", 0)) or 0)
                liq = float(m.get("liquidityNum", m.get("liquidity", 0)) or 0)

                # Filtre: prix entre 0.60 et 0.93 (pas encore résolu, mais probable)
                # ET prix NO entre 0.07 et 0.40 (le marché hésite encore)
                if 0.60 <= yes_price <= 0.93 and vol > 500:
                    # Edge estimé: si résolution dans <7j avec 60-93% de prob,
                    # le marché devrait converger vers 95%+ → edge = 95% - yes_price
                    implied_convergence = min(0.95, yes_price + 0.10)
                    edge_yes = implied_convergence - yes_price

                    # Edge NO: si yes est à 0.60-0.65 mais résolution proche, peut aussi aller à 0
                    if yes_price < 0.70:
                        edge_no = no_price - 0.05  # NO devrait converger vers 5% max

                    if edge_yes > 0.05 or (yes_price < 0.70 and edge_no > 0.20):
                        opportunities.append({
                            "type": "imminent_resolution",
                            "market_id": str(m.get("id", "")),
                            "question": m.get("question", "")[:80],
                            "yes_price": yes_price,
                            "no_price": no_price,
                            "edge_yes": round(edge_yes, 3),
                            "edge_no": round(no_price - 0.05, 3) if yes_price < 0.70 else 0,
                            "days_left": round(days_left, 1),
                            "volume": vol,
                            "liquidity": liq,
                            "end_date": end_date[:16],
                            "priority_score": round(edge_yes / max(days_left, 0.1), 3),
                        })

            if len(markets) < 100:
                break
            offset += 100

        # Trier par priorité (edge / jours restants = urgence)
        opportunities.sort(key=lambda x: x["priority_score"], reverse=True)
        logger.info(f"OpportunityScanner: {len(opportunities)} marchés imminents détectés")
        return opportunities

    def scan_low_volume_mispriced(self, min_vol: float = 500, max_vol: float = 8000) -> list:
        """
        Faille 2: Marchés sous-tradés avec prix potentiellement mal ajustés.
        Volume $500-8000 → peu de market makers → pricing moins efficace.
        """
        opportunities = []
        offset = 0
        while offset < 1500:
            data = self._request(f"{GAMMA_API}/markets", params={
                "active": "true", "closed": "false",
                "limit": 100, "offset": offset
            })
            if not data:
                break
            markets = data if isinstance(data, list) else data.get("markets", data.get("data", []))
            if not markets:
                break

            import json as _json
            for m in markets:
                vol = float(m.get("volumeNum", m.get("volume", 0)) or 0)
                if not (min_vol <= vol <= max_vol):
                    continue

                prices = m.get("outcomePrices", "")
                try:
                    if isinstance(prices, str):
                        prices = _json.loads(prices)
                    yes_price = float(prices[0])
                    no_price = float(prices[1])
                except Exception:
                    continue

                # Prix mal alignés: sum != 1.0 (spread élevé = inefficacité)
                spread = abs(1.0 - yes_price - no_price)
                
                # Prix extrêmes dans un marché sous-tradé (20-35% ou 65-80%)
                # = probablement mal pricé car peu de gens ont regardé
                is_extreme_low = 0.15 <= yes_price <= 0.35
                is_extreme_high = 0.65 <= yes_price <= 0.85

                liq = float(m.get("liquidityNum", m.get("liquidity", 0)) or 0)
                
                if (is_extreme_low or is_extreme_high) and spread > 0.02:
                    opportunities.append({
                        "type": "low_volume_mispriced",
                        "market_id": str(m.get("id", "")),
                        "question": m.get("question", "")[:80],
                        "yes_price": yes_price,
                        "no_price": no_price,
                        "spread": round(spread, 3),
                        "volume": vol,
                        "liquidity": liq,
                        "end_date": m.get("endDate", "")[:16],
                        "priority_score": round(spread * (max_vol - vol) / max_vol, 3),
                    })

            if len(markets) < 100:
                break
            offset += 100

        opportunities.sort(key=lambda x: x["priority_score"], reverse=True)
        logger.info(f"OpportunityScanner: {len(opportunities)} marchés sous-tradés détectés")
        return opportunities

    def get_all_opportunities(self) -> dict:
        """Scanne toutes les failles et retourne un rapport consolidé."""
        now = time.time()
        cache_key = "all_opps"
        if cache_key in self._cache:
            ts, data = self._cache[cache_key]
            if now - ts < self._cache_ttl:
                return data

        imminent = self.scan_imminent_resolution(max_days=7)
        low_vol = self.scan_low_volume_mispriced()

        result = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "imminent_resolution": imminent[:20],
            "low_volume_mispriced": low_vol[:20],
            "total_opportunities": len(imminent) + len(low_vol),
        }

        self._cache[cache_key] = (now, result)
        logger.info(
            f"OpportunityScanner total: {result['total_opportunities']} opportunities "
            f"({len(imminent)} imminent, {len(low_vol)} low_vol)"
        )
        return result


# Singleton
_scanner = None
def get_opportunity_scanner() -> OpportunityScanner:
    global _scanner
    if _scanner is None:
        _scanner = OpportunityScanner()
    return _scanner
