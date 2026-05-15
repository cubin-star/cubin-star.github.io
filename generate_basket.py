#!/usr/bin/env python3
"""
SureBets Basketball Bot – generates baskets.json
Runs daily at 6:00 UTC via GitHub Actions.

Basketball has no fixed Over line – each game has its own.
1. Find "selection line" (Over where odds ≈ 1.90) – aggressive, high line
2. Derive dynamic Variant A/B thresholds from that line
3. If qualified, output the "safe line" (Over where odds ≈ 1.30) – ~20pt cushion

Thresholds are proportional ratios of half_line (= selection_line / 2),
compressed for basketball's tighter scoring distribution.

SETUP:
  1. Copy this file to the root of cubin-star/cubin-star.github.io
  2. Copy tools/github-actions-surebets-basket.yml to .github/workflows/
  3. In repo Settings → Secrets → Actions, add:
     API_BASKETBALL_KEY = your API key from api-sports.io
"""

import json
import math
import os
import random
import time
import urllib.request
from datetime import datetime, timedelta, timezone

# ===== CONFIG =====
API_KEY = os.environ.get("API_BASKETBALL_KEY", "")
BASE_URL = "https://v1.basketball.api-sports.io"
DELAY = 0.3
OUTPUT = "baskets.json"
OUTPUT_LIVE = "liveb.json"

EXCLUDED_COUNTRIES = {"russia", "belarus"}
MIN_GAMES = 6

# Ženské soutěže a mládež – blokovat napříč všemi zeměmi (klíčová slova v názvu ligy)
EXCLUDED_LEAGUE_KEYWORDS = (
    "women", "woman", "ladies", "féminine", "feminine",
    "frauen", "femenino", "femenina", "femminile", "wnba",
    "kvinnor", "naisten", "kobiet", "ženy", "zeny", "ženská", "zenska",
    "u20", "u-20", "u19", "u-19", "u18", "u-18", "u17", "u-17", "u16", "u-16",
    "junior", "juniors", "juvenil", "youth", "cadet",
)

# === Edge-first konfig (NEW v3) ===
# Min. edge = (predicted_total − bookmaker_main_line) musí být >= MIN_EDGE,
# aby bot zápas považoval za "mispriced". Per region (NBA / EU / ostatní).
MIN_EDGE_BY_REGION = {
    "NBA":   8.0,   # NBA má efektivní trh, kvalitní edge je 8+ bodů
    "EU":    6.0,   # Euroliga, EuroCup, národní špičky – mírnější
    "WORLD": 5.0,   # zbytek (FIBA, mezinárodní), nejméně přísné
}

# Value-gate na výstupní linii: nejnižší dostupná Over linie s průměrným kurzem
# napříč bookmakery >= MIN_ODDS_OUT bude vystupovat.
# 1.30 bylo příliš přísné – většina value Over linií leží 1.22–1.28 a propadala.
# Vracíme na 1.25 jako kompromis (cca 80 % implied prob., dost cushion).
MIN_ODDS_OUT = 1.25

# Klíčová slova pro detekci NBA (kvůli regionu prahů a TFM logice)
NBA_KEYWORDS = ("nba",)

# Target odds for line selection
SELECTION_ODDS = 1.90   # find the Over line near this odds (aggressive – higher line)
OUTPUT_ODDS = 1.22       # find the safer Over line near this odds (safe – bigger cushion)
ODDS_TOLERANCE = 0.30    # max deviation from target

# Criteria – venue-specific matchup vs. bookmaker line
BOTH_FLOOR_R = 0.93       # uvolněno z 0.97 (evro ligy mají 1 tým mírně pod průměrem)
MIN_HALF_LINE = 70        # minimální half_line – sníženo z 80 (otevírá EuroCup, NCAA, atd.)
EXPECTED_MIN_R = 1.02     # uvolněno z 1.05 (NBA top zápasy padaly o 2 body)
OFFENSE_VS_LEAGUE_R = 0.90  # uvolněno z 0.95 (jeden tým může být pod průměrem)

# Enhanced criteria – recent form, H2H, consistency, rest
RECENT_N = 10               # rolling window: last N finished games
RECENT_FLOOR_R = 0.97       # rolling avg total of last N games >= 97% of selection_line
MIN_OVER_HIT_RATE = 0.60    # >= 60% of last N games had total >= safe_line (each team)
MAX_TOTAL_SD = 22.0         # max std dev of game totals – uvolněno z 20 (evro ligy mají vyšší)
MIN_TOTAL_SD = 11.0         # min SD obou týmů – pod 11 = extrémně defenzivní/pomalý
                            # tým, který "drží" zápas pod linií. Sníženo z 13 (vyřazovalo
                            # disciplinované týmy s normální variabilitou ~11–12).
H2H_MIN_GAMES = 2           # min H2H finished games to apply H2H filter
H2H_OVER_R = 0.92           # H2H avg total >= 92% of selection_line
MIN_REST_HOURS = 0           # 0 = vypnuto (back-to-back filtr deaktivován)

# Defense leakage – both teams must concede enough (porous defense)
BOTH_CONCEDE_FLOOR_R = 0.90  # uvolněno z 0.95 (lepší obrana neznamená zákaz)
MIN_LINE_GAP = 10.0          # output line must be at least 10 pts below selection line

# 2nd half filter (like football's 2nd-half filter)
MIN_2H_RATIO = 0.47           # 2H bodů musí být ≥ 47% celkových bodů
MIN_2H_BASELINE = 38.0        # minimum 2H baseline (avg per-team scored+conceded in Q3+Q4)

# Pace proxy z Q1 – pomalý start = riziko Under
MIN_Q1_RATIO = 0.22           # avg Q1 score musí být >= 22% selection_line / 4 baseline
                              # pomalé starty (< 22 %) = pravděpodobně low-pace zápas

# Blowout / garbage time filter – velký rozdíl skóre tlačí Q4 dolů
BLOWOUT_MARGIN = 25           # rozdíl > 25 bodů = blowout
MAX_BLOWOUT_RATE = 0.40       # max 40 % posledních H2H smí být blowouty

# Cross-market konfirmace: oba team totaly Over levné = bookmaker vidí oba aktivní
TT_BONUS_ODDS = 1.75          # zpřísněno z 1.95 – kurz blízko 2.00 není konfirmace
TT_BONUS_MULT = 1.15          # multiplikátor skóre při konfirmaci

# Quality score system – pouští jen TOP zápasy splňující víc než jen základ
# Každé splněné kritérium = body. Zápas musí mít >= MIN_QUALITY_SCORE.
MIN_QUALITY_SCORE = 4    # min počet bodů z kvalitního skóre (0-10)
MAX_TIPS_PER_DAY = 3     # globální limit – nejlepších N podle skóre (kvalita>kvantita)

# H2H jako TVRDÝ filtr (ne jen bonus) – při dostatečném vzorku H2H
# Pokud H2H avg < HARD_FAIL_R × selection_line a n >= MIN_N → automatický fail
# Důvod: bookmaker už ví o specifickém matchupu víc než naše recent form
# Sníženo z 0.96 → 0.92: bookmaker linie je často 4–5 % nad H2H průměrem (běžný
# market noise) a 0.96 vyřazoval i jasné edge zápasy v NBA.
H2H_HARD_FAIL_R = 0.92       # H2H avg pod 92 % selection_line = past
H2H_HARD_FAIL_MIN_N = 10     # min počet H2H pro hard fail (statistická významnost)

# H2H minimální vzorek pro spolehlivý průměr – při n < tomto se H2H avg
# musí rovnat ALESPOŇ selection_line (přísnější), jinak je vzorek statisticky
# bezcenný. Leuven-Zwolle měli H2H=157 z n=5 (bookmaker 159 - prošlo H2H_OVER_R=0.92,
# ale realita 130). Beijing-Guangdong měli n=52 → spolehlivé.
H2H_RELIABLE_MIN_N = 10      # nad tímto vzorkem platí standardní H2H_OVER_R práh
# Při malém vzorku byl práh 1.00 příliš tvrdý – vyřazoval skoro všechny EU ligy,
# kde se týmy potkávají 2× za sezónu a n bývá 4–6. 0.95 je rozumný kompromis.
H2H_SMALL_SAMPLE_R = 0.95    # při n < H2H_RELIABLE_MIN_N: H2H avg >= 95 % selection_line

# Playoff / play-in detekce – taktičtější zápasy mívají nižší totaly
# Pokud název ligy obsahuje některý keyword, přidej rezervu na expected_min_r
PLAYOFF_KEYWORDS = ("playoff", "play-in", "play in", "semifinal", "semi-final",
                    "final", "quarter", "elimination", "knockout")
PLAYOFF_EXPECTED_BONUS = 0.05  # +5 % rezerva na expected vs. line v playoff

# Recent form rozdíl mezi týmy – velký rozdíl = různé tempo, neslučitelné styly
# (jeden tým hraje 180, druhý 160 → průměr 170 je často chimér)
# 12 bylo moc přísné (NBA běžně útočný vs. defenzivní = 13–16 gap).
MAX_FORM_GAP = 18.0          # |h_avg - a_avg| > 18 → fail (různé tempo)

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


def fetch_games(date_str):
    print(f"  Games {date_str}...", end="")
    data = api_get("games", {"date": date_str, "timezone": "UTC"})
    games = {}
    for g in data.get("response", []):
        gid = g.get("id")
        if not gid:
            continue
        status = g.get("status", {}).get("short", "")
        if status not in ("NS", "TBD", ""):
            continue
        games[gid] = {
            "home": g.get("teams", {}).get("home", {}).get("name", "?"),
            "away": g.get("teams", {}).get("away", {}).get("name", "?"),
            "home_id": g.get("teams", {}).get("home", {}).get("id", 0),
            "away_id": g.get("teams", {}).get("away", {}).get("id", 0),
            "league": g.get("league", {}).get("name", "?"),
            "league_id": g.get("league", {}).get("id", 0),
            "season": g.get("league", {}).get("season", ""),
            "country": g.get("country", {}).get("name", "?"),
            "timestamp": g.get("timestamp", 0),
        }
    print(f" {len(games)} upcoming")
    return games


def fetch_odds(game_id):
    time.sleep(DELAY)
    data = api_get("odds", {"game": str(game_id)})
    return data.get("response", [])


def fetch_team_stats(league_id, season, team_id):
    time.sleep(DELAY)
    data = api_get("statistics", {
        "league": str(league_id),
        "season": str(season),
        "team": str(team_id),
    })
    return data.get("response")


def fetch_team_games(team_id, league_id, season):
    """Fetch all finished games for a team in given league+season."""
    time.sleep(DELAY)
    data = api_get("games", {
        "team": str(team_id),
        "league": str(league_id),
        "season": str(season),
    })
    finished = []
    for g in data.get("response", []):
        status = g.get("status", {}).get("short", "")
        if status not in ("FT", "AOT"):
            continue
        scores = g.get("scores", {})
        h_total = scores.get("home", {}).get("total")
        a_total = scores.get("away", {}).get("total")
        if h_total is None or a_total is None:
            continue
        try:
            entry = {
                "timestamp": g.get("timestamp", 0),
                "home_id": g.get("teams", {}).get("home", {}).get("id", 0),
                "away_id": g.get("teams", {}).get("away", {}).get("id", 0),
                "home_total": int(h_total),
                "away_total": int(a_total),
                "total": int(h_total) + int(a_total),
                "margin": abs(int(h_total) - int(a_total)),
            }
            # Quarter data for 2nd half (Q3+Q4) analysis
            h_sc = scores.get("home", {})
            a_sc = scores.get("away", {})
            h_q3 = h_sc.get("quarter_3")
            h_q4 = h_sc.get("quarter_4")
            a_q3 = a_sc.get("quarter_3")
            a_q4 = a_sc.get("quarter_4")
            if all(v is not None for v in (h_q3, h_q4, a_q3, a_q4)):
                try:
                    entry["h_sh"] = int(h_q3) + int(h_q4)
                    entry["a_sh"] = int(a_q3) + int(a_q4)
                    entry["total_sh"] = entry["h_sh"] + entry["a_sh"]
                    # Regulation total (4Q, no OT) for fair 2H ratio
                    h_q1 = int(h_sc.get("quarter_1", 0) or 0)
                    h_q2 = int(h_sc.get("quarter_2", 0) or 0)
                    a_q1 = int(a_sc.get("quarter_1", 0) or 0)
                    a_q2 = int(a_sc.get("quarter_2", 0) or 0)
                    entry["reg_total"] = h_q1 + h_q2 + int(h_q3) + int(h_q4) + a_q1 + a_q2 + int(a_q3) + int(a_q4)
                    # Q1 total (pace proxy)
                    entry["q1_total"] = h_q1 + a_q1
                except (ValueError, TypeError):
                    pass
            finished.append(entry)
        except (ValueError, TypeError):
            pass
    finished.sort(key=lambda x: x["timestamp"], reverse=True)
    return finished


def fetch_h2h(home_id, away_id):
    """Fetch head-to-head history between two teams."""
    time.sleep(DELAY)
    data = api_get("games", {"h2h": f"{home_id}-{away_id}"})
    results = []
    for g in data.get("response", []):
        status = g.get("status", {}).get("short", "")
        if status not in ("FT", "AOT"):
            continue
        scores = g.get("scores", {})
        h_total = scores.get("home", {}).get("total")
        a_total = scores.get("away", {}).get("total")
        if h_total is None or a_total is None:
            continue
        try:
            results.append({
                "timestamp": g.get("timestamp", 0),
                "total": int(h_total) + int(a_total),
                "margin": abs(int(h_total) - int(a_total)),
            })
        except (ValueError, TypeError):
            pass
    results.sort(key=lambda x: x["timestamp"], reverse=True)
    return results


# ===== ODDS PARSING =====

def find_main_line_and_all_overs(odds_data):
    """Edge-first odds discovery.

    Vrací (main_line_info, all_overs, home_tt_odd, away_tt_odd):
      - main_line_info: dict {line, avg_odd, n} – hlavní bookmaker linie
        (Over s kurzem nejblíž 1.90 napříč BK, agregováno přes BK).
      - all_overs: list dictů [{line, avg_odd, n, label, odd_str}] seřazený dle 'line' vzestupně.
        Každá linie obsahuje průměrný kurz napříč všemi bookmakery (n = počet BK).
      - home_tt_odd, away_tt_odd: nejnižší Over kurz pro home/away team total.

    Pokud nejsou dostupné Over linie u bet id 4, vrací (None, [], None, None).
    """
    by_line = {}  # line -> {sum, n, label}
    home_tt = None
    away_tt = None

    for resp in odds_data:
        for bk in resp.get("bookmakers", []):
            for bet in bk.get("bets", []):
                bet_id = bet.get("id")
                bet_name = (bet.get("name") or "").lower()

                # --- Cross-market: Home/Away Team Total ---
                if "team total" in bet_name or (
                    "total" in bet_name and ("home" in bet_name or "away" in bet_name)
                ):
                    is_home = "home" in bet_name
                    is_away = "away" in bet_name
                    if is_home or is_away:
                        for val in bet.get("values", []):
                            v = str(val.get("value", ""))
                            if not v.lower().startswith("over"):
                                continue
                            try:
                                odd = float(val.get("odd", "0"))
                                if is_home and (home_tt is None or odd < home_tt):
                                    home_tt = odd
                                if is_away and (away_tt is None or odd < away_tt):
                                    away_tt = odd
                            except (ValueError, TypeError):
                                pass

                # --- Main: Over/Under full game (bet id 4) ---
                if bet_id == 4:
                    for val in bet.get("values", []):
                        v = str(val.get("value", ""))
                        if not v.lower().startswith("over"):
                            continue
                        try:
                            line = float(v.split()[-1])
                            odd = float(val.get("odd", "0"))
                            if odd <= 1.0:
                                continue
                        except (ValueError, IndexError, TypeError):
                            continue
                        rec = by_line.setdefault(line, {
                            "sum": 0.0, "n": 0, "label": v,
                            "odd_str_first": str(val.get("odd")),
                        })
                        rec["sum"] += odd
                        rec["n"] += 1

    if not by_line:
        return None, [], home_tt, away_tt

    all_overs = []
    for line, rec in by_line.items():
        avg_odd = rec["sum"] / rec["n"]
        all_overs.append({
            "line": line,
            "avg_odd": avg_odd,
            "n": rec["n"],
            "label": rec["label"],
            "odd_str": f"{avg_odd:.2f}",
        })
    all_overs.sort(key=lambda x: x["line"])

    # Hlavní linie = Over kurz nejblíž 1.90 (typický spravedlivý market)
    main = min(all_overs, key=lambda x: abs(x["avg_odd"] - 1.90))
    return main, all_overs, home_tt, away_tt


def _is_half_line(line):
    """True pokud linie končí na .5 (např. 197.5). Toleruje float chyby."""
    frac = abs(line - math.floor(line) - 0.5)
    return frac < 1e-6


def pick_lowest_value_over(all_overs, min_odds, max_line=None):
    """Vrátí nejnižší Over linii, jejíž průměrný kurz >= min_odds.
    Pokud max_line je zadáno, omezí výběr na linie <= max_line.
    Vrací pouze linie končící na .5 (basketbalový tip nesmí být celé číslo –
    vyhneme se push, např. 197.5 ano, 197 ne).
    Vrací None pokud žádná nevyhovuje."""
    candidates = [
        o for o in all_overs
        if o["avg_odd"] >= min_odds
        and (max_line is None or o["line"] <= max_line)
        and _is_half_line(o["line"])
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda x: x["line"])


def find_over_lines(odds_data):
    """Find selection line (odds ≈ 1.80) and output line (odds ≈ 1.45).
    Returns (selection, output, home_tt_over, away_tt_over) where the team-total
    Over odds (closest to selection_line/2) are returned for cross-market
    confirmation. Team total values are None if not found.
    Returns (None, None, None, None) if main lines not found."""
    sel_result = None
    out_result = None
    home_tt = None
    away_tt = None

    for resp in odds_data:
        for bk in resp.get("bookmakers", []):
            for bet in bk.get("bets", []):
                bet_id = bet.get("id")
                bet_name = bet.get("name", "").lower()

                # --- Cross-market: Home/Away Team Total ---
                # Hledáme bet, kde název obsahuje "team total" + "home"/"away"
                if "team total" in bet_name or ("total" in bet_name and ("home" in bet_name or "away" in bet_name)):
                    is_home = "home" in bet_name
                    is_away = "away" in bet_name
                    if is_home or is_away:
                        # Vezmeme nejnižší Over kurz (nejvíc preferovaný bookmakerem)
                        for val in bet.get("values", []):
                            v = str(val.get("value", ""))
                            if not v.lower().startswith("over"):
                                continue
                            try:
                                odd = float(val.get("odd", "0"))
                                if is_home and (home_tt is None or odd < home_tt):
                                    home_tt = odd
                                if is_away and (away_tt is None or odd < away_tt):
                                    away_tt = odd
                            except (ValueError, TypeError):
                                pass

                # --- Main: Over/Under full game (bet id 4) ---
                if (sel_result is None or out_result is None) and bet_id == 4:
                    overs = []
                    for val in bet.get("values", []):
                        v = str(val.get("value", ""))
                        if not v.lower().startswith("over"):
                            continue
                        try:
                            line = float(v.split()[-1])
                            odd = float(val.get("odd", "0"))
                            overs.append({
                                "line": line,
                                "odd": odd,
                                "label": v,
                                "odd_str": str(val.get("odd")),
                            })
                        except (ValueError, IndexError):
                            pass

                    if len(overs) >= 2:
                        sel = min(overs, key=lambda x: abs(x["odd"] - SELECTION_ODDS))
                        out = min(overs, key=lambda x: abs(x["odd"] - OUTPUT_ODDS))

                        if abs(sel["odd"] - SELECTION_ODDS) <= ODDS_TOLERANCE and \
                           abs(out["odd"] - OUTPUT_ODDS) <= ODDS_TOLERANCE and \
                           out["line"] < sel["line"] and \
                           sel["line"] - out["line"] >= MIN_LINE_GAP:
                            sel_result = sel
                            out_result = out

    return sel_result, out_result, home_tt, away_tt


# ===== CRITERIA =====

def _sf(val, default=0.0):
    try:
        return float(val) if val is not None else default
    except (ValueError, TypeError):
        return default


def _is_excluded_league(league_name, country_name=""):
    """True pokud liga je ženská / mládežnická / RU / BY → bot ji ani neprohledává."""
    name = (league_name or "").lower()
    country = (country_name or "").lower()
    if country in EXCLUDED_COUNTRIES:
        return True
    for kw in EXCLUDED_LEAGUE_KEYWORDS:
        if kw in name:
            return True
    return False


def _classify_region(league_name, country_name=""):
    """Vrátí region klíč pro MIN_EDGE_BY_REGION: 'NBA' / 'EU' / 'WORLD'."""
    name = (league_name or "").lower()
    country = (country_name or "").lower()
    for kw in NBA_KEYWORDS:
        if kw in name:
            return "NBA"
    eu_countries = {
        "spain", "italy", "germany", "france", "greece", "turkey", "serbia",
        "lithuania", "latvia", "estonia", "poland", "czech-republic", "czech republic",
        "slovenia", "croatia", "israel", "belgium", "netherlands", "portugal",
        "austria", "switzerland", "hungary", "romania", "bulgaria", "ukraine",
        "finland", "sweden", "norway", "denmark", "iceland",
        "united kingdom", "great britain", "england", "scotland",
    }
    eu_keywords = ("euroleague", "eurocup", "euro cup", "champions league", "fiba europe")
    if country in eu_countries:
        return "EU"
    for kw in eu_keywords:
        if kw in name:
            return "EU"
    return "WORLD"


def predict_total(home_stats, away_stats, home_form=None, away_form=None,
                  is_playoff=False):
    """Vrátí (predicted_total, components_detail) nebo (None, reason).

    Predikce je váženým průměrem dvou nezávislých signálů:
      - venue_expected: home offense AT HOME + away defense AWAY +
                        away offense AWAY + home defense AT HOME (děleno 2)
      - form_expected: průměr posledních N total skóre obou týmů (váha 50/50)

    Váhy: venue 0.6, form 0.4. Pokud form chybí, použije se jen venue.
    Playoff zápasy se srážejí o 3 body (taktika, méně risku).
    """
    if not home_stats or not away_stats:
        return None, "no stats"

    h_for = _sf(home_stats.get("points", {}).get("for", {}).get("average", {}).get("all"))
    a_for = _sf(away_stats.get("points", {}).get("for", {}).get("average", {}).get("all"))
    h_agn = _sf(home_stats.get("points", {}).get("against", {}).get("average", {}).get("all"))
    a_agn = _sf(away_stats.get("points", {}).get("against", {}).get("average", {}).get("all"))

    if h_for == 0 or a_for == 0:
        return None, "no points stats"

    h_for_h = _sf(home_stats.get("points", {}).get("for", {}).get("average", {}).get("home")) or h_for
    a_for_a = _sf(away_stats.get("points", {}).get("for", {}).get("average", {}).get("away")) or a_for
    h_agn_h = _sf(home_stats.get("points", {}).get("against", {}).get("average", {}).get("home")) or h_agn
    a_agn_a = _sf(away_stats.get("points", {}).get("against", {}).get("average", {}).get("away")) or a_agn

    venue_expected = (h_for_h + a_agn_a + a_for_a + h_agn_h) / 2

    form_expected = None
    if home_form and away_form:
        h_avg = home_form.get("avg_total")
        a_avg = away_form.get("avg_total")
        if h_avg and a_avg:
            form_expected = (h_avg + a_avg) / 2

    if form_expected is not None:
        predicted = 0.6 * venue_expected + 0.4 * form_expected
        detail = (f"pred={predicted:.1f} "
                  f"(venue={venue_expected:.1f}*0.6 + form={form_expected:.1f}*0.4)")
    else:
        predicted = venue_expected
        detail = f"pred={predicted:.1f} (venue only)"

    if is_playoff:
        predicted -= 3.0
        detail += " [PO -3]"

    return predicted, detail


def meets_criteria(home_stats, away_stats, selection_line, is_playoff=False,
                   predicted_total=None, min_edge=None):
    """Basketball criteria – edge-first (v3) s fallback na původní expected_r logiku.

    Pokud je předán `predicted_total` a `min_edge`:
      - kontroluje venue floor / defense floor (ne agresivně),
      - vyžaduje (predicted_total - selection_line) >= min_edge.

    Jinak (legacy) používá expected_r * selection_line.
    Vrací (ok, detail, score) – score se používá pro řazení.
    """
    if not home_stats or not away_stats:
        return False, "", 0.0

    h_played = int(_sf(home_stats.get("games", {}).get("played", {}).get("all", 0)))
    a_played = int(_sf(away_stats.get("games", {}).get("played", {}).get("all", 0)))
    if h_played < MIN_GAMES or a_played < MIN_GAMES:
        return False, f"few games: {h_played}/{a_played}", 0.0


    # Overall stats (league baseline)
    h_for = _sf(home_stats.get("points", {}).get("for", {}).get("average", {}).get("all"))
    a_for = _sf(away_stats.get("points", {}).get("for", {}).get("average", {}).get("all"))
    h_agn = _sf(home_stats.get("points", {}).get("against", {}).get("average", {}).get("all"))
    a_agn = _sf(away_stats.get("points", {}).get("against", {}).get("average", {}).get("all"))

    if h_for == 0 or a_for == 0:
        return False, "no stats", 0.0

    half = selection_line / 2
    if half < MIN_HALF_LINE:
        return False, f"half_line too low: {half:.0f} < {MIN_HALF_LINE}", 0.0

    # Venue-specific stats: home team AT HOME, away team AWAY
    # Fallback to overall if venue split unavailable (0 = no data)
    h_for_h = _sf(home_stats.get("points", {}).get("for", {}).get("average", {}).get("home")) or h_for
    a_for_a = _sf(away_stats.get("points", {}).get("for", {}).get("average", {}).get("away")) or a_for
    h_agn_h = _sf(home_stats.get("points", {}).get("against", {}).get("average", {}).get("home")) or h_agn
    a_agn_a = _sf(away_stats.get("points", {}).get("against", {}).get("average", {}).get("away")) or a_agn

    # League baseline from overall stats
    league_avg = (h_for + h_agn + a_for + a_agn) / 2
    league_avg_per_team = league_avg / 2

    # Matchup expected from venue splits
    # Home attack at home + away defense leakage away
    # + Away attack away + home defense leakage at home
    expected = (h_for_h + a_agn_a + a_for_a + h_agn_h) / 2

    # --- Check 1: Both teams must have active offense at their venue ---
    min_floor = half * BOTH_FLOOR_R
    if h_for_h < min_floor or a_for_a < min_floor:
        return False, (f"weak venue offense: {h_for_h:.0f}/{a_for_a:.0f} "
                       f"(min {min_floor:.0f})"), 0.0

    # --- Check 2: Both teams venue offense above league average ---
    offense_floor = league_avg_per_team * OFFENSE_VS_LEAGUE_R
    if h_for_h < offense_floor or a_for_a < offense_floor:
        return False, (f"venue offense below league avg: {h_for_h:.0f}/{a_for_a:.0f} "
                       f"(league avg/team={league_avg_per_team:.0f})"), 0.0

    # --- Check 2b: Both teams must have porous defense at their venue ---
    concede_floor = half * BOTH_CONCEDE_FLOOR_R
    if h_agn_h < concede_floor or a_agn_a < concede_floor:
        return False, (f"defense too tight: concede {h_agn_h:.0f}/{a_agn_a:.0f} "
                       f"(min {concede_floor:.0f})"), 0.0

    # --- Check 3: Edge-first nebo legacy expected_r ---
    if predicted_total is not None and min_edge is not None:
        edge = predicted_total - selection_line
        po_tag = " [PO]" if is_playoff else ""
        if edge < min_edge:
            return False, (f"venue {h_for_h:.0f}+{a_for_a:.0f} conc {h_agn_h:.0f}+{a_agn_a:.0f}{po_tag} "
                           f"(pred={predicted_total:.1f} − line={selection_line:.1f} "
                           f"= edge={edge:+.1f} < min_edge={min_edge:.1f}, league={league_avg:.0f})"), 0.0
        # score = velikost edge (pro ranking) – víc bodů edge = silnější tip
        score = edge
        detail = (f"venue {h_for_h:.0f}+{a_for_a:.0f} conc {h_agn_h:.0f}+{a_agn_a:.0f}{po_tag} "
                  f"EDGE={edge:+.1f} (pred={predicted_total:.1f} vs line={selection_line:.1f}, "
                  f"league={league_avg:.0f})")
        return True, detail, score

    # Legacy větev (bez edge)
    expected_r = EXPECTED_MIN_R + (PLAYOFF_EXPECTED_BONUS if is_playoff else 0.0)
    min_expected = selection_line * expected_r
    if expected < min_expected:
        po_tag = " [PO]" if is_playoff else ""
        return False, (f"venue {h_for_h:.0f}+{a_for_a:.0f} conc {h_agn_h:.0f}+{a_agn_a:.0f} "
                       f"(exp={expected:.0f} < line*{expected_r:.2f}={min_expected:.0f}{po_tag}, "
                       f"league={league_avg:.0f})"), 0.0

    score = expected / selection_line
    po_tag = " [PO]" if is_playoff else ""
    detail = (f"venue {h_for_h:.0f}+{a_for_a:.0f} conc {h_agn_h:.0f}+{a_agn_a:.0f}{po_tag} "
              f"(exp={expected:.0f} vs line={selection_line:.0f}, "
              f"league={league_avg:.0f}, ratio={score:.3f})")
    return True, detail, score


def analyze_recent_form(games, team_id, n=RECENT_N):
    """Analyze last N finished games for a team.
    Returns dict with avg_total, totals, std_dev, last_game_ts, n_games.
    Returns None if insufficient data."""
    if not games:
        return None
    last_n = games[:n]
    if len(last_n) < MIN_GAMES:
        return None

    totals = [g["total"] for g in last_n]
    team_points = []
    for g in last_n:
        if g["home_id"] == team_id:
            team_points.append(g["home_total"])
        else:
            team_points.append(g["away_total"])

    avg_total = sum(totals) / len(totals)
    avg_team_pts = sum(team_points) / len(team_points)
    variance = sum((t - avg_total) ** 2 for t in totals) / len(totals)
    std_dev = math.sqrt(variance)
    last_ts = games[0]["timestamp"] if games else 0

    # Q1 pace proxy – avg total bodů v Q1
    q1_games = [g for g in last_n if "q1_total" in g]
    avg_q1 = (sum(g["q1_total"] for g in q1_games) / len(q1_games)) if q1_games else None

    # Blowout rate – kolik % posledních zápasů končilo s rozdílem > BLOWOUT_MARGIN
    blowout_count = sum(1 for g in last_n if g.get("margin", 0) > BLOWOUT_MARGIN)
    blowout_rate = blowout_count / len(last_n) if last_n else 0.0

    # 2nd half (Q3+Q4) analysis – like football's 2nd-half filter
    sh_games = [g for g in last_n if "total_sh" in g and g.get("reg_total", g["total"]) > 0]
    if sh_games:
        sh_ratios = [g["total_sh"] / g.get("reg_total", g["total"]) for g in sh_games]
        avg_sh_ratio = sum(sh_ratios) / len(sh_ratios)
        team_sh = []
        team_sh_conceded = []
        for g in sh_games:
            if g["home_id"] == team_id:
                team_sh.append(g.get("h_sh", 0))
                team_sh_conceded.append(g.get("a_sh", 0))
            else:
                team_sh.append(g.get("a_sh", 0))
                team_sh_conceded.append(g.get("h_sh", 0))
        avg_team_sh = sum(team_sh) / len(team_sh) if team_sh else 0
        avg_team_sh_conceded = sum(team_sh_conceded) / len(team_sh_conceded) if team_sh_conceded else 0
    else:
        avg_sh_ratio = None
        avg_team_sh = None
        avg_team_sh_conceded = None

    return {
        "avg_total": avg_total,
        "avg_team_pts": avg_team_pts,
        "totals": totals,
        "std_dev": std_dev,
        "last_game_ts": last_ts,
        "n_games": len(last_n),
        "avg_q1": avg_q1,
        "blowout_rate": blowout_rate,
        "avg_sh_ratio": avg_sh_ratio,
        "avg_team_sh": avg_team_sh,
        "avg_team_sh_conceded": avg_team_sh_conceded,
    }


def meets_enhanced_criteria(home_form, away_form, h2h_games,
                            selection_line, safe_line, game_ts):
    """Enhanced criteria – recent form, H2H, consistency, rest.

    1. Recent form: avg total of last N games >= RECENT_FLOOR_R * selection_line
    2. Over hit rate: >= MIN_OVER_HIT_RATE of last N games had total >= safe_line
    3. Consistency: std_dev <= MAX_TOTAL_SD (both teams)
    4. H2H: if >= H2H_MIN_GAMES, avg total >= H2H_OVER_R * selection_line
    5. Rest: both teams rested >= MIN_REST_HOURS

    Returns (ok, detail_string).
    """
    parts = []

    # --- Checks 1-3 & 5: Recent form (both teams must have data) ---
    if home_form and away_form:
        # Check 1: Rolling average total
        min_avg = selection_line * RECENT_FLOOR_R
        h_avg = home_form["avg_total"]
        a_avg = away_form["avg_total"]
        if h_avg < min_avg or a_avg < min_avg:
            return False, (f"recent avg low: {h_avg:.0f}/{a_avg:.0f} "
                           f"(min {min_avg:.0f}={RECENT_FLOOR_R}*{selection_line:.0f})")

        # Check 2: Over hit rate (against safe line)
        h_over = sum(1 for t in home_form["totals"] if t >= safe_line) / home_form["n_games"]
        a_over = sum(1 for t in away_form["totals"] if t >= safe_line) / away_form["n_games"]
        if h_over < MIN_OVER_HIT_RATE or a_over < MIN_OVER_HIT_RATE:
            return False, (f"over rate low: {h_over:.0%}/{a_over:.0%} "
                           f"(min {MIN_OVER_HIT_RATE:.0%} @ safe={safe_line:.0f})")

        # Check 3: Consistency (standard deviation)
        h_sd = home_form["std_dev"]
        a_sd = away_form["std_dev"]
        if h_sd > MAX_TOTAL_SD or a_sd > MAX_TOTAL_SD:
            return False, (f"inconsistent: SD {h_sd:.1f}/{a_sd:.1f} "
                           f"(max {MAX_TOTAL_SD:.0f})")
        # Příliš nízká SD = defenzivní/pomalý tým držící zápas pod linií.
        # Min(h_sd, a_sd) < MIN_TOTAL_SD => fail (Zwolle SD=11 → 130 v 148.5 line).
        if min(h_sd, a_sd) < MIN_TOTAL_SD:
            return False, (f"too defensive: min SD {min(h_sd, a_sd):.1f} < {MIN_TOTAL_SD:.0f} "
                           f"(SD={h_sd:.1f}/{a_sd:.1f}, jeden tým drží tempo dolů)")

        # Check 3a: Form gap – velký rozdíl mezi týmy = různé tempo (chimérický průměr)
        # Příklad: jeden tým hraje 162, druhý 178 → "průměr" 170 je často spíš náhoda
        form_gap = abs(h_avg - a_avg)
        if form_gap > MAX_FORM_GAP:
            return False, (f"form gap too large: {h_avg:.0f} vs {a_avg:.0f} "
                           f"(gap={form_gap:.0f} > {MAX_FORM_GAP:.0f}, různé tempo)")

        parts.append(f"form={h_avg:.0f}/{a_avg:.0f} over={h_over:.0%}/{a_over:.0%} "
                     f"SD={h_sd:.0f}/{a_sd:.0f} gap={form_gap:.0f}")

        # Check 3b: Q1 pace proxy – pomalý start = riziko Under
        # Očekávaná Q1 hodnota = selection_line / 4 (jeden quarter z reg času)
        h_q1 = home_form.get("avg_q1")
        a_q1 = away_form.get("avg_q1")
        if h_q1 is not None and a_q1 is not None:
            q1_floor = (selection_line / 4) * (1 + MIN_Q1_RATIO - 0.25)  # baseline Q1 ~ line/4
            # Jednodušší práh: oba týmy musí mít Q1 ratio >= MIN_Q1_RATIO ze selection_line
            h_q1_r = h_q1 / selection_line
            a_q1_r = a_q1 / selection_line
            if h_q1_r < MIN_Q1_RATIO or a_q1_r < MIN_Q1_RATIO:
                return False, (f"slow Q1 start: ratio {h_q1_r:.0%}/{a_q1_r:.0%} "
                               f"(min {MIN_Q1_RATIO:.0%} of {selection_line:.0f})")
            parts.append(f"Q1={h_q1:.0f}/{a_q1:.0f}({h_q1_r:.0%}/{a_q1_r:.0%})")

        # Check 3c: Blowout rate – garbage time tlačí Q4 dolů
        h_blow = home_form.get("blowout_rate", 0.0)
        a_blow = away_form.get("blowout_rate", 0.0)
        if h_blow > MAX_BLOWOUT_RATE or a_blow > MAX_BLOWOUT_RATE:
            return False, (f"blowout-heavy: rate {h_blow:.0%}/{a_blow:.0%} "
                           f"(max {MAX_BLOWOUT_RATE:.0%}, margin>{BLOWOUT_MARGIN})")
        parts.append(f"blow={h_blow:.0%}/{a_blow:.0%}")

        # Check 5: Rest (back-to-back filter) – přeskočeno pokud MIN_REST_HOURS == 0
        if MIN_REST_HOURS > 0:
            game_dt = datetime.fromtimestamp(game_ts, tz=timezone.utc)
            h_last_dt = datetime.fromtimestamp(home_form["last_game_ts"], tz=timezone.utc)
            a_last_dt = datetime.fromtimestamp(away_form["last_game_ts"], tz=timezone.utc)
            h_rest_h = (game_dt - h_last_dt).total_seconds() / 3600
            a_rest_h = (game_dt - a_last_dt).total_seconds() / 3600
            if h_rest_h < MIN_REST_HOURS or a_rest_h < MIN_REST_HOURS:
                return False, (f"B2B fatigue: rest {h_rest_h:.0f}h/{a_rest_h:.0f}h "
                               f"(min {MIN_REST_HOURS}h)")
            parts.append(f"rest={h_rest_h:.0f}h/{a_rest_h:.0f}h")

        # Check 6: 2nd half ratio (Q3+Q4 scoring distribution)
        h_sh_ratio = home_form.get("avg_sh_ratio")
        a_sh_ratio = away_form.get("avg_sh_ratio")
        if h_sh_ratio is not None and a_sh_ratio is not None:
            if h_sh_ratio < MIN_2H_RATIO or a_sh_ratio < MIN_2H_RATIO:
                return False, (f"weak 2nd half: ratio {h_sh_ratio:.0%}/{a_sh_ratio:.0%} "
                               f"(min {MIN_2H_RATIO:.0%})")
            parts.append(f"2H={h_sh_ratio:.0%}/{a_sh_ratio:.0%}")

            # Check 7: 2nd half scoring baseline
            h_sh_scr = home_form.get("avg_team_sh")
            a_sh_scr = away_form.get("avg_team_sh")
            h_sh_con = home_form.get("avg_team_sh_conceded")
            a_sh_con = away_form.get("avg_team_sh_conceded")
            if all(v is not None for v in (h_sh_scr, a_sh_scr, h_sh_con, a_sh_con)):
                base_2h = (h_sh_scr + a_sh_scr + h_sh_con + a_sh_con) / 4
                if base_2h < MIN_2H_BASELINE:
                    return False, (f"2H baseline low: {base_2h:.1f} < {MIN_2H_BASELINE} "
                                   f"(scr {h_sh_scr:.1f}/{a_sh_scr:.1f}, con {h_sh_con:.1f}/{a_sh_con:.1f})")
                parts.append(f"2Hbase={base_2h:.0f}")
            else:
                parts.append("2Hbase=N/A")
        else:
            parts.append("2H=N/A")
    else:
        parts.append("form=N/A")

    # --- Check 4: Head-to-Head ---
    if h2h_games and len(h2h_games) >= H2H_MIN_GAMES:
        h2h_avg = sum(g["total"] for g in h2h_games) / len(h2h_games)
        # Při malém vzorku (n < H2H_RELIABLE_MIN_N) H2H průměr je statisticky
        # nespolehlivý → vyžaduje přísnější práh (>= 100 % selection_line),
        # jinak musíme H2H signál ignorovat. Leuven-Zwolle: H2H=157 z n=5,
        # bookmaker 159 → prošlo 0.92 prahem ale realita 130.
        if len(h2h_games) < H2H_RELIABLE_MIN_N:
            small_min = selection_line * H2H_SMALL_SAMPLE_R
            if h2h_avg < small_min:
                return False, (f"H2H small sample: avg {h2h_avg:.0f} < {small_min:.0f} "
                               f"({H2H_SMALL_SAMPLE_R}*{selection_line:.0f}, n={len(h2h_games)} "
                               f"< {H2H_RELIABLE_MIN_N} = nespolehlivé)")
        else:
            h2h_min = selection_line * H2H_OVER_R
            if h2h_avg < h2h_min:
                return False, (f"H2H avg low: {h2h_avg:.0f} "
                               f"(min {h2h_min:.0f}={H2H_OVER_R}*{selection_line:.0f}, "
                               f"n={len(h2h_games)})")

        # H2H HARD FAIL – při dostatečném vzorku (n >= 10) je H2H silnější signál
        # než recent form. Pokud H2H avg < 96 % selection_line, bookmaker už ví víc
        # o specifickém matchupu (typicky pomalejší tempo) – automatický fail.
        if len(h2h_games) >= H2H_HARD_FAIL_MIN_N:
            hard_min = selection_line * H2H_HARD_FAIL_R
            if h2h_avg < hard_min:
                return False, (f"H2H HARD FAIL: avg {h2h_avg:.0f} < {hard_min:.0f} "
                               f"({H2H_HARD_FAIL_R}*{selection_line:.0f}, n={len(h2h_games)}) "
                               f"– bookmaker zná matchup")

        # H2H blowout filtr – pokud byly H2H často blowouty, garbage time tlačí Q4 dolů
        h2h_blow = sum(1 for g in h2h_games if g.get("margin", 0) > BLOWOUT_MARGIN) / len(h2h_games)
        if h2h_blow > MAX_BLOWOUT_RATE:
            return False, (f"H2H blowouts: {h2h_blow:.0%} "
                           f"(max {MAX_BLOWOUT_RATE:.0%}, n={len(h2h_games)})")
        parts.append(f"H2H={h2h_avg:.0f}(n={len(h2h_games)},bl={h2h_blow:.0%})")
    else:
        n_h2h = len(h2h_games) if h2h_games else 0
        parts.append(f"H2H=N/A(n={n_h2h})")

    return True, " | ".join(parts)


def compute_quality_score(home_form, away_form, h2h_games,
                          selection_line, safe_line,
                          h_tt, a_tt):
    """Quality score 0-10 – body za splnění kvalitních (nepovinných) kritérií.
    Čím vyšší, tím lepší zápas. Min. MIN_QUALITY_SCORE pro propuštění do výsledků.

    Bodovník:
    + 2  recent form avg (oba) ≥ 1.10× safe_line — silný recent trend
    + 1  recent form avg (oba) > selection_line
    + 2  oba týmy mají Over hit-rate ≥ 80 % na safe_line
    + 1  oba mají Over hit-rate ≥ 75 %
    + 1  H2H avg ≥ selection_line (silný H2H signál)
    + 1  oba mají std_dev ≤ 60 % maximálního prahu (velmi konzistentní)
    + 1  oba mají 2H ratio ≥ 50 % (dominantní H2 scoring)
    + 1  oba TT Over kurzy ≤ 1.85 (silná konfirmace bookmakerem)
    + 1  oba mají blowout rate ≤ 20 % (málo garbage time)
    """
    pts = 0
    reasons = []

    if home_form and away_form:
        h_avg = home_form["avg_total"]
        a_avg = away_form["avg_total"]

        # 1. Recent form vs line
        if h_avg >= safe_line * 1.10 and a_avg >= safe_line * 1.10:
            pts += 2
            reasons.append("form≥1.10×safe(+2)")
        elif h_avg > selection_line and a_avg > selection_line:
            pts += 1
            reasons.append("form>sel(+1)")

        # 2. Over hit-rate
        h_over = sum(1 for t in home_form["totals"] if t >= safe_line) / home_form["n_games"]
        a_over = sum(1 for t in away_form["totals"] if t >= safe_line) / away_form["n_games"]
        if h_over >= 0.80 and a_over >= 0.80:
            pts += 2
            reasons.append("over≥80%(+2)")
        elif h_over >= 0.75 and a_over >= 0.75:
            pts += 1
            reasons.append("over≥75%(+1)")

        # 3. Konzistence (SD)
        if home_form["std_dev"] <= MAX_TOTAL_SD * 0.6 and away_form["std_dev"] <= MAX_TOTAL_SD * 0.6:
            pts += 1
            reasons.append("SD≤60%(+1)")

        # 4. 2H ratio dominantní pozdní scoring
        h_sh = home_form.get("avg_sh_ratio")
        a_sh = away_form.get("avg_sh_ratio")
        if h_sh is not None and a_sh is not None:
            if h_sh >= 0.50 and a_sh >= 0.50:
                pts += 1
                reasons.append("2H≥50%(+1)")

        # 5. Blowout rate – málo garbage time
        h_blow = home_form.get("blowout_rate", 0.0)
        a_blow = away_form.get("blowout_rate", 0.0)
        if h_blow <= 0.20 and a_blow <= 0.20:
            pts += 1
            reasons.append("blow≤20%(+1)")

    # 6. H2H signál
    if h2h_games and len(h2h_games) >= H2H_MIN_GAMES:
        h2h_avg = sum(g["total"] for g in h2h_games) / len(h2h_games)
        if h2h_avg >= selection_line:
            pts += 1
            reasons.append("H2H≥sel(+1)")

    # 7. TT silná konfirmace – kurz musí být skutečně overově nakloněný (≤ 1.70).
    # Dříve 1.85: prošlo i TT=1.01 (čistě neutrální line), což byl falešný bonus
    # (Leuven-Zwolle TT=1.01/1.01★ → reálně Under).
    if h_tt is not None and a_tt is not None and h_tt <= 1.70 and a_tt <= 1.70:
        pts += 1
        reasons.append("TT≤1.70(+1)")

    return pts, ", ".join(reasons) if reasons else "no bonuses"


# === FALLBACK pro baskets.json: random Over s nízkým kurzem ===
# Spouští se POUZE když po všech filtrech není v baskets.json žádný zápas.
# "Hod korunou" rozhodne, jestli se přidá 0 nebo 1 zápas (max 1).
# Idea: vezmi nějaký zápas, najdi jeho main line (~1.90, např. Over 180.5)
# a vyber NEJNIŽŠÍ Over linii (např. 168.5) s kurzem v rozsahu kolem 1.25.
BASKETS_FB_TARGET_OUT = 1.25
BASKETS_FB_OUT_MIN = 1.18
BASKETS_FB_OUT_MAX = 1.35
BASKETS_FB_MAX_ATTEMPTS = 20


def pick_random_basket_fallback(filtered_games, need):
    """Záchranný fallback PRO baskets.json (volat jen když je seznam prázdný).

    Náhodně projde předfiltrované zápasy (24h, bez RU/BY/women/youth), pro
    každý stáhne kurzy, najde main line + všechny Over linie a vybere
    nejnižší Over s kurzem v rozsahu ``BASKETS_FB_OUT_MIN..BASKETS_FB_OUT_MAX``
    (výrazně pod main – tj. "bezpečný" Over kolem 1.25). Vrací max ``need`` záznamů
    ve stejném tvaru jako standardní basket záznamy.
    """
    if need <= 0 or not filtered_games:
        return []

    candidates = list(filtered_games.items())
    random.shuffle(candidates)
    print(f"  Baskets fallback: hledám random Over s kurzem "
          f"{BASKETS_FB_OUT_MIN}-{BASKETS_FB_OUT_MAX} v {len(candidates)} zápasech "
          f"(need={need}, max_attempts={BASKETS_FB_MAX_ATTEMPTS})...")

    picked = []
    used_leagues = set()
    attempts = 0
    for gid, g in candidates:
        if len(picked) >= need:
            break
        if attempts >= BASKETS_FB_MAX_ATTEMPTS:
            break
        attempts += 1
        league_name = g.get("league", "?")
        if league_name in used_leagues:
            continue
        match_str = f"{g.get('home', '?')} vs {g.get('away', '?')}"
        odds_data = fetch_odds(gid)
        main_line, all_overs, _h_tt, _a_tt = find_main_line_and_all_overs(odds_data)
        if main_line is None or not all_overs:
            print(f"    - {match_str[:50]}: no odds")
            continue
        # Nejnižší Over linie pod main_line s kurzem v cílovém rozsahu.
        cap = main_line["line"]
        candidates_overs = [
            o for o in all_overs
            if o["line"] < cap
            and _is_half_line(o["line"])
            and BASKETS_FB_OUT_MIN <= o["avg_odd"] <= BASKETS_FB_OUT_MAX
        ]
        if not candidates_overs:
            print(f"    - {match_str[:50]}: žádný Over v {BASKETS_FB_OUT_MIN}-{BASKETS_FB_OUT_MAX} pod {cap}")
            continue
        chosen = min(candidates_overs, key=lambda x: x["line"])
        kickoff = datetime.fromtimestamp(g["timestamp"], tz=timezone.utc)\
            .strftime("%Y-%m-%dT%H:%M:%S+00:00")
        print(f"    \u2713 {match_str[:50]}: main={main_line['label']}@{main_line['odd_str']}"
              f" → {chosen['label']}@{chosen['odd_str']} ({league_name})")
        picked.append({
            "league": league_name,
            "match": match_str,
            "tip": chosen["label"],
            "odds": chosen["odd_str"],
            "date": kickoff,
        })
        used_leagues.add(league_name)

    return picked


# ===== MAIN =====

def main():
    if not API_KEY:
        print("API_BASKETBALL_KEY not set!")
        return

    now = datetime.now(timezone.utc)
    today = now.strftime("%Y-%m-%d")
    tomorrow = (now + timedelta(days=1)).strftime("%Y-%m-%d")
    cutoff = now + timedelta(hours=24)

    print("== SureBets Basketball Bot (v3 edge-first) ==")
    print(f"Time: {now.strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"Strategy: estimate fair total → detect bookmaker mispricing →")
    print(f"          output LOWEST Over line with avg odds >= {MIN_ODDS_OUT}")
    print(f"Min edge by region: NBA={MIN_EDGE_BY_REGION['NBA']}, "
          f"EU={MIN_EDGE_BY_REGION['EU']}, WORLD={MIN_EDGE_BY_REGION['WORLD']}")
    print(f"Excluded: countries={sorted(EXCLUDED_COUNTRIES)}, "
          f"women/youth keywords ({len(EXCLUDED_LEAGUE_KEYWORDS)})")
    print(f"Quality min={MIN_QUALITY_SCORE}, top-N/day={MAX_TIPS_PER_DAY}\n")

    # 1. Fetch games
    games_today = fetch_games(today)
    time.sleep(DELAY)
    games_tomorrow = fetch_games(tomorrow)
    all_games = {**games_today, **games_tomorrow}
    print(f"  Total: {len(all_games)} games\n")

    if not all_games:
        print("No games found.")
        with open(OUTPUT, "w", encoding="utf-8") as f:
            json.dump([], f)
        with open(OUTPUT_LIVE, "w", encoding="utf-8") as f:
            json.dump([], f)
        return

    # 2. Filter: time window + country + women/youth blacklist
    filtered = {}
    skipped_excl = 0
    for gid, g in all_games.items():
        ts = datetime.fromtimestamp(g["timestamp"], tz=timezone.utc)
        if ts < now or ts > cutoff:
            continue
        if _is_excluded_league(g.get("league", ""), g.get("country", "")):
            skipped_excl += 1
            continue
        filtered[gid] = g
    print(f"  After filter (24h, RU/BY/women/youth blocked={skipped_excl}): "
          f"{len(filtered)} games\n")

    if not filtered:
        with open(OUTPUT, "w", encoding="utf-8") as f:
            json.dump([], f)
        with open(OUTPUT_LIVE, "w", encoding="utf-8") as f:
            json.dump([], f)
        return

    # 3. STATS-FIRST: spočítat predikci pro každý zápas, fail-fast bez stats
    qualified = []
    print(f"  [Phase 1] Computing predicted total for {len(filtered)} games...")
    for i, (gid, g) in enumerate(filtered.items()):
        label = f"{g['home']} vs {g['away']}"
        print(f"  [{i+1}/{len(filtered)}] {label[:45]:.<47s}", end="")
        try:
            home_stats = fetch_team_stats(g["league_id"], g["season"], g["home_id"])
            away_stats = fetch_team_stats(g["league_id"], g["season"], g["away_id"])
            home_games = fetch_team_games(g["home_id"], g["league_id"], g["season"])
            away_games = fetch_team_games(g["away_id"], g["league_id"], g["season"])
            home_form = analyze_recent_form(home_games, g["home_id"])
            away_form = analyze_recent_form(away_games, g["away_id"])

            league_lower = (g["league"] or "").lower()
            is_playoff = any(kw in league_lower for kw in PLAYOFF_KEYWORDS)

            predicted, pred_detail = predict_total(
                home_stats, away_stats, home_form, away_form, is_playoff=is_playoff)
            if predicted is None:
                print(f" no stats ({pred_detail})")
                continue

            region = _classify_region(g["league"], g.get("country", ""))
            min_edge = MIN_EDGE_BY_REGION.get(region, MIN_EDGE_BY_REGION["WORLD"])
            print(f" {pred_detail} [{region} need edge>={min_edge}]")

            qualified.append({
                "game_id": gid, "league": g["league"], "league_id": g["league_id"],
                "season": g["season"], "match": label,
                "home_id": g["home_id"], "away_id": g["away_id"],
                "country": g.get("country", ""),
                "is_playoff": is_playoff, "region": region,
                "predicted": predicted, "min_edge": min_edge,
                "home_stats": home_stats, "away_stats": away_stats,
                "home_form": home_form, "away_form": away_form,
                "home_games": home_games, "away_games": away_games,
                "timestamp": g["timestamp"],
            })
        except Exception as exc:
            print(f" ERROR: {exc}")

    print(f"\n  Stats-qualified: {len(qualified)} of {len(filtered)} games\n")

    # POZN: dříve zde byl early return při qualified==0, který blokoval
    # spuštění baskets fallbacku (hod korunou). Teď necháme tok pokračovat –
    # pokud je qualified prázdný, smyčka níže nic neudělá, liveb.json se
    # zapíše jako [] a níže se vyhodnotí coin-toss fallback (0/1 zápas).

    # 4. ODDS + EDGE GATE: pro každý kvalifikovaný zápas najdi main line + edge
    results = []
    print(f"  [Phase 2] Fetching odds & checking edge for {len(qualified)} games...")
    for i, c in enumerate(qualified):
        print(f"  [{i+1}/{len(qualified)}] {c['match'][:45]:.<47s}", end="")
        try:
            odds_data = fetch_odds(c["game_id"])
            main_line, all_overs, h_tt, a_tt = find_main_line_and_all_overs(odds_data)
            if main_line is None:
                print(" no odds")
                continue

            sel_line = main_line["line"]
            edge = c["predicted"] - sel_line
            if edge < c["min_edge"]:
                print(f" no edge (pred={c['predicted']:.1f} − line={sel_line:.1f} "
                      f"= {edge:+.1f} < {c['min_edge']:.1f})")
                continue

            # Najdi výstupní linii: nejnižší dostupný Over s avg_odd >= MIN_ODDS_OUT,
            # bezpečnostní limit – jen linie pod main_line (jinak nemá smysl).
            out_line = pick_lowest_value_over(all_overs, MIN_ODDS_OUT, max_line=sel_line)
            if out_line is None:
                print(f" edge OK (+{edge:.1f}) but no value Over >= {MIN_ODDS_OUT}")
                continue
            if out_line["line"] >= sel_line:
                # ochrana – kupujeme nižší linii
                print(f" out line {out_line['line']} not below main {sel_line}")
                continue

            # Edge-first criteria gate (lehké venue/defense floory)
            ok, detail, score = meets_criteria(
                c["home_stats"], c["away_stats"], sel_line,
                is_playoff=c["is_playoff"],
                predicted_total=c["predicted"], min_edge=c["min_edge"])
            if not ok:
                print(f" criteria fail ({detail})")
                continue

            # Enhanced filtry (consistency, H2H, blowout, atd.) – ponecháno z v2
            h2h = fetch_h2h(c["home_id"], c["away_id"])
            ok2, detail2 = meets_enhanced_criteria(
                c["home_form"], c["away_form"], h2h,
                sel_line, out_line["line"], c["timestamp"])
            if not ok2:
                print(f" enhanced fail ({detail2})")
                continue

            # TT konfirmace bonus
            if (h_tt is not None and a_tt is not None
                    and h_tt <= TT_BONUS_ODDS and a_tt <= TT_BONUS_ODDS):
                score *= TT_BONUS_MULT
                detail2 += f" | TT={h_tt:.2f}/{a_tt:.2f}★"

            qpts, qdetail = compute_quality_score(
                c["home_form"], c["away_form"], h2h,
                sel_line, out_line["line"], h_tt, a_tt)
            if qpts < MIN_QUALITY_SCORE:
                print(f" quality fail (Q={qpts}/{MIN_QUALITY_SCORE}: {qdetail})")
                continue

            print(f" ★ EDGE+{edge:.1f} Q={qpts} → {out_line['label']}@{out_line['odd_str']}")
            print(f"       {detail}")
            print(f"       enhanced: {detail2}")
            print(f"       quality: {qdetail}")
            kickoff = datetime.fromtimestamp(c["timestamp"], tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")
            results.append({
                "league": c["league"],
                "match": c["match"],
                "tip": out_line["label"],
                "odds": out_line["odd_str"],
                "date": kickoff,
                "_score": score,
                "_quality": qpts,
                "_sel_label": main_line["label"],
                "_sel_odds": main_line["odd_str"],
                "_edge": edge,
            })
        except Exception as exc:
            print(f" ERROR: {exc}")

    # 5a. liveb.json – všechny qualifying zápasy s pre-match SELECTION (main) line
    live_results = sorted(results, key=lambda r: r["date"])
    live_out = [{
        "league": r["league"],
        "match": r["match"],
        "tip": r["_sel_label"],
        "odds": r["_sel_odds"],
        "date": r["date"],
    } for r in live_results]
    with open(OUTPUT_LIVE, "w", encoding="utf-8") as f:
        json.dump(live_out, f, indent=2, ensure_ascii=False)
    print(f"\n  Live: {len(live_out)} match(es) \u2192 {OUTPUT_LIVE}")

    # 5b. Best per league
    before = len(results)
    best_per_league = {}
    for r in results:
        lg = r["league"]
        rank = (r["_quality"], r["_score"], r["_edge"])
        cur_rank = (best_per_league[lg]["_quality"], best_per_league[lg]["_score"], best_per_league[lg]["_edge"]) if lg in best_per_league else (-1, -1, -1)
        if rank > cur_rank:
            best_per_league[lg] = r
    results = list(best_per_league.values())
    if before > len(results):
        print(f"  Dedup: {before} → {len(results)} (best per league by Q+score+edge)")

    # 5c. Globální TOP-N
    if len(results) > MAX_TIPS_PER_DAY:
        results.sort(key=lambda r: (r["_quality"], r["_edge"], r["_score"]), reverse=True)
        before_n = len(results)
        results = results[:MAX_TIPS_PER_DAY]
        print(f"  Top-N filter: {before_n} → {len(results)} (max {MAX_TIPS_PER_DAY}/day)")

    for r in results:
        r.pop("_score", None)
        r.pop("_quality", None)
        r.pop("_sel_label", None)
        r.pop("_sel_odds", None)
        r.pop("_edge", None)

    # 5d. BASKETS FALLBACK: pokud po všech filtrech není ani jeden zápas,
    #     "hod si korunou" – přidej 0 nebo 1 random zápas (max 1) s nejnižší
    #     Over linií okolo kurzu 1.25 (např. main 180.5@1.90 → tip 168.5@1.25).
    if not results:
        coin = random.choice([0, 1])
        if coin == 0:
            print(f"\n  ⚠ baskets.json prázdný – hod korunou: need=0 (nic nepřidávám)")
        else:
            print(f"\n  ⚠ baskets.json prázdný – hod korunou: need=1 "
                  f"(target Over ≈ {BASKETS_FB_TARGET_OUT})")
            results = pick_random_basket_fallback(filtered, coin)

    results.sort(key=lambda r: r["date"])
    with open(OUTPUT, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    print(f"\n{'='*50}")
    print(f"  Results: {len(results)} match(es) → {OUTPUT} + {OUTPUT_LIVE}")
    print(f"  API requests: {request_count} / 7500 ({request_count * 100 // 7500}%)")


if __name__ == "__main__":
    main()
