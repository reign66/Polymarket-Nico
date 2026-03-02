import os
import logging
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
from typing import Dict, List

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Shared style constants (inline CSS — required for email client compatibility)
# ---------------------------------------------------------------------------
_BG = '#1a1a2e'
_SURFACE = '#16213e'
_CARD = '#0f3460'
_TEXT = '#e0e0e0'
_MUTED = '#a0a0b0'
_ACCENT = '#00F5A0'
_RED = '#ff4d4d'
_ORANGE = '#ffa500'
_BORDER = '#2a2a4a'

_BASE_STYLE = f"""
body {{ margin: 0; padding: 0; background-color: {_BG}; font-family: Arial, Helvetica, sans-serif; color: {_TEXT}; }}
.wrapper {{ max-width: 640px; margin: 0 auto; padding: 24px 16px; }}
.header {{ text-align: center; padding: 24px 0 8px; }}
.header h1 {{ margin: 0; font-size: 22px; color: {_ACCENT}; letter-spacing: 1px; }}
.header p {{ margin: 4px 0 0; font-size: 13px; color: {_MUTED}; }}
.card {{ background: {_SURFACE}; border: 1px solid {_BORDER}; border-radius: 8px; padding: 20px; margin: 16px 0; }}
.card h2 {{ margin: 0 0 12px; font-size: 15px; color: {_ACCENT}; text-transform: uppercase; letter-spacing: 0.5px; }}
.kpi-row {{ display: flex; gap: 12px; margin: 12px 0; }}
.kpi {{ flex: 1; background: {_CARD}; border-radius: 6px; padding: 12px; text-align: center; }}
.kpi .value {{ font-size: 20px; font-weight: bold; color: {_ACCENT}; }}
.kpi .label {{ font-size: 11px; color: {_MUTED}; margin-top: 4px; }}
table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
th {{ background: {_CARD}; color: {_ACCENT}; padding: 8px 10px; text-align: left; font-size: 11px; text-transform: uppercase; letter-spacing: 0.5px; }}
td {{ padding: 8px 10px; border-bottom: 1px solid {_BORDER}; color: {_TEXT}; }}
tr:last-child td {{ border-bottom: none; }}
.green {{ color: {_ACCENT}; }}
.red {{ color: {_RED}; }}
.tag {{ display: inline-block; background: {_CARD}; color: {_ACCENT}; border-radius: 4px; padding: 2px 8px; font-size: 11px; font-weight: bold; }}
.footer {{ text-align: center; font-size: 11px; color: {_MUTED}; padding: 16px 0 0; }}
"""


def _html_wrap(title: str, subtitle: str, body_html: str) -> str:
    """Wrap content in the standard dark-theme email shell."""
    return f"""<!DOCTYPE html>
<html lang="fr">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{title}</title>
  <style>{_BASE_STYLE}</style>
</head>
<body>
  <div class="wrapper">
    <div class="header">
      <h1>{title}</h1>
      <p>{subtitle}</p>
    </div>
    {body_html}
    <div class="footer">Polymarket Bot &mdash; rapport automatique &mdash; {datetime.utcnow().strftime('%Y-%m-%d %H:%M')} UTC</div>
  </div>
</body>
</html>"""


def _pnl_class(value: float) -> str:
    return 'green' if value >= 0 else 'red'


def _pnl_str(value: float) -> str:
    return f"{value:+.2f}&euro;"


def _build_niches_table(niches: List[Dict]) -> str:
    if not niches:
        return '<p style="color:#a0a0b0;font-size:13px;">Aucune donnée par niche.</p>'
    rows = ''
    for n in niches:
        name = str(n.get('niche', 'N/A')).upper()
        pnl = float(n.get('pnl', 0))
        nb = int(n.get('nb', 0))
        wr = float(n.get('wr', 0))
        rows += (
            f'<tr>'
            f'<td><span class="tag">{name}</span></td>'
            f'<td class="{_pnl_class(pnl)}">{_pnl_str(pnl)}</td>'
            f'<td>{nb}</td>'
            f'<td>{wr:.0%}</td>'
            f'</tr>'
        )
    return (
        '<table>'
        '<thead><tr><th>Niche</th><th>P&amp;L</th><th>Bets</th><th>Win Rate</th></tr></thead>'
        f'<tbody>{rows}</tbody>'
        '</table>'
    )


def _build_positions_table(positions: List[Dict]) -> str:
    if not positions:
        return '<p style="color:#a0a0b0;font-size:13px;">Aucune position ouverte.</p>'
    rows = ''
    for p in positions:
        market = p.get('market', 'N/A')
        direction = p.get('direction', 'N/A')
        price = float(p.get('price', 0))
        pnl = float(p.get('pnl', 0))
        rows += (
            f'<tr>'
            f'<td>{market}</td>'
            f'<td>{direction}</td>'
            f'<td>{price}$</td>'
            f'<td class="{_pnl_class(pnl)}">{_pnl_str(pnl)}</td>'
            f'</tr>'
        )
    return (
        '<table>'
        '<thead><tr><th>Marché</th><th>Direction</th><th>Prix</th><th>P&amp;L latent</th></tr></thead>'
        f'<tbody>{rows}</tbody>'
        '</table>'
    )


def _build_suggestions_section(suggestions: List[Dict]) -> str:
    if not suggestions:
        return '<p style="color:#a0a0b0;font-size:13px;">Aucune suggestion.</p>'
    items = ''
    for s in suggestions:
        bot = s.get('bot', 'N/A')
        suggestion = s.get('suggestion', '')
        items += (
            f'<div style="border-left:3px solid {_ACCENT};padding:6px 12px;margin:6px 0;">'
            f'<span class="tag">{bot}</span>'
            f'<span style="color:{_TEXT};font-size:13px;margin-left:8px;">{suggestion}</span>'
            f'</div>'
        )
    return items


class EmailNotifier:
    def __init__(self):
        self.sender = os.environ.get('EMAIL_SENDER', '')
        self.password = os.environ.get('EMAIL_PASSWORD', '')
        self.recipient = os.environ.get('EMAIL_RECIPIENT', '')
        self.enabled = bool(self.sender and self.password and self.recipient)
        if not self.enabled:
            logger.warning(
                "Email alerts disabled: missing EMAIL_SENDER, EMAIL_PASSWORD, or EMAIL_RECIPIENT"
            )

    def _send_email(self, subject: str, html_body: str) -> bool:
        if not self.enabled:
            logger.info(f"Email (disabled): {subject}")
            return False
        try:
            msg = MIMEMultipart('alternative')
            msg['Subject'] = subject
            msg['From'] = self.sender
            msg['To'] = self.recipient
            msg.attach(MIMEText(html_body, 'html'))

            with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
                server.login(self.sender, self.password)
                server.sendmail(self.sender, self.recipient, msg.as_string())
            logger.info(f"Email sent: {subject}")
            return True
        except Exception as e:
            logger.error(f"Email error: {e}")
            return False

    # ------------------------------------------------------------------
    # Public methods
    # ------------------------------------------------------------------

    def send_daily_report(self, stats: Dict) -> bool:
        """Send an HTML daily performance report email."""
        date_str = stats.get('date', datetime.utcnow().strftime('%Y-%m-%d'))
        pnl = float(stats.get('pnl', 0))
        nb_bets = int(stats.get('nb_bets', 0))
        nb_wins = int(stats.get('nb_wins', 0))
        nb_losses = int(stats.get('nb_losses', 0))
        wr_day = float(stats.get('wr_day', 0))
        wr_30d = float(stats.get('wr_30d', 0))
        capital = float(stats.get('capital', 0))
        roi_month = float(stats.get('roi_month', 0))
        api_cost = float(stats.get('api_cost', 0))

        niches: List[Dict] = stats.get('niches', [])
        open_positions: List[Dict] = stats.get('open_positions', [])
        suggestions: List[Dict] = stats.get('suggestions', [])

        pnl_color = _ACCENT if pnl >= 0 else _RED
        pnl_sign = '+' if pnl >= 0 else ''

        body = f"""
        <div class="card">
          <h2>Performance du jour</h2>
          <!-- KPI row using table for email compatibility -->
          <table style="margin-bottom:16px;">
            <tr>
              <td style="padding:8px;text-align:center;background:{_CARD};border-radius:6px;width:25%;">
                <div style="font-size:20px;font-weight:bold;color:{pnl_color};">{pnl_sign}{pnl:.2f}&euro;</div>
                <div style="font-size:11px;color:{_MUTED};margin-top:4px;">P&amp;L du jour</div>
              </td>
              <td style="width:12px;"></td>
              <td style="padding:8px;text-align:center;background:{_CARD};border-radius:6px;width:25%;">
                <div style="font-size:20px;font-weight:bold;color:{_ACCENT};">{nb_bets}</div>
                <div style="font-size:11px;color:{_MUTED};margin-top:4px;">Bets placés</div>
              </td>
              <td style="width:12px;"></td>
              <td style="padding:8px;text-align:center;background:{_CARD};border-radius:6px;width:25%;">
                <div style="font-size:20px;font-weight:bold;color:{_ACCENT};">{wr_day:.0%}</div>
                <div style="font-size:11px;color:{_MUTED};margin-top:4px;">Win Rate jour</div>
              </td>
              <td style="width:12px;"></td>
              <td style="padding:8px;text-align:center;background:{_CARD};border-radius:6px;width:25%;">
                <div style="font-size:20px;font-weight:bold;color:{_ACCENT};">{capital:.0f}&euro;</div>
                <div style="font-size:11px;color:{_MUTED};margin-top:4px;">Capital total</div>
              </td>
            </tr>
          </table>
          <table>
            <tr><td style="color:{_MUTED};">Gagnés / Perdus</td><td>{nb_wins} / {nb_losses}</td></tr>
            <tr><td style="color:{_MUTED};">Win Rate 30j</td><td>{wr_30d:.0%}</td></tr>
            <tr><td style="color:{_MUTED};">ROI du mois</td><td>{roi_month:.1%}</td></tr>
            <tr><td style="color:{_MUTED};">Coût API Claude ce mois</td><td>{api_cost:.2f}&euro;</td></tr>
          </table>
        </div>

        <div class="card">
          <h2>Par Niche</h2>
          {_build_niches_table(niches)}
        </div>

        <div class="card">
          <h2>Positions ouvertes ({len(open_positions)})</h2>
          {_build_positions_table(open_positions)}
        </div>

        <div class="card">
          <h2>Améliorations potentielles</h2>
          {_build_suggestions_section(suggestions)}
        </div>
        """

        html = _html_wrap(
            title=f"📊 Bilan journalier — {date_str}",
            subtitle=f"Rapport automatique du {date_str}",
            body_html=body,
        )
        subject = f"📊 Polymarket Bot — Bilan journalier {date_str}"
        return self._send_email(subject, html)

    def send_weekly_report(self, stats: Dict) -> bool:
        """Send an HTML weekly performance report email."""
        week_label = stats.get('week_label', datetime.utcnow().strftime('Semaine du %d/%m/%Y'))
        pnl = float(stats.get('pnl', 0))
        nb_bets = int(stats.get('nb_bets', 0))
        nb_wins = int(stats.get('nb_wins', 0))
        nb_losses = int(stats.get('nb_losses', 0))
        wr_week = float(stats.get('wr_week', 0))
        capital = float(stats.get('capital', 0))
        roi_week = float(stats.get('roi_week', 0))
        api_cost = float(stats.get('api_cost', 0))
        sharpe = float(stats.get('sharpe_ratio', 0))
        max_dd = float(stats.get('max_drawdown', 0))

        best_bet: Dict = stats.get('best_bet', {})
        worst_bet: Dict = stats.get('worst_bet', {})

        niches: List[Dict] = stats.get('niches', [])
        open_positions: List[Dict] = stats.get('open_positions', [])
        suggestions: List[Dict] = stats.get('suggestions', [])

        pnl_color = _ACCENT if pnl >= 0 else _RED
        pnl_sign = '+' if pnl >= 0 else ''

        best_market = best_bet.get('market', 'N/A')
        best_pnl = float(best_bet.get('pnl', 0))
        worst_market = worst_bet.get('market', 'N/A')
        worst_pnl = float(worst_bet.get('pnl', 0))

        body = f"""
        <div class="card">
          <h2>Performance de la semaine</h2>
          <table style="margin-bottom:16px;">
            <tr>
              <td style="padding:8px;text-align:center;background:{_CARD};border-radius:6px;width:25%;">
                <div style="font-size:20px;font-weight:bold;color:{pnl_color};">{pnl_sign}{pnl:.2f}&euro;</div>
                <div style="font-size:11px;color:{_MUTED};margin-top:4px;">P&amp;L semaine</div>
              </td>
              <td style="width:12px;"></td>
              <td style="padding:8px;text-align:center;background:{_CARD};border-radius:6px;width:25%;">
                <div style="font-size:20px;font-weight:bold;color:{_ACCENT};">{nb_bets}</div>
                <div style="font-size:11px;color:{_MUTED};margin-top:4px;">Bets placés</div>
              </td>
              <td style="width:12px;"></td>
              <td style="padding:8px;text-align:center;background:{_CARD};border-radius:6px;width:25%;">
                <div style="font-size:20px;font-weight:bold;color:{_ACCENT};">{wr_week:.0%}</div>
                <div style="font-size:11px;color:{_MUTED};margin-top:4px;">Win Rate</div>
              </td>
              <td style="width:12px;"></td>
              <td style="padding:8px;text-align:center;background:{_CARD};border-radius:6px;width:25%;">
                <div style="font-size:20px;font-weight:bold;color:{_ACCENT};">{capital:.0f}&euro;</div>
                <div style="font-size:11px;color:{_MUTED};margin-top:4px;">Capital total</div>
              </td>
            </tr>
          </table>
          <table>
            <tr><td style="color:{_MUTED};">Gagnés / Perdus</td><td>{nb_wins} / {nb_losses}</td></tr>
            <tr><td style="color:{_MUTED};">ROI semaine</td><td>{roi_week:.1%}</td></tr>
            <tr><td style="color:{_MUTED};">Sharpe Ratio</td><td>{sharpe:.2f}</td></tr>
            <tr><td style="color:{_MUTED};">Max Drawdown</td><td style="color:{_RED};">{max_dd:.1%}</td></tr>
            <tr><td style="color:{_MUTED};">Coût API Claude</td><td>{api_cost:.2f}&euro;</td></tr>
          </table>
        </div>

        <div class="card">
          <h2>Meilleur &amp; Pire Bet</h2>
          <table>
            <tr>
              <td style="color:{_MUTED};">🏆 Meilleur bet</td>
              <td>{best_market}</td>
              <td class="green">{best_pnl:+.2f}&euro;</td>
            </tr>
            <tr>
              <td style="color:{_MUTED};">💀 Pire bet</td>
              <td>{worst_market}</td>
              <td class="red">{worst_pnl:+.2f}&euro;</td>
            </tr>
          </table>
        </div>

        <div class="card">
          <h2>Par Niche</h2>
          {_build_niches_table(niches)}
        </div>

        <div class="card">
          <h2>Positions ouvertes ({len(open_positions)})</h2>
          {_build_positions_table(open_positions)}
        </div>

        <div class="card">
          <h2>Améliorations potentielles</h2>
          {_build_suggestions_section(suggestions)}
        </div>
        """

        html = _html_wrap(
            title=f"📅 Bilan hebdomadaire — {week_label}",
            subtitle="Rapport automatique hebdomadaire",
            body_html=body,
        )
        subject = f"📅 Polymarket Bot — Bilan hebdomadaire {week_label}"
        return self._send_email(subject, html)

    def send_circuit_breaker_alert(self, reason: str, capital: float = None) -> bool:
        """Send an urgent circuit breaker alert email with red styling."""
        capital_str = f"{capital:.2f}&euro;" if capital is not None else 'N/A'
        timestamp = datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')

        body = f"""
        <div style="background:#2a0a0a;border:2px solid {_RED};border-radius:8px;padding:24px;margin:16px 0;text-align:center;">
          <div style="font-size:48px;margin-bottom:8px;">🚨</div>
          <h2 style="color:{_RED};font-size:22px;margin:0 0 8px;text-transform:uppercase;letter-spacing:1px;">
            Circuit Breaker Activé
          </h2>
          <p style="color:{_MUTED};font-size:13px;margin:0 0 16px;">{timestamp}</p>
        </div>

        <div class="card" style="border-color:{_RED};">
          <h2 style="color:{_RED};">Raison</h2>
          <p style="font-size:15px;color:{_TEXT};line-height:1.6;margin:0;">{reason}</p>
        </div>

        <div class="card" style="border-color:{_ORANGE};">
          <h2 style="color:{_ORANGE};">Capital restant</h2>
          <p style="font-size:28px;font-weight:bold;color:{_ORANGE};margin:0;">{capital_str}</p>
        </div>

        <div style="background:#1a1a0a;border:1px solid {_ORANGE};border-radius:8px;padding:16px;margin:16px 0;">
          <p style="color:{_ORANGE};font-size:13px;margin:0;text-align:center;">
            ⚠️ Toutes les opérations automatiques sont suspendues.
            Vérifiez l'état du bot avant de reprendre.
          </p>
        </div>
        """

        html = _html_wrap(
            title="🚨 CIRCUIT BREAKER ACTIVÉ",
            subtitle="Alerte urgente — action requise",
            body_html=body,
        )
        subject = "🚨 URGENT — Polymarket Bot Circuit Breaker Activé"
        return self._send_email(subject, html)
