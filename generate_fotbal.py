#!/usr/bin/env python3
"""
SureBets Football Bot – generates fotbals.json
Runs daily at 7:00 UTC via GitHub Actions.

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
OUTPUT_TIPS = "tips.json"
MAX_TIPS = 2

MIN_ODDS = 1.60          # Over 2.5 range (used only for tips.json filler)
MAX_ODDS = 1.80
MIN_ODDS_15_OUT = 1.12   # NEW: value-gate na výstupu (Over 1.5 musí být ≥ 1.12)
MIN_GAMES = 6            # zvýšeno z 5 → spolehlivější vzorek

# === Stats-first kritéria pro "kandidáta na Over 3.5" ===
# Cíl: expected total ≥ 3.0 gólů (P(Over 3.5) ~ 35 %, P(Over 1.5) ~ 90 %+)
MIN_TOTAL_AVG = 3.00     # tvrdý gate: (h_for+a_for+h_agn+a_agn)/2 ≥ 3.0
MIN_DEFENSE_LEAK = 1.00  # aspoň jeden tým inkasuje ≥ 1.0 g/z (musí být odkud góly brát)
MIN_READY_35 = 0.85      # kompozitní index "Over 3.5 readiness"

# === NEW: Poissonova pravděpodobnost Over 3.5 (per varianta) ===
# expected total (λ) → P(Over 3.5) přes nezávislé Poisson rozdělení.
# Diferenciace podle profilu zápasu:
#   B = oba útočí (strukturálně nejlepší pro Over 3.5) → volnější
#   A = jeden silný útok + děravé obrany → default
#   C = "open shootout" symetrie → přísnější (často klouže k 0:0)
MIN_P35_BY_VARIANT = {
    "A": 0.45,
    "B": 0.43,
    "C": 0.50,
}

# League-relative ratios (mírně zostřeno proti původnímu Over 2.5 botu)
BOTH_FLOOR_R = 0.85      # oba alespoň 85% baseline
STRONG_MIN_R = 1.15      # "výrazný" tým 115%+ baseline (z 1.10)
CONTRAST_MAX_R = 0.95    # protějšek pod 95% baseline
MIN_BASELINE = 1.40      # zvýšeno z 1.25 → expected ~3.0+ gólů celkem
MIN_ATTACK = 0.95        # zvýšeno z 0.80 → oba reálně střílí
MIN_2H_BASELINE = 0.55   # zvýšeno z 0.45 → 2H aktivita

EXCLUDED_COUNTRIES = {"russia", "belarus"}

# League blacklist – exact league names from API
EXCLUDED_LEAGUES = {
    "2. liga",           # 2. Slovenská liga
}

# Country-specific whitelist – if a country is listed here,
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

    # Home team → home split, Away team → away split
    h_for = _sf(home.get("league", {}).get("goals", {}).get("for", {}).get("average", {}).get("home"))
    a_for = _sf(away.get("league", {}).get("goals", {}).get("for", {}).get("average", {}).get("away"))
    h_agn = _sf(home.get("league", {}).get("goals", {}).get("against", {}).get("average", {}).get("home"))
    a_agn = _sf(away.get("league", {}).get("goals", {}).get("against", {}).get("average", {}).get("away"))

    if h_for == 0 and a_for == 0:
        return False, "", 0.0

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
    # Variant C (NEW): "open shootout" – oba dost útočí I dost inkasují
    # → nepotřebuje kontrast, stačí že obě strany jsou nad floor v obou metrikách
    variant_c = (
        h_for >= both_floor and a_for >= both_floor
        and h_agn >= both_floor and a_agn >= both_floor
    )

    if not (variant_a or variant_b or variant_c):
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

    tag = "A" if variant_a else ("B" if variant_b else "C")

    # === BRÁNA 5: Poissonova P(Over 3.5) – diferenciovaná podle varianty ===
    p35 = poisson_p_over(total_avg, 3.5)
    min_p35 = MIN_P35_BY_VARIANT.get(tag, 0.45)
    if p35 < min_p35:
        return False, (f"[{tag}] p35 too low: {p35*100:.1f}% < {min_p35*100:.0f}% "
                       f"(λ={total_avg:.2f})"), 0.0

    detail = (f"[{tag}] total={total_avg:.2f} p35={p35*100:.0f}% ready={ready_35:.2f} "
              f"| scored {h_for:.2f}/{a_for:.2f}, conceded {h_agn:.2f}/{a_agn:.2f} "
              f"| 2H base={base_2h:.2f} (base={baseline:.2f})")
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
    print(f"Stats gate: total≥{MIN_TOTAL_AVG}, baseline≥{MIN_BASELINE}, ready_35≥{MIN_READY_35}")
    print(f"P(O3.5) gate per variant: A≥{MIN_P35_BY_VARIANT['A']*100:.0f}%, "
          f"B≥{MIN_P35_BY_VARIANT['B']*100:.0f}%, C≥{MIN_P35_BY_VARIANT['C']*100:.0f}%")
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
        if country in EXCLUDED_COUNTRIES:
            continue
        league = fix.get("league", "")
        if league in EXCLUDED_LEAGUES:
            continue
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
                qualified.append({
                    "fixture_id": fid,
                    "League": fix["league"],
                    "Match": match_str,
                    "kickoff": fix["kickoff"],
                    "league_id": fix["league_id"],
                    "season": fix["season"],
                    "_score": score,
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
        if o15 < MIN_ODDS_15_OUT:
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
            "_o25": f"{o25:.2f}" if o25 else None,
        })

    # 6a. Write live.json – ALL value-gate-passing matches (no dedup)
    live_results = sorted(results, key=lambda r: r["Date"])
    live_out = [{k: v for k, v in r.items() if not k.startswith("_")} for r in live_results]
    with open(OUTPUT_LIVE, "w", encoding="utf-8") as f:
        json.dump(live_out, f, indent=2, ensure_ascii=False)
    print(f"\n  Live: {len(live_out)} match(es) \u2192 {OUTPUT_LIVE}")

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
    deduped.sort(key=lambda r: r["Date"])
    fotbals_out = [{k: v for k, v in r.items() if not k.startswith("_")} for r in deduped]
    with open(OUTPUT, "w", encoding="utf-8") as f:
        json.dump(fotbals_out, f, indent=2, ensure_ascii=False)

    # 8. Write tips.json – 2 tipy preferovaně z kvalifikovaných zápasů s Over 2.5 v rozsahu
    #    Priorita:
    #      a) kvalifikovaný zápas (best-per-league) s Over 2.5 v pásmu MIN_ODDS–MAX_ODDS
    #      b) jakýkoli kvalifikovaný zápas → tipnout Over 1.5 (bezpečnější)
    tips = []
    selected_keys = set()

    # 8a. Pokus: Over 2.5 z kvalifikovaných v pásmu 1.60–1.80
    o25_pool = [
        r for r in deduped
        if r.get("_o25") and MIN_ODDS <= float(r["_o25"]) <= MAX_ODDS
    ]
    if o25_pool:
        picks = random.sample(o25_pool, min(MAX_TIPS, len(o25_pool)))
        for p in picks:
            tips.append({
                "League": p["League"],
                "Match": p["Match"],
                "Tip": "Over 2.5",
                "Odds": p["_o25"],
                "Date": p["Date"],
            })
            selected_keys.add((p["Match"], p["Date"]))

    # 8b. Doplnit Over 1.5 z kvalifikovaných (různé ligy)
    if len(tips) < MAX_TIPS:
        used_leagues = {t["League"] for t in tips}
        filler_pool = [
            r for r in deduped
            if (r["Match"], r["Date"]) not in selected_keys
               and r["League"] not in used_leagues
        ]
        # seřadit podle score sestupně
        filler_pool.sort(key=lambda r: r["_score"], reverse=True)
        need = MAX_TIPS - len(tips)
        for r in filler_pool[:need]:
            tips.append({
                "League": r["League"],
                "Match": r["Match"],
                "Tip": "Over 1.5",
                "Odds": r["Odds"],
                "Date": r["Date"],
            })

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
    print(f"  Tips:    {len(tips)} match(es) → {OUTPUT_TIPS}")
    print(f"  API requests: {request_count} / 7500 ({request_count * 100 // 7500}%)")


if __name__ == "__main__":
    main()
