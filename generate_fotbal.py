#!/usr/bin/env python3
"""
SureBets Football Bot – generates fotbals.json
Runs daily at 6:00 UTC via GitHub Actions.

NEW STRATEGY (stats-first):
  1. Statisticky najdi zápasy s vysokým potenciálem na Over 3.5
     (expected total ≥ 3.0 gólů, ready_35 ≥ 0.85, + 2H aktivita)
  2. U vybraných ověř, že kurz Over 1.5 je ≥ 1.12 (value-gate)
  3. Tipni Over 1.5 jako "tutovku" (P ~ 90 %+)

Tips.json zachovává starou Over 2.5 logiku (kurz 1.60–1.80) jako vedlejší výstup.

SETUP:
  1. Copy this file to the root of cubin-star/cubin-star.github.io
  2. Copy tools/github-actions-surebets.yml to .github/workflows/
  3. In repo Settings → Secrets → Actions, add:
     API_FOOTBALL_KEY1 = your API key
"""

import json
import math
import os
import random
import re
import time
import urllib.request
from datetime import datetime, timedelta, timezone

# ===== CONFIG =====
API_KEY = os.environ.get("API_FOOTBALL_KEY1", "")
BASE_URL = "https://v3.football.api-sports.io"
DELAY = 0.3
OUTPUT = "fotbals.json"
OUTPUT_LIVE = "live.json"
OUTPUT_LIVE2 = "live2.json"
OUTPUT_TIPS = "tips.json"
MAX_TIPS = 2

MIN_ODDS = 1.60          # Over 2.5 range (used only for tips.json filler)
MAX_ODDS = 1.80
MIN_ODDS_15_OUT = 1.12   # NEW: value-gate na výstupu (Over 1.5 musí být ≥ 1.12)
MIN_GAMES = 5            # min. odehraných zápasů (sníženo 6→5 – chytí víc kandidátů bez ztráty kvality)

# === Stats-first kritéria pro "kandidáta na Over 3.5" ===
# Cíl: expected total ≥ 3.0 gólů (P(Over 3.5) ~ 35 %, P(Over 1.5) ~ 90 %+)
MIN_TOTAL_AVG = 2.60     # tvrdý gate: (h_for+a_for+h_agn+a_agn)/2 ≥ 2.6 (sníženo 2.8→2.6)
MIN_DEFENSE_LEAK = 1.00  # aspoň jeden tým inkasuje ≥ 1.0 g/z (musí být odkud góly brát)
MIN_READY_35 = 0.85      # kompozitní index "Over 3.5 readiness"

# === NEW: Poissonova pravděpodobnost Over 3.5 (per varianta) ===
# expected total (λ) → P(Over 3.5) přes nezávislé Poisson rozdělení.
# Diferenciace podle profilu zápasu:
#   A = jeden silný útok + děravé obrany → výchozí
#   B = oba útočí + jedna obrana děravější (asymetrie v obraně)
# (Variant C "open shootout" byl odstraněn – v praxi propadal.)
MIN_P35_BY_VARIANT = {
    "A": 0.30,
    "B": 0.30,
}

# === Asymetrický defenzivní filtr (chrání před "1:0 pastmi") ===
# Pokud má jeden tým výrazně lepší obranu (= nízké inkasované g/z), zápas snadno
# sklouzne k 0:0 / 1:0, i když celkové expected total vypadá dobře.
# Příklad: Lugano (1.10 inkasuje) vs YB (2.40 inkasuje) → λ=3.55 vypadalo solidně,
# ale Lugano zavřelo zápas 1:0. Proto: pokud min(h_agn,a_agn) < threshold,
# zvedneme práh p35 o bonus (přísnější propuštění).
ASYMMETRIC_DEF_THRESHOLD = 1.35   # g/z – pokud lepší obrana je pod tímto, je "tight" (zvýšeno 1.30→1.35)
ASYMMETRIC_DEF_GAP_MIN = 0.80     # rozdíl obran (slabší - silnější) ≥ 0.80 g/z = výrazná asymetrie
ASYMMETRIC_P35_BONUS = 0.07       # +7 pp k MIN_P35_BY_VARIANT[tag]

# === Tight-both defensive filter (NEW) ===
# Chrání před scénáři typu Sunderland–MU 0:0 nebo Yamaga–Fujieda 0:0:
# OBĚ obrany jsou symetricky pevné (asym filtr neaktivní, protože gap je malý),
# ale zápas se přesto snadno zavře 0:0 / 1:0. Když min(conc) ≤ TIGHT_BOTH_MIN_MAX
# A ZÁROVEŇ max(conc) ≤ TIGHT_BOTH_MAX_MAX (= obě obrany pevné), zvedneme min_p35.
TIGHT_BOTH_MIN_MAX = 1.30   # lepší z obou obran je pod tímto = tight
TIGHT_BOTH_MAX_MAX = 1.50   # i horší z obou obran je pod tímto = obě tight
TIGHT_BOTH_P35_BONUS = 0.10 # +10 pp k MIN_P35_BY_VARIANT[tag]

# League-relative ratios (mírně zostřeno proti původnímu Over 2.5 botu)
BOTH_FLOOR_R = 0.85      # oba alespoň 85% baseline
STRONG_MIN_R = 1.15      # "výrazný" tým 115%+ baseline (z 1.10)
CONTRAST_MAX_R = 0.95    # protějšek pod 95% baseline
MIN_BASELINE = 1.40      # zvýšeno z 1.25 → expected ~3.0+ gólů celkem
MIN_ATTACK = 1.10        # zvýšeno z 0.95 → oba musí reálně střílet (filtruje "Goias" profily)
MIN_2H_BASELINE = 0.55   # zvýšeno z 0.45 → 2H aktivita

# === Pre-match xG gate (NEW) ===
# Pokud API /predictions vrátí očekávané góly pro oba týmy (predictions.goals.home/away
# jsou OBA > 0), vyžadujeme součet ≥ MIN_PREMATCH_XG_TOTAL. Tím se odfiltrují případy,
# kdy sezónní průměry vypadají dobře, ale konkrétní zápas má pre-match xG nízké
# (typicky když outsider venku nemá šanci → 1:0 past).
# Pokud API hodnoty nevrátí (None / 0 / nečíselné) → gate se přeskočí (fallback na
# původní logiku bez xG).
MIN_PREMATCH_XG_TOTAL = 2.5

EXCLUDED_COUNTRIES = {
    "russia",
    "belarus",
    "japan",
    "south-korea",
    "south korea",
    "korea republic",
}

# League blacklist – exact league names from API
EXCLUDED_LEAGUES = {
    "2. liga",           # 2. Slovenská liga
}

# Substring blacklist (case-insensitive) – matched against league name.
# Slouží k blokaci celých kategorií soutěží (např. všech ženských).
EXCLUDED_LEAGUE_SUBSTRINGS = (
    "women",       # EN (UEFA Women's Champions League, NWSL Women, ...)
    "féminine",    # FR
    "feminine",    # FR (bez diakritiky)
    "femenina",    # ES
    "feminina",    # PT/IT
    "frauen",      # DE
    "ženy",        # CZ/SK
    "zeny",        # CZ/SK (bez diakritiky)
)


def is_excluded_fixture(fix):
    """Centrální filtr – platí pro VŠECHNY výstupní JSONy (fotbals/live/live2/tips).

    Blokuje:
      - země v EXCLUDED_COUNTRIES (Rusko, Bělorusko, Japonsko, Jižní Korea)
      - ligy v EXCLUDED_LEAGUES (přesný název)
      - všechny ženské soutěže (název obsahuje výraz z EXCLUDED_LEAGUE_SUBSTRINGS)
    """
    country = (fix.get("country") or "").lower()
    if country in EXCLUDED_COUNTRIES:
        return True
    league = fix.get("league") or ""
    if league in EXCLUDED_LEAGUES:
        return True
    league_lc = league.lower()
    for needle in EXCLUDED_LEAGUE_SUBSTRINGS:
        if needle in league_lc:
            return True
    return False

# === FALLBACK pro tips.json: random Over 2.5 z TOP lig ===
# Pokud po A-poolu i prvním filleru zbývá místo v tips.json, doplníme
# úplně random zápas(y) z TOP first-tier evropských + významných světových
# lig s reálným kurzem Over 2.5 v rozmezí TIPS_FB_MIN_ODDS..TIPS_FB_MAX_ODDS.
TIPS_FB_MIN_ODDS = 1.70
TIPS_FB_MAX_ODDS = 1.90
TIPS_FB_MAX_ATTEMPTS = 25  # max počet zápasů, u kterých zkusíme načíst odds

# API-Football league IDs – first-tier ligy (TOP 5 + top evropské + světové)
# Ty mají v poolu PRIORITU (zkouší se jako první).
TIPS_FB_TOP_LEAGUE_IDS = {
    39,   # Premier League (England)
    140,  # La Liga (Spain)
    135,  # Serie A (Italy)
    78,   # Bundesliga (Germany)
    61,   # Ligue 1 (France)
    88,   # Eredivisie (Netherlands)
    94,   # Primeira Liga (Portugal)
    144,  # Jupiler Pro League (Belgium)
    203,  # Süper Lig (Turkey)
    197,  # Super League (Greece)
    207,  # Super League (Switzerland)
    218,  # Bundesliga (Austria)
    119,  # Superliga (Denmark)
    103,  # Eliteserien (Norway)
    113,  # Allsvenskan (Sweden)
    106,  # Ekstraklasa (Poland)
    345,  # Czech Liga (Czech Republic)
    71,   # Brasileirão Série A (Brazil)
    128,  # Liga Profesional (Argentina)
    253,  # MLS (USA)
    262,  # Liga MX (Mexico)
    188,  # A-League (Australia)
    2,    # UEFA Champions League
    3,    # UEFA Europa League
    848,  # UEFA Europa Conference League
}

# Druholigové / sekundární soutěže – použijí se až když TOP nestačí.
TIPS_FB_SECOND_TIER_LEAGUE_IDS = {
    40,   # Championship (England)
    141,  # La Liga 2 (Spain)
    136,  # Serie B (Italy)
    79,   # 2. Bundesliga (Germany)
    62,   # Ligue 2 (France)
    89,   # Eerste Divisie (Netherlands)
    95,   # Liga Portugal 2
    145,  # Challenger Pro League (Belgium)
    204,  # 1. Lig (Turkey)
    208,  # Challenge League (Switzerland)
    219,  # 2. Liga (Austria)
    120,  # 1st Division (Denmark)
    104,  # OBOS-ligaen (Norway)
    114,  # Superettan (Sweden)
    107,  # I Liga (Poland)
    346,  # FNL (Czech Republic)
    72,   # Brasileirão Série B (Brazil)
    129,  # Primera Nacional (Argentina)
    254,  # USL Championship (USA)
    263,  # Liga de Expansión MX (Mexico)
}

# Country-specific whitelist
# only the specified leagues are allowed (all others blocked)
ALLOWED_LEAGUES_BY_COUNTRY = {
    "poland": {"Superliga", "Ekstraklasa", "I Liga"},
}

request_count = 0


# ===== API =====

def api_get(endpoint, params):
    global request_count
    query = "&".join(f"{k}={v}" for k, v in params.items())
    url = f"{BASE_URL}/{endpoint}?{query}"
    req = urllib.request.Request(url)
    req.add_header("x-apisports-key", API_KEY)
    for attempt in range(1, 4):
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                request_count += 1
                remaining = resp.headers.get("x-ratelimit-requests-remaining", "?")
                print(f" [{remaining}]", end="")
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(5 * attempt)
            else:
                print(f" HTTP{e.code}", end="")
                return {}
        except Exception:
            return {}
    return {}


def fetch_fixtures(date_str):
    print(f"  Fixtures {date_str}...", end="")
    data = api_get("fixtures", {"date": date_str, "timezone": "UTC"})
    fixtures = {}
    for f in data.get("response", []):
        fid = f.get("fixture", {}).get("id")
        if not fid:
            continue
        status = f.get("fixture", {}).get("status", {}).get("short", "")
        if status not in ("NS", "TBD", ""):
            continue
        fixtures[fid] = {
            "home": f.get("teams", {}).get("home", {}).get("name", "?"),
            "away": f.get("teams", {}).get("away", {}).get("name", "?"),
            "home_id": f.get("teams", {}).get("home", {}).get("id", 0),
            "away_id": f.get("teams", {}).get("away", {}).get("id", 0),
            "league": f.get("league", {}).get("name", "?"),
            "league_id": f.get("league", {}).get("id", 0),
            "season": f.get("league", {}).get("season", 2025),
            "country": f.get("league", {}).get("country", "?"),
            "kickoff": f.get("fixture", {}).get("date", ""),
        }
    print(f" {len(fixtures)} upcoming")
    return fixtures


def fetch_league_odds(league_id, season, date_str):
    """Fetch odds for a specific league/season/date (paginated, like Kombik)."""
    all_items = []
    page = 1
    while True:
        time.sleep(DELAY)
        data = api_get("odds", {
            "league": str(league_id),
            "season": str(season),
            "date": date_str,
            "bet": "5",
            "page": str(page),
        })
        items = data.get("response", [])
        paging = data.get("paging", {})
        total_pages = paging.get("total", 1)
        if items:
            all_items.extend(items)
        if page >= total_pages or not items:
            break
        page += 1
    return all_items


def fetch_prediction(fixture_id):
    """Single API call returns stats for BOTH teams."""
    time.sleep(DELAY)
    data = api_get("predictions", {"fixture": str(fixture_id)})
    items = data.get("response", [])
    return items[0] if items else None


def fetch_team_stats(league_id, season, team_id):
    """Fetch team statistics – fallback when /predictions has no data."""
    time.sleep(DELAY)
    data = api_get("teams/statistics", {
        "league": str(league_id),
        "season": str(season),
        "team": str(team_id),
    })
    return data.get("response")


def build_pred_from_stats(home_stats, away_stats):
    """Convert /teams/statistics responses into the same structure
    that /predictions returns, so meets_criteria() works unchanged."""
    if not home_stats or not away_stats:
        return None
    return {
        "teams": {
            "home": {"league": home_stats},
            "away": {"league": away_stats},
        }
    }


# ===== CRITERIA =====

def _sf(val, default=0.0):
    try:
        return float(val) if val is not None else default
    except (ValueError, TypeError):
        return default


def poisson_p_over(lam, line):
    """P(total > line) pro nezáporný integer total ~ Poisson(lam).
    Pro line=3.5 vrací P(total ≥ 4) = 1 - sum_{k=0..3} e^-λ λ^k / k!.
    """
    if lam <= 0:
        return 0.0
    k_max = int(math.floor(line))  # pro 3.5 → 3
    cdf = 0.0
    term = math.exp(-lam)  # k=0
    cdf += term
    for k in range(1, k_max + 1):
        term *= lam / k
        cdf += term
    p = 1.0 - cdf
    if p < 0.0:
        return 0.0
    if p > 1.0:
        return 1.0
    return p


def get_half_stats(team_data, side):
    """Extract 1st/2nd half goal averages from minute breakdown."""
    minute = team_data.get("league", {}).get("goals", {}).get(side, {}).get("minute", {})
    played = int(_sf(team_data.get("league", {}).get("fixtures", {}).get("played", {}).get("total", 0)))
    if not minute or played == 0:
        return None
    val = lambda k: int(_sf(minute.get(k, {}).get("total", 0)))
    first_half = val("0-15") + val("16-30") + val("31-45")
    second_half = val("46-60") + val("61-75") + val("76-90")
    return {"first": first_half, "second": second_half,
            "avg_first": first_half / played, "avg_second": second_half / played, "played": played}


def estimate_o25_from_o15(o15):
    """Dopočte odhadovaný kurz Over 2.5 z kurzu Over 1.5 přes Poissonovu inverzi.

    Postup:
      1. Implicitní pravděpodobnost P(O1.5) ≈ 1/o15.
      2. Najdi λ (expected total) tak, aby P_Poisson(X≥2 | λ) = P(O1.5).
      3. Spočítej P(O2.5) = P(X≥3 | λ) a vrať odhad o25 ≈ 1/P(O2.5).
    Bez korekce bookmakerské marže – pro orientační účely tips.json to stačí.
    Vrací None pokud o15 mimo rozumný rozsah."""
    if o15 is None or o15 <= 1.001:
        return None
    p_o15 = 1.0 / o15
    if not (0.05 < p_o15 < 0.999):
        return None

    # Binární search λ tak, aby poisson_p_over(λ, 1.5) ≈ p_o15
    lo, hi = 0.1, 12.0
    for _ in range(60):
        mid = (lo + hi) / 2
        if poisson_p_over(mid, 1.5) < p_o15:
            lo = mid
        else:
            hi = mid
    lam = (lo + hi) / 2

    p_o25 = poisson_p_over(lam, 2.5)
    if p_o25 <= 1e-6:
        return None
    o25 = 1.0 / p_o25
    # Bezpečné meze pro výstup
    return max(1.05, min(o25, 25.0))


def meets_criteria(pred):
    """
    Stats-first kritéria: kandidát na Over 3.5 → tutovka Over 1.5.

    Brány (zápas musí projít VŠEMI):
      1) Absolutní gate: expected total ≥ MIN_TOTAL_AVG (3.0 gólů)
                       + aspoň jeden tým inkasuje ≥ MIN_DEFENSE_LEAK
                       + oba útočí ≥ MIN_ATTACK
                       + baseline ≥ MIN_BASELINE
                       + odehráno ≥ MIN_GAMES
      2) Profilový kontrast (Variant A nebo B na celkových datech)
      3) 2H aktivita (Variant A nebo B na 2H datech)
      4) Kompozitní skóre ready_35 ≥ MIN_READY_35

    Vrací: (ok, detail_str, score)
    """
    home = pred.get("teams", {}).get("home", {})
    away = pred.get("teams", {}).get("away", {})
    if not home or not away:
        return False, "", 0.0

    h_played = int(_sf(home.get("league", {}).get("fixtures", {}).get("played", {}).get("total", 0)))
    a_played = int(_sf(away.get("league", {}).get("fixtures", {}).get("played", {}).get("total", 0)))
    if h_played < MIN_GAMES or a_played < MIN_GAMES:
        return False, f"too few games: {h_played}/{a_played}", 0.0

    # Total (sezónní) průměry – robustnější vzorek než home/away split,
    # který v API často chybí nebo má extrémně malý počet zápasů.
    h_for = _sf(home.get("league", {}).get("goals", {}).get("for", {}).get("average", {}).get("total"))
    a_for = _sf(away.get("league", {}).get("goals", {}).get("for", {}).get("average", {}).get("total"))
    h_agn = _sf(home.get("league", {}).get("goals", {}).get("against", {}).get("average", {}).get("total"))
    a_agn = _sf(away.get("league", {}).get("goals", {}).get("against", {}).get("average", {}).get("total"))

    if h_for == 0 and a_for == 0:
        return False, "", 0.0

    # === BRÁNA 0: Pre-match xG (pokud k dispozici z /predictions) ===
    # API-Football vrací v "predictions.goals.home/away" očekávané góly pro daný
    # konkrétní zápas (jinak sezónní průměry mohou skrývat slabou aktuální formu).
    # Hodnoty mohou být float, string ("1.85"), nebo None ("-"). Akceptujeme jen
    # případ, kdy OBA jsou platná čísla > 0; jinak gate přeskočíme (fallback).
    pg = pred.get("predictions", {}).get("goals", {}) if isinstance(pred.get("predictions"), dict) else {}
    xg_h = _sf(pg.get("home"), default=-1.0)
    xg_a = _sf(pg.get("away"), default=-1.0)
    if xg_h > 0 and xg_a > 0:
        xg_total = xg_h + xg_a
        if xg_total < MIN_PREMATCH_XG_TOTAL:
            return False, (f"pre-match xG too low: {xg_h:.2f}+{xg_a:.2f}={xg_total:.2f} "
                           f"< {MIN_PREMATCH_XG_TOTAL}"), 0.0

    # === BRÁNA 1: Absolutní gate ===
    if h_for < MIN_ATTACK or a_for < MIN_ATTACK:
        return False, f"weak attack: {h_for:.2f}/{a_for:.2f} (min {MIN_ATTACK})", 0.0

    # Expected total = h_for (góly domácích) + a_for (góly hostů)
    #                  + průměrná inkasovaná stránka (sanity přes obranu)
    # Použijeme klasický odhad: total = (h_for + a_agn)/2 + (a_for + h_agn)/2
    total_avg = (h_for + a_agn) / 2 + (a_for + h_agn) / 2
    if total_avg < MIN_TOTAL_AVG:
        return False, f"total too low: {total_avg:.2f} < {MIN_TOTAL_AVG}", 0.0

    if max(h_agn, a_agn) < MIN_DEFENSE_LEAK:
        return False, f"no defense leak: {h_agn:.2f}/{a_agn:.2f} (need ≥{MIN_DEFENSE_LEAK})", 0.0

    baseline = (h_for + a_for + h_agn + a_agn) / 4
    if baseline < MIN_BASELINE:
        return False, f"baseline too low: {baseline:.2f} < {MIN_BASELINE}", 0.0

    # === BRÁNA 2: Profilový kontrast (Variant A/B) ===
    # Pozn.: Variant C ("open shootout" – oba útočí i inkasují, bez kontrastu) byl
    # odstraněn z výběru, protože v praxi propadal (často Under 1.5). Necháváme jen
    # asymetrické profily A (silný útok vs děravá obrana) a B (silná obrana vs slabá obrana).
    both_floor = baseline * BOTH_FLOOR_R
    strong_min = baseline * STRONG_MIN_R
    contrast_max = baseline * CONTRAST_MAX_R

    variant_a = (
        h_agn >= both_floor and a_agn >= both_floor
        and ((h_for >= strong_min and a_for < contrast_max)
             or (a_for >= strong_min and h_for < contrast_max))
    )
    variant_b = (
        h_for >= both_floor and a_for >= both_floor
        and ((h_agn >= strong_min and a_agn < contrast_max)
             or (a_agn >= strong_min and h_agn < contrast_max))
    )

    if not (variant_a or variant_b):
        return False, (f"profile fail: scored {h_for:.2f}/{a_for:.2f}, "
                       f"conceded {h_agn:.2f}/{a_agn:.2f} (base={baseline:.2f})"), 0.0

    # === BRÁNA 3: 2H aktivita ===
    h2f = get_half_stats(home, "for")
    a2f = get_half_stats(away, "for")
    h2a = get_half_stats(home, "against")
    a2a = get_half_stats(away, "against")

    if not h2f or not a2f or not h2a or not a2a:
        return False, "no minute breakdown", 0.0

    h_scr_2h = h2f["avg_second"]
    a_scr_2h = a2f["avg_second"]
    h_con_2h = h2a["avg_second"]
    a_con_2h = a2a["avg_second"]

    base_2h = (h_scr_2h + a_scr_2h + h_con_2h + a_con_2h) / 4
    if base_2h < MIN_2H_BASELINE:
        return False, (f"2H low base: {base_2h:.2f} < {MIN_2H_BASELINE}"), 0.0

    # === BRÁNA 4: Kompozitní ready_35 score ===
    # 0.50 expected total, 0.20 min(útok), 0.15 max(obrana inkasuje), 0.10 2H base, 0.05 zatím 0
    ready_35 = (
        0.50 * (total_avg / 3.5)
        + 0.20 * (min(h_for, a_for) / 1.0)
        + 0.15 * (max(h_agn, a_agn) / 1.5)
        + 0.10 * (base_2h / 1.0)
        + 0.05 * 1.0  # placeholder pro budoucí BTTS%
    )
    if ready_35 < MIN_READY_35:
        return False, f"ready_35 too low: {ready_35:.2f} < {MIN_READY_35}", 0.0

    tag = "A" if variant_a else "B"

    # === BRÁNA 5: Poissonova P(Over 3.5) – diferenciovaná podle varianty ===
    p35 = poisson_p_over(total_avg, 3.5)
    min_p35 = MIN_P35_BY_VARIANT.get(tag, 0.45)

    # Asymetrický defenzivní filtr: pokud jeden tým má výrazně lepší obranu
    # (lepší obrana < threshold A rozdíl obran ≥ gap_min), zvedneme práh p35.
    # Chrání před scénáři typu "silnější obrana zavře zápas 1:0".
    def_min = min(h_agn, a_agn)
    def_max = max(h_agn, a_agn)
    def_gap = abs(h_agn - a_agn)
    asym_tag = ""
    if def_min < ASYMMETRIC_DEF_THRESHOLD and def_gap >= ASYMMETRIC_DEF_GAP_MIN:
        min_p35 += ASYMMETRIC_P35_BONUS
        asym_tag = f" ASYM(def_min={def_min:.2f},gap={def_gap:.2f},+{ASYMMETRIC_P35_BONUS*100:.0f}pp)"

    # Tight-both filter: obě obrany symetricky pevné → vyšší riziko 0:0 / 1:0.
    # Aktivuje se i když asym neaktivní (typicky malý gap mezi obranami).
    tight_tag = ""
    if def_min <= TIGHT_BOTH_MIN_MAX and def_max <= TIGHT_BOTH_MAX_MAX:
        min_p35 += TIGHT_BOTH_P35_BONUS
        tight_tag = (f" TIGHT(def_min={def_min:.2f},def_max={def_max:.2f},"
                     f"+{TIGHT_BOTH_P35_BONUS*100:.0f}pp)")

    if p35 < min_p35:
        return False, (f"[{tag}] p35 too low: {p35*100:.1f}% < {min_p35*100:.0f}% "
                       f"(λ={total_avg:.2f}){asym_tag}{tight_tag}"), 0.0

    detail = (f"[{tag}] total={total_avg:.2f} p35={p35*100:.0f}%≥{min_p35*100:.0f}% "
              f"ready={ready_35:.2f} "
              f"| scored {h_for:.2f}/{a_for:.2f}, conceded {h_agn:.2f}/{a_agn:.2f} "
              f"| 2H base={base_2h:.2f} (base={baseline:.2f}){asym_tag}{tight_tag}")
    return True, detail, ready_35


# ===== CANDIDATES =====

def compute_odds_for_fixtures(odds_data, fixture_ids):
    """
    Z odds dat (per liga+datum) spočítá průměrný kurz Over 1.5 a Over 2.5
    napříč všemi bookmakery – pouze pro vybrané fixture_ids.
    Vrací: { fixture_id: {"o15": float|None, "o25": float|None} }
    """
    result = {}
    for item in odds_data:
        fid = item.get("fixture", {}).get("id")
        if fid not in fixture_ids:
            continue
        all_o15 = []
        all_o25 = []
        for bk in item.get("bookmakers", []):
            for bet in bk.get("bets", []):
                for val in bet.get("values", []):
                    v = str(val.get("value", ""))
                    try:
                        odd_val = float(val.get("odd", "0"))
                    except (ValueError, TypeError):
                        continue
                    if v == "Over 1.5" and odd_val > 0:
                        all_o15.append(odd_val)
                    elif v == "Over 2.5" and odd_val > 0:
                        all_o25.append(odd_val)
        result[fid] = {
            "o15": (sum(all_o15) / len(all_o15)) if all_o15 else None,
            "o25": (sum(all_o25) / len(all_o25)) if all_o25 else None,
        }
    return result


def pick_random_top_league_tips(all_fixtures, exclude_keys, exclude_leagues, need):
    """
    FALLBACK pro tips.json: úplně random Over 2.5 z TOP first-tier lig.

    Z dostupných fixtures (24h okno) vybere náhodně zápasy z TIPS_FB_TOP_LEAGUE_IDS,
    načte jejich Over 2.5 kurzy a vrátí ty s kurzem v rozmezí
    TIPS_FB_MIN_ODDS..TIPS_FB_MAX_ODDS. Maximum: ``need`` zápasů.
    """
    if need <= 0 or not all_fixtures:
        return []

    now2 = datetime.now(timezone.utc)
    cutoff = now2 + timedelta(hours=24)

    # 1) Kandidáti: TOP + 2nd-tier ligy + 24h okno + nejsou už použiti.
    #    Rozlišujeme 'tier' (1 = first-tier, 2 = second-tier) kvůli prioritizaci.
    top_candidates = []
    second_candidates = []
    for fid, fix in all_fixtures.items():
        lid = fix.get("league_id")
        if lid in TIPS_FB_TOP_LEAGUE_IDS:
            tier = 1
        elif lid in TIPS_FB_SECOND_TIER_LEAGUE_IDS:
            tier = 2
        else:
            continue
        if is_excluded_fixture(fix):
            continue
        match_str = f"{fix.get('home', '?')} vs {fix.get('away', '?')}"
        kickoff_str = fix.get("kickoff", "")
        if (match_str, kickoff_str) in exclude_keys:
            continue
        if fix.get("league", "") in exclude_leagues:
            continue
        if kickoff_str:
            try:
                kdt = datetime.fromisoformat(kickoff_str.replace("Z", "+00:00"))
                if kdt < now2 or kdt > cutoff:
                    continue
            except ValueError:
                pass
        (top_candidates if tier == 1 else second_candidates).append((fid, fix))

    if not top_candidates and not second_candidates:
        print("  Tips fallback: no fixtures in TOP / 2nd-tier leagues for next 24h")
        return []

    random.shuffle(top_candidates)
    random.shuffle(second_candidates)
    candidates = top_candidates + second_candidates  # 1. ligy mají prioritu
    print(f"  Tips fallback: trying random Over 2.5 from "
          f"{len(top_candidates)} TOP + {len(second_candidates)} 2nd-tier fixtures "
          f"(odds {TIPS_FB_MIN_ODDS}-{TIPS_FB_MAX_ODDS}, need={need}, max_attempts={TIPS_FB_MAX_ATTEMPTS})...")

    picked = []
    used_leagues = set()
    attempts = 0
    for fid, fix in candidates:
        if len(picked) >= need:
            break
        if attempts >= TIPS_FB_MAX_ATTEMPTS:
            break
        attempts += 1
        league_name = fix.get("league", "?")
        if league_name in used_leagues:
            continue
        date_part = fix.get("kickoff", today_str())[:10]
        items = fetch_league_odds(fix["league_id"], fix["season"], date_part)
        odds_map = compute_odds_for_fixtures(items, {fid})
        o25 = odds_map.get(fid, {}).get("o25")
        match_str = f"{fix.get('home', '?')} vs {fix.get('away', '?')}"
        if o25 is None:
            print(f"    - {match_str[:50]}: no Over 2.5 odds")
            continue
        if not (TIPS_FB_MIN_ODDS <= o25 <= TIPS_FB_MAX_ODDS):
            print(f"    - {match_str[:50]}: O2.5={o25:.2f} mimo rozsah")
            continue
        print(f"    \u2713 {match_str[:50]}: O2.5={o25:.2f} ({league_name})")
        picked.append({
            "League": league_name,
            "Match": match_str,
            "Tip": "Over 2.5",
            "Odds": f"{o25:.2f}",
            "Date": fix.get("kickoff", ""),
        })
        used_leagues.add(league_name)

    return picked


def today_str():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


# ===== MAIN =====

def main():
    if not API_KEY:
        print("API_FOOTBALL_KEY1 not set!")
        return

    now = datetime.now(timezone.utc)
    today = now.strftime("%Y-%m-%d")
    tomorrow = (now + timedelta(days=1)).strftime("%Y-%m-%d")

    print("== SureBets Football Bot (stats-first / Over 3.5 → Over 1.5) ==")
    print(f"Time: {now.strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"Stats gate: total≥{MIN_TOTAL_AVG}, baseline≥{MIN_BASELINE}, "
          f"attack≥{MIN_ATTACK}, ready_35≥{MIN_READY_35}")
    print(f"Pre-match xG gate (when available): total ≥ {MIN_PREMATCH_XG_TOTAL}")
    print(f"Asymmetric def: threshold={ASYMMETRIC_DEF_THRESHOLD}, "
          f"gap≥{ASYMMETRIC_DEF_GAP_MIN}, +{ASYMMETRIC_P35_BONUS*100:.0f}pp to p35")
    print(f"P(O3.5) gate per variant: A≥{MIN_P35_BY_VARIANT['A']*100:.0f}%, "
          f"B≥{MIN_P35_BY_VARIANT['B']*100:.0f}%")
    print(f"Odds gate:  Over 1.5 ≥ {MIN_ODDS_15_OUT}\n")

    # 1. Fixtures
    fixtures_today = fetch_fixtures(today)
    time.sleep(DELAY)
    fixtures_tomorrow = fetch_fixtures(tomorrow)
    all_fixtures = {**fixtures_today, **fixtures_tomorrow}
    print(f"  Total: {len(all_fixtures)} fixtures\n")

    if not all_fixtures:
        print("No fixtures found.")
        with open(OUTPUT, "w", encoding="utf-8") as f:
            json.dump([], f)
        with open(OUTPUT_LIVE, "w", encoding="utf-8") as f:
            json.dump([], f)
        with open(OUTPUT_LIVE2, "w", encoding="utf-8") as f:
            json.dump([], f)
        with open(OUTPUT_TIPS, "w", encoding="utf-8") as f:
            json.dump([], f)
        return

    # 2. Filter fixtures by 24h window + country
    now2 = datetime.now(timezone.utc)
    cutoff = now2 + timedelta(hours=24)
    filtered = {}
    for fid, fix in all_fixtures.items():
        kickoff_str = fix.get("kickoff", "")
        if kickoff_str:
            try:
                kickoff_dt = datetime.fromisoformat(kickoff_str.replace("Z", "+00:00"))
                if kickoff_dt < now2 or kickoff_dt > cutoff:
                    continue
            except ValueError:
                pass
        country = fix.get("country", "").lower()
        if is_excluded_fixture(fix):
            continue
        league = fix.get("league", "")
        if country in ALLOWED_LEAGUES_BY_COUNTRY:
            if league not in ALLOWED_LEAGUES_BY_COUNTRY[country]:
                continue
        filtered[fid] = fix
    print(f"  After filter (24h, country/league): {len(filtered)} fixtures\n")

    # 3. STATS-FIRST: zavolat predictions/stats na VŠECHNY filtered zápasy
    print(f"  [Stage 1/3] Analyzing {len(filtered)} fixtures statistically...")
    qualified = []  # zápasy, které prošly statistickým gate
    for i, (fid, fix) in enumerate(filtered.items()):
        match_str = f"{fix['home']} vs {fix['away']}"
        print(f"  [{i+1}/{len(filtered)}] {match_str[:45]:.<47s}", end="")
        try:
            pred = fetch_prediction(fid)
            if not pred and fix.get("home_id") and fix.get("away_id"):
                print(" pred=∅", end="")
                h_stats = fetch_team_stats(fix["league_id"], fix["season"], fix["home_id"])
                a_stats = fetch_team_stats(fix["league_id"], fix["season"], fix["away_id"])
                pred = build_pred_from_stats(h_stats, a_stats)
            if not pred:
                print(" no data")
                continue
            ok, detail, score = meets_criteria(pred)
            if ok:
                print(f" ★ {detail}")
                # Extrahuj variant tag (A/B/C) z prvních znaků detailu
                m_var = re.match(r"\[([ABC])\]", detail)
                variant = m_var.group(1) if m_var else "?"
                qualified.append({
                    "fixture_id": fid,
                    "League": fix["league"],
                    "Match": match_str,
                    "kickoff": fix["kickoff"],
                    "league_id": fix["league_id"],
                    "season": fix["season"],
                    "_score": score,
                    "_variant": variant,
                })
            else:
                print(f" fail ({detail})")
        except Exception as exc:
            print(f" ERROR: {exc}")

    print(f"\n  Stats-qualified: {len(qualified)} fixtures")

    # 4. ODDS pouze pro kvalifikované zápasy (jen jejich ligy + datumy)
    odds_map = {}
    if qualified:
        league_map = {}
        for q in qualified:
            key = f"{q['league_id']}_{q['season']}"
            if key not in league_map:
                league_map[key] = {
                    "league_id": q["league_id"],
                    "season": q["season"],
                    "name": q["League"],
                    "dates": set(),
                }
            date_part = q["kickoff"][:10] if q["kickoff"] else today
            league_map[key]["dates"].add(date_part)

        qualified_ids = {q["fixture_id"] for q in qualified}
        print(f"\n  [Stage 2/3] Fetching odds for {len(league_map)} leagues "
              f"(only stats-qualified)...")
        all_odds = []
        for i, (key, lg) in enumerate(league_map.items()):
            for d in sorted(lg["dates"]):
                print(f"  [{i+1}/{len(league_map)}] {lg['name'][:40]} ({d})...", end="")
                items = fetch_league_odds(lg["league_id"], lg["season"], d)
                all_odds.extend(items)
                print(f" {len(items)}")
        odds_map = compute_odds_for_fixtures(all_odds, qualified_ids)
        print(f"  Total odds entries collected: {len(all_odds)}\n")

    # 5. Value gate: Over 1.5 ≥ MIN_ODDS_15_OUT
    print(f"  [Stage 3/3] Value gate (Over 1.5 ≥ {MIN_ODDS_15_OUT})...")
    results = []
    for q in qualified:
        odds_info = odds_map.get(q["fixture_id"], {})
        o15 = odds_info.get("o15")
        o25 = odds_info.get("o25")
        if o15 is None:
            print(f"  ✗ {q['Match'][:50]}: no Over 1.5 odds available")
            continue
        # Tolerance 0.005 – kurz 1.119 zaokrouhlený na 1.12 by jinak spadl pod práh.
        if o15 < MIN_ODDS_15_OUT - 0.005:
            print(f"  ✗ {q['Match'][:50]}: O1.5={o15:.2f} < {MIN_ODDS_15_OUT}")
            continue
        print(f"  ✓ {q['Match'][:50]}: O1.5={o15:.2f} (score={q['_score']:.2f})")
        results.append({
            "League": q["League"],
            "Match": q["Match"],
            "Tip": "Over 1.5",
            "Odds": f"{o15:.2f}",
            "Date": q["kickoff"],
            "_score": q["_score"],
            "_o15": o15,
            "_o25": f"{o25:.2f}" if o25 else None,
            "_variant": q.get("_variant", "?"),
        })

    # 6a. Write live.json – ALL value-gate-passing matches (no dedup)
    live_results = sorted(results, key=lambda r: r["Date"])
    live_out = [{k: v for k, v in r.items() if not k.startswith("_")} for r in live_results]
    with open(OUTPUT_LIVE, "w", encoding="utf-8") as f:
        json.dump(live_out, f, indent=2, ensure_ascii=False)
    print(f"\n  Live: {len(live_out)} match(es) \u2192 {OUTPUT_LIVE}")

    # 6a-2. Write live2.json – VŠECHNY zápasy varianty A s tipem Over 2.5
    #       Iterujeme `qualified` (= prošlo statistickým gate), ne `results`,
    #       protože live2 má vlastní účel a nemá ho omezovat value gate na O1.5.
    #       Kurz Over 2.5 dopočítán z Over 1.5 přes Poissonovu inverzi (když o15 chybí,
    #       zápas se přeskočí – bez kurzu nemá odhad smysl).
    live2_out = []
    live2_qualified_a = [q for q in qualified if q.get("_variant") == "A"]
    live2_qualified_a.sort(key=lambda q: q.get("kickoff", ""))
    for q in live2_qualified_a:
        o15 = odds_map.get(q["fixture_id"], {}).get("o15")
        o25_est = estimate_o25_from_o15(o15)
        if o25_est is None:
            print(f"  ⚠ live2 skip {q['Match'][:50]}: o15={o15} → nelze dopočíst O2.5")
            continue
        if o25_est <= 1.5:
            print(f"  ⚠ live2 skip {q['Match'][:50]}: O2.5={o25_est:.2f} ≤ 1.5 → nízký kurz")
            continue
        live2_out.append({
            "League": q["League"],
            "Match": q["Match"],
            "Tip": "Over 2.5",
            "Odds": f"{o25_est:.2f}",
            "Date": q["kickoff"],
        })
    with open(OUTPUT_LIVE2, "w", encoding="utf-8") as f:
        json.dump(live2_out, f, indent=2, ensure_ascii=False)
    print(f"  Live2 (variant A, Over 2.5): {len(live2_out)} match(es) \u2192 {OUTPUT_LIVE2}")

    # 6b. Best per league – keep only the top match from each league (for fotbals.json)
    TOURNAMENT_KEYWORDS = (
        "world cup", "euro ", "european", "copa america", "africa cup",
        "asian cup", "nations league", "champions league", "europa league",
        "conference league", "libertadores", "sudamericana", "concacaf",
        "afc cup", "afc champions", "olympic",
    )

    def normalize_league(name):
        low = name.lower()
        if any(kw in low for kw in TOURNAMENT_KEYWORDS):
            return name
        return re.sub(
            r'\s*[-–]\s*('
            r'Gir(?:one|\.)\s*\w+'
            r'|Gr(?:oup|p\.?)\s*\w+'
            r'|CFL\s*\w+'
            r'|Zone\s*\w+'
            r'|Conference\s*\w+'
            r'|Division\s*\w+'
            r'|North(?:ern)?|South(?:ern)?'
            r'|East(?:ern)?|West(?:ern)?'
            r'|[A-I]'
            r')\s*$',
            '', name, flags=re.IGNORECASE
        ).strip()

    before = len(results)
    best_per_league = {}
    for r in results:
        lg = normalize_league(r["League"])
        if lg not in best_per_league or r["_score"] > best_per_league[lg]["_score"]:
            best_per_league[lg] = r
    deduped = list(best_per_league.values())
    if before > len(deduped):
        print(f"  Dedup: {before} → {len(deduped)} (best per league, normalized)")

    # 7. Sort by kickoff time and write fotbals.json (bez interních polí)
    #    Filtr: zápasy s Over 1.5 < 1.30 přeskoč (test – hledáme value zápasy s vyššími kurzy).
    deduped.sort(key=lambda r: r["Date"])
    MIN_O15_FOTBALS = 1.25
    fotbals_filtered = []
    for r in deduped:
        o15 = r.get("_o15")
        if o15 is None or o15 < MIN_O15_FOTBALS:
            print(f"  ⚠ fotbals skip {r['Match'][:50]}: O1.5={o15} < {MIN_O15_FOTBALS}")
            continue
        fotbals_filtered.append(r)
    fotbals_out = [{k: v for k, v in r.items() if not k.startswith("_")} for r in fotbals_filtered]
    with open(OUTPUT, "w", encoding="utf-8") as f:
        json.dump(fotbals_out, f, indent=2, ensure_ascii=False)

    # 8. Write tips.json – max MAX_TIPS tipů, primárně z varianty A.
    #    Logika:
    #      a) Vyber zápasy s _variant == "A" (nejsilnější profil – oba útočí + obrany inkasují)
    #      b) Pro každý dopočti odhadovaný kurz Over 2.5 z Over 1.5 přes Poisson
    #         (nemusíme ho hledat v API – inverzí z o15)
    #      c) Pokud A pool < MAX_TIPS, doplň náhodným zápasem z deduped (i jiné varianty)
    #         – opět tipnut Over 2.5 (dopočet z o15)
    tips = []
    selected_keys = set()
    MIN_TIPS_O25 = 1.6  # zápas se do tips.json zapíše jen když dopočtený O2.5 ≥ 1.6

    a_pool = [r for r in deduped if r.get("_variant") == "A"]
    random.shuffle(a_pool)
    for r in a_pool[:MAX_TIPS]:
        o25_est = estimate_o25_from_o15(r.get("_o15"))
        if o25_est is None:
            continue
        if o25_est < MIN_TIPS_O25:
            print(f"  ⚠ tips skip {r['Match'][:50]}: O2.5={o25_est:.2f} < {MIN_TIPS_O25}")
            continue
        tips.append({
            "League": r["League"],
            "Match": r["Match"],
            "Tip": "Over 2.5",
            "Odds": f"{o25_est:.2f}",
            "Date": r["Date"],
        })
        selected_keys.add((r["Match"], r["Date"]))

    # 8b. Doplnit chybějící náhodnými zápasy z ostatních (B/C/?) – opět Over 2.5
    if len(tips) < MAX_TIPS:
        used_leagues = {t["League"] for t in tips}
        filler_pool = [
            r for r in deduped
            if (r["Match"], r["Date"]) not in selected_keys
               and r["League"] not in used_leagues
               and r.get("_o15") is not None
        ]
        random.shuffle(filler_pool)
        need = MAX_TIPS - len(tips)
        for r in filler_pool:
            if need <= 0:
                break
            o25_est = estimate_o25_from_o15(r.get("_o15"))
            if o25_est is None:
                continue
            if o25_est < MIN_TIPS_O25:
                print(f"  ⚠ tips skip {r['Match'][:50]}: O2.5={o25_est:.2f} < {MIN_TIPS_O25}")
                continue
            tips.append({
                "League": r["League"],
                "Match": r["Match"],
                "Tip": "Over 2.5",
                "Odds": f"{o25_est:.2f}",
                "Date": r["Date"],
            })
            need -= 1

    # 8c. FALLBACK: pokud po 8a + 8b stále chybí tipy (typicky když dnes
    #     žádné A varianty neprošly a `deduped` je prázdný/malý), doplň
    #     úplně random Over 2.5 z TOP first-tier lig s reálným kurzem
    #     v rozmezí TIPS_FB_MIN_ODDS..TIPS_FB_MAX_ODDS.
    if len(tips) < MAX_TIPS:
        need = MAX_TIPS - len(tips)
        exclude_keys = set(selected_keys)
        for t in tips:
            exclude_keys.add((t["Match"], t["Date"]))
        exclude_leagues = {t["League"] for t in tips}
        random_picks = pick_random_top_league_tips(
            all_fixtures, exclude_keys, exclude_leagues, need)
        for p in random_picks:
            tips.append(p)
            selected_keys.add((p["Match"], p["Date"]))

    if tips:
        print(f"  Tips: {len(tips)} match(es) → {OUTPUT_TIPS}")
    else:
        tips = [{"League": "-", "Match": "No tips available today.", "Tip": "-", "Odds": "-", "Date": now.isoformat()}]
        print(f"  Tips: no candidates at all → placeholder → {OUTPUT_TIPS}")

    with open(OUTPUT_TIPS, "w", encoding="utf-8") as f:
        json.dump(tips, f, indent=2, ensure_ascii=False)

    print(f"\n{'='*50}")
    print(f"  Results: {len(deduped)} match(es) → {OUTPUT}")
    print(f"  Live:    {len(live_out)} match(es) → {OUTPUT_LIVE}")
    print(f"  Live2:   {len(live2_out)} match(es) → {OUTPUT_LIVE2}")
    print(f"  Tips:    {len(tips)} match(es) → {OUTPUT_TIPS}")
    print(f"  API requests: {request_count} / 7500 ({request_count * 100 // 7500}%)")


if __name__ == "__main__":
    main()
