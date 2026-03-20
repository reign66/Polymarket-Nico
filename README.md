# Polymarket Trading Bot V2 — MATH FIRST, AI LAST

Bot de trading automatise pour [Polymarket](https://polymarket.com) — marches de prediction.

## Philosophie V2

**95% du travail est fait par des calculs mathematiques (zero cout API).**

```
Marches actifs (200-500)
    |  Filtre mecanique: volume, spread, temps       [GRATUIT]
    v
Marches filtres (~60-100)
    |  Classification par niche: regex/keywords       [GRATUIT]
    v
Marches classes (~50-80)
    |  Scoring math: Elo, GBM, momentum              [GRATUIT]
    |  Edge = notre proba - prix marche
    v
Edge > 8% (~2-5/jour)
    |  Claude Haiku: confirmation edge                [~0.001$/appel]
    v
Haiku confirme (~1-3/jour)
    |  Claude Sonnet: decision finale                 [~0.01$/appel]
    v
Sonnet GO (~0-2/jour)
    |  Risk check + Kelly sizing + execution
    v
BET PLACE (paper ou reel)
```

**Cout API cible: < 2 EUR/mois** (vs 86$/mois V1)

## Modeles Mathematiques

| Niche | Modele | Methode | Confiance |
|-------|--------|---------|-----------|
| NBA | Elo Rating | Ratings Elo + home advantage + streaks | 0.60-0.80 |
| F1 | Championship | Classements + tiers constructeurs | 0.45-0.65 |
| Crypto | GBM | Mouvement brownien geometrique (scipy) | 0.50-0.70 |
| Geopolitique | Momentum | Tendance prix + CII scores | 0.15-0.40 |
| Politique | Momentum | Incumbency + tendance | 0.20-0.45 |
| Generique | Market Prior | Trust the market | 0.05-0.15 |

## Structure

```
polymarket-bot/
├── core/
│   ├── database.py              # SQLite (6 tables)
│   ├── market_fetcher.py        # Gamma API + cache
│   ├── mechanical_filter.py     # Volume/spread/temps
│   ├── niche_classifier.py      # Regex keywords (zero IA)
│   ├── math_models/
│   │   ├── elo_model.py         # NBA Elo
│   │   ├── f1_model.py          # F1 standings
│   │   ├── crypto_model.py      # GBM + CoinGecko
│   │   ├── geo_model.py         # Momentum + CII
│   │   ├── politics_model.py    # Incumbency + momentum
│   │   └── generic_model.py     # Fallback
│   ├── edge_calculator.py       # Edge + Kelly + EV
│   ├── haiku_confirmer.py       # Claude Haiku (max 20/jour)
│   ├── sonnet_decider.py        # Claude Sonnet (max 5/jour)
│   ├── polymarket_client.py     # Gamma + CLOB API
│   ├── position_sizer.py        # Kelly criterion
│   ├── risk_manager.py          # 8 checks sequentiels
│   └── exit_manager.py          # TP/SL/near-resolution
├── alerts/telegram_bot.py       # 1 bot, tags par niche
├── dashboard/                   # Flask + responsive HTML
├── tools/                       # KPIs + health check
├── config.yaml
├── main.py                      # Orchestrateur (1 cycle/30min)
└── data/elo_ratings.json        # Ratings NBA persistes
```

## Installation

```bash
git clone <repo-url> && cd polymarket-bot
python3.11 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # Editer avec vos cles
python tools/check_health.py
python main.py  # Paper trading par defaut
```

## Deploiement Railway

```bash
railway login
railway link  # Lier au projet existant
railway up --detach
railway domain  # URL du dashboard
```

## Risk Management

- Circuit breakers: perte journaliere 10%, drawdown 7j 25%
- Max 5 positions ouvertes simultanement
- Kelly fraction 0.25, cap 5% du capital par bet
- Take-profit +20%, stop-loss -15%
- API budget: max 5 EUR/mois

<!-- deploy: 2026-03-20T12:14:08.586009 -->
