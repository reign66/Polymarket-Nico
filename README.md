# Polymarket Trading Bot

Bot de trading automatisé pour [Polymarket](https://polymarket.com) — marchés de prédiction.

## Architecture

```
polymarket-bot/
├── core/                    # Modules principaux
│   ├── database.py          # SQLite + SQLAlchemy (6 tables)
│   ├── worldmonitor_reader.py # News WorldMonitor + fallbacks
│   ├── sport_reader.py      # NBA, F1, ESPN
│   ├── haiku_classifier.py  # Claude Haiku — filtre news
│   ├── sonnet_decider.py    # Claude Sonnet — décision de pari
│   ├── polymarket_client.py # Gamma + CLOB API
│   ├── position_sizer.py    # Kelly Criterion
│   ├── risk_manager.py      # Circuit breakers + capital rules
│   └── exit_manager.py      # Take-profit / stop-loss / cash-out
├── bots/                    # Bots spécialisés par niche
│   ├── base_bot.py          # Classe abstraite — pipeline complet
│   ├── bot_nba.py           # NBA (cycle 15min)
│   ├── bot_f1.py            # F1 (cycle 30min)
│   ├── bot_crypto.py        # Crypto + macro (cycle 10min)
│   ├── bot_geopolitics.py   # Géopolitique (cycle 30min, Kelly réduit)
│   └── bot_politics.py      # Politique US/EU (cycle 20min)
├── alerts/
│   ├── telegram_bot.py      # 1 bot Telegram, tags par niche
│   └── email_notifier.py    # Rapports email HTML
├── dashboard/
│   ├── app.py               # Flask API (14 endpoints)
│   └── templates/index.html # Dashboard responsive dark theme
├── tools/
│   ├── kpi_tracker.py       # KPIs + Go/No-Go
│   ├── backtester.py        # Simulation historique
│   └── check_health.py      # Vérification santé système
├── config.yaml              # Configuration complète
├── main.py                  # Orchestrateur APScheduler
├── start.sh / stop.sh       # Scripts démarrage/arrêt
├── Procfile                 # Railway deployment
└── runtime.txt              # Python 3.11
```

## Pipeline de Trading

```
News → Haiku (filtre, 300 tokens) → Score >= 0.7 & Edge >= 12%
  → Circuit breakers OK → Sonnet (décision, 500 tokens, max 5/jour/bot)
  → Edge >= 15% & Confidence HIGH/MEDIUM → Liquidity check
  → Kelly sizing (fraction 0.25) → Place bet (paper ou réel)
  → Telegram notification
```

## Stratégie Claude API (<7€/mois)

| Modèle | Usage | Max tokens | Condition |
|--------|-------|------------|-----------|
| Haiku | Classification news | 300 | News pertinente pour Polymarket |
| Sonnet | Décision finale | 500 | Haiku score >= 0.7 ET edge > 12% |

- Max 5 appels Sonnet/jour/bot
- JAMAIS Claude pour alertes, dashboard, résumés

## Installation

```bash
# Cloner le repo
git clone <repo-url>
cd polymarket-bot

# Environnement virtuel
python3.11 -m venv venv
source venv/bin/activate

# Dépendances
pip install -r requirements.txt

# Configuration
cp .env.example .env
# Éditer .env avec vos clés API

# Vérification santé
python tools/check_health.py

# Démarrage (paper trading par défaut)
python main.py
```

## Variables d'Environnement

| Variable | Description |
|----------|-------------|
| `ANTHROPIC_API_KEY` | Clé API Anthropic |
| `TELEGRAM_BOT_TOKEN` | Token bot Telegram |
| `TELEGRAM_CHAT_ID` | Chat ID Telegram |
| `PAPER_TRADING` | `true` (défaut) ou `false` |
| `CAPITAL_INITIAL` | Capital de départ en € |
| `MAX_BET_SIZE` | Taille max d'un pari en USDC |
| `POLYMARKET_*` | Clés Polymarket (pour trading réel) |
| `EMAIL_*` | Gmail SMTP (pour rapports email) |

## Risk Management

- **Circuit breakers** : perte journalière 10%, drawdown 7j 25%, 5 positions max, API 15€/mois
- **Exit rules** : take-profit +20%, stop-loss -15%, alerte cash-out à 48h de résolution
- **Capital limits** : 30% du capital/jour (<2000€), 40% (>=2000€), exception +20% si edge >25%
- **Dedup** : un seul pari par marché à travers tous les bots

## Dashboard

Accessible sur `http://localhost:5000` (ou URL Railway).
Dark theme responsive (375px → 1920px), auto-refresh 30s.

## Déploiement Railway

```bash
railway login
railway init --name polymarket-bot
railway variables set ...  # Depuis .env
railway up --detach
railway domain  # Obtenir l'URL
```
