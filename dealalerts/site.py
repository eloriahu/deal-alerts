"""Build the static dashboard page."""

import html
import logging
from datetime import datetime
from pathlib import Path
from string import Template
from typing import Any, Dict, List, Mapping, Optional
from urllib.parse import urlsplit

from dealalerts.config import Config
from dealalerts.models import REGION_LABELS, REGIONS
from dealalerts.notify import money
from dealalerts.store import State

logger = logging.getLogger(__name__)

_PAGE = Template("""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Deal Alerts</title>
<style>
:root { --bg:#f7f6f3; --card:#ffffff; --ink:#1d1c1a; --soft:#6b6862; --line:#e2dfd8;
        --hot:#b3261e; --hot-bg:#fdecea; --ok:#1e7d46; --accent:#1f4fd8; }
@media (prefers-color-scheme: dark) {
  :root { --bg:#151412; --card:#1f1e1b; --ink:#f1efe9; --soft:#a29e95; --line:#34322d;
          --hot:#ff8a80; --hot-bg:#3a1d1a; --ok:#6fd39a; --accent:#8fb0ff; } }
* { box-sizing:border-box; }
body { margin:0; background:var(--bg); color:var(--ink);
       font:16px/1.45 system-ui, -apple-system, "Segoe UI", sans-serif; }
main { max-width:860px; margin:0 auto; padding:20px 16px 48px; }
h1 { font-size:1.4rem; margin:0 0 4px; }
.checked { color:var(--soft); font-size:.9rem; margin:0 0 16px; }
nav { display:flex; gap:6px; overflow-x:auto; padding-bottom:4px; margin-bottom:16px; }
nav button { flex:none; padding:8px 14px; border:1px solid var(--line); border-radius:999px;
             background:var(--card); color:var(--ink); font:inherit; cursor:pointer; }
nav button[aria-selected="true"] { background:var(--ink); color:var(--bg); border-color:var(--ink); }
section h2 { font-size:1.1rem; margin:24px 0 8px; }
.deal { background:var(--card); border:1px solid var(--line); border-radius:10px;
        padding:12px 14px; margin-bottom:8px; }
.deal.glitch { border-color:var(--hot); background:var(--hot-bg); }
.deal a { color:var(--accent); text-decoration:none; font-weight:600; overflow-wrap:anywhere; }
.badge { color:var(--hot); font-size:.75rem; font-weight:700; letter-spacing:.04em; }
.price { font-variant-numeric:tabular-nums; margin-top:2px; }
.meta { color:var(--soft); font-size:.85rem; margin-top:2px; }
.empty { color:var(--soft); }
.health { margin-top:12px; font-size:.85rem; color:var(--soft); }
.source { display:block; }
.source.ok::before { content:"● "; color:var(--ok); }
.source.bad::before { content:"● "; color:var(--hot); }
</style>
</head>
<body>
<main>
<h1>Deal Alerts</h1>
<p class="checked">Last check: <time datetime="$checked">$checked_text</time></p>
<nav role="tablist">$tabs</nav>
$panes
</main>
<script>
(function () {
  var tabs = document.querySelectorAll('[data-tab]');
  var panes = document.querySelectorAll('[data-region]');
  function show(region) {
    tabs.forEach(function (t) { t.setAttribute('aria-selected', String(t.dataset.tab === region)); });
    panes.forEach(function (p) { p.hidden = p.dataset.region !== region; });
    try { localStorage.setItem('region', region); } catch (e) {}
  }
  tabs.forEach(function (t) { t.addEventListener('click', function () { show(t.dataset.tab); }); });
  var saved = null;
  try { saved = localStorage.getItem('region'); } catch (e) {}
  show(saved && document.querySelector('[data-region="' + saved + '"]') ? saved : 'sg');
  document.querySelectorAll('time[datetime]').forEach(function (el) {
    var mins = Math.round((Date.now() - new Date(el.getAttribute('datetime'))) / 60000);
    if (isNaN(mins) || mins < 0) { return; }
    el.textContent = mins < 60 ? mins + ' min ago'
      : mins < 1440 ? Math.round(mins / 60) + ' h ago' : Math.round(mins / 1440) + ' d ago';
  });
})();
</script>
</body>
</html>
""")


def _safe_link(url: str) -> str:
    """Return the url unchanged if it uses http or https, else an empty string."""
    return url if urlsplit(url).scheme in ("http", "https") else ""


def _stamp(iso: str) -> str:
    """Render an ISO timestamp as an escaped <time> element."""
    return f'<time datetime="{html.escape(iso)}">{html.escape(iso[:16].replace("T", " "))} UTC</time>'


def _deal_label(deal: Any) -> str:
    """A short, safe identifier for a stored deal, for use only in log messages.

    Never raises. Never returns the deal's title, link, or any other feed text.
    """
    if isinstance(deal, dict):
        deal_id = deal.get("id")
        if isinstance(deal_id, str) and deal_id:
            return deal_id
    return "unknown"


def _deal_html(deal: Mapping[str, Any]) -> str:
    """Render one deal record as an HTML card, escaping all feed-sourced text."""
    title = html.escape(deal["title"])
    link = _safe_link(deal["link"])
    heading = f'<a href="{html.escape(link)}" rel="noopener noreferrer">{title}</a>' if link else title
    glitch = deal.get("kind") == "glitch"
    badge = '<div class="badge">SUSPECTED PRICE ERROR</div>' if glitch else ""
    price_bits: List[str] = []
    if deal.get("price_now") is not None:
        price_bits.append(money(deal.get("currency"), deal["price_now"]))
    if deal.get("usual_price") is not None:
        price_bits.append(f"usual {money(deal.get('currency'), deal['usual_price'])}")
    if deal.get("discount_pct") is not None:
        price_bits.append(f"-{deal['discount_pct']:.0f}%")
    price = f'<div class="price">{html.escape(" · ".join(price_bits))}</div>' if price_bits else ""
    meta_bits = [deal["shop"]] if deal.get("shop") else []
    meta_bits.append(deal["source"])
    meta = html.escape(" · ".join(meta_bits))
    return (
        f'<div class="deal{" glitch" if glitch else ""}">{badge}{heading}{price}'
        f'<div class="meta">{meta} · {_stamp(deal["first_seen"])}</div></div>'
    )


def _health_html(region: str, config: Config, health: Dict[str, Dict[str, Any]]) -> str:
    """Render the per-source health lines for one region."""
    lines: List[str] = []
    for source in config.sources:
        if source.region != region:
            continue
        entry = health.get(source.name, {})
        name = html.escape(source.name)
        if entry.get("fails"):
            error = html.escape(entry.get("last_error", ""))
            lines.append(f'<span class="source bad">{name}: failing ({error})</span>')
        elif entry.get("last_ok"):
            lines.append(f'<span class="source ok">{name}: read {_stamp(entry["last_ok"])}</span>')
        else:
            lines.append(f'<span class="source bad">{name}: not read yet</span>')
    return f'<div class="health">{"".join(lines)}</div>'


def build_site(state: State, config: Config, now: datetime, out_dir: Path) -> Path:
    """Write index.html into out_dir and return its path.

    State.__init__ is the single place that drops malformed stored deals, so this
    function never inspects or rewrites state.deals itself. Rendering reads deals
    through a disposable State built from state.deals, which reuses that same
    validation without ever touching the caller's state object. The per-region and
    per-row guards below are only a last line of defence against a deal appended
    after loading (bypassing that validation), never the normal path.
    """
    days = config.settings.dashboard_days
    tabs = "".join(
        f'<button role="tab" data-tab="{region}">{REGION_LABELS[region]}</button>'
        for region in REGIONS
    )
    validated = State({"deals": state.deals})
    panes: List[str] = []
    for region in REGIONS:
        try:
            deals = validated.deals_for(region)
        except (KeyError, TypeError):
            logger.warning("Skipping region %s: its stored deals could not be sorted", region)
            deals = []
        rows: List[str] = []
        for deal in deals:
            row: Optional[str] = None
            try:
                row = _deal_html(deal)
            except (KeyError, TypeError, ValueError, AttributeError):
                row = None
            if row is None:
                logger.warning(
                    "Skipping a stored deal that could not be shown: %s", _deal_label(deal)
                )
                continue
            rows.append(row)
        body = "".join(rows) or f'<p class="empty">Nothing in the last {days} days.</p>'
        panes.append(
            f'<section data-region="{region}"><h2>{REGION_LABELS[region]}</h2>'
            f"{body}{_health_html(region, config, state.health)}</section>"
        )

    page = _PAGE.substitute(
        checked=now.isoformat(),
        checked_text=now.strftime("%Y-%m-%d %H:%M UTC"),
        tabs=tabs,
        panes="\n".join(panes),
    )
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / "index.html"
    target.write_text(page, encoding="utf-8")
    return target
