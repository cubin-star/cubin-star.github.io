"""
Ultimate Football Overs — Daily Tip Generator v5
Fetches ALL upcoming matches from The Odds API across 80+ leagues,
collects every Over 2.5 candidate (odds 1.75–2.20), then picks the 3 best tips.

v5 Changes:
  - Single API key: API_FOOTBALL_KEY (100 requests/day)
  - Expanded to 80+ leagues
  - ALWAYS scans ALL leagues (no early stopping)
  - Smart retry with exponential backoff on rate limits
  - Multiple bookmaker regions (eu + uk + au)
  - 24h time window
  - Odds range 1.75–2.20

Usage:
  python generate_tips1.py

Environment variable required:
  API_FOOTBALL_KEY — API key from https://the-odds-api.com (100 req/day plan)

Output:
  fotbal.json — JSON array consumed by the mobile app
"""

import os
import json
import random
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta

API_KEY = os.environ.get("API_FOOTBALL_KEY", "")
MIN_ODDS = 1.75
MAX_ODDS = 2.20
NUM_TIPS = 3
HOURS_AHEAD = 24
DELAY_BETWEEN_REQUESTS = 1.0
MAX_RETRIES = 3
OUTPUT_FILE = "fotbal.json"

# 80+ leagues — with 100 req/day we can afford this
LEAGUES = [
    # === TOP 5 EUROPEAN LEAGUES ===
    ("soccer_epl",                          "Premier League"),
    ("soccer_spain_la_liga",                "La Liga"),
    ("soccer_germany_bundesliga",           "Bundesliga"),
    ("soccer_italy_serie_a",               "Serie A"),
    ("soccer_france_ligue_one",            "Ligue 1"),
    # === EUROPEAN CUPS ===
    ("soccer_uefa_champs_league",          "Champions League"),
    ("soccer_uefa_europa_league",          "Europa League"),
    ("soccer_uefa_europa_conference_league","Conference League"),
    # === STRONG EUROPEAN LEAGUES ===
    ("soccer_netherlands_eredivisie",       "Eredivisie"),
    ("soccer_portugal_primeira_liga",       "Primeira Liga"),
    ("soccer_turkey_super_league",          "Turkish Süper Lig"),
    ("soccer_belgium_first_div",            "Belgian Pro League"),
    ("soccer_scotland_premiership",         "Scottish Premiership"),
    ("soccer_austria_bundesliga",           "Austrian Bundesliga"),
    ("soccer_switzerland_superleague",      "Swiss Super League"),
    # === SCANDINAVIA ===
    ("soccer_denmark_superliga",            "Danish Superliga"),
    ("soccer_sweden_allsvenskan",           "Swedish Allsvenskan"),
    ("soccer_norway_eliteserien",           "Norwegian Eliteserien"),
    ("soccer_finland_veikkausliiga",        "Finnish Veikkausliiga"),
    ("soccer_iceland_urvalsdeild",          "Icelandic Úrvalsdeild"),
    # === EASTERN EUROPE ===
    ("soccer_poland_ekstraklasa",           "Polish Ekstraklasa"),
    ("soccer_greece_super_league",          "Greek Super League"),
    ("soccer_czech_czech_football_league",  "Czech First League"),
    ("soccer_romania_liga_1",               "Romanian Liga 1"),
    ("soccer_croatia_hnl",                  "Croatian HNL"),
    ("soccer_serbia_super_liga",            "Serbian Super Liga"),
    ("soccer_hungary_nb1",                  "Hungarian NB I"),
    ("soccer_bulgaria_first_league",        "Bulgarian First League"),
    ("soccer_slovakia_super_liga",          "Slovak Super Liga"),
    ("soccer_ukraine_premier_league",       "Ukrainian Premier League"),
    ("soccer_russia_premier_league",        "Russian Premier League"),
    ("soccer_cyprus_first_division",        "Cypriot First Division"),
    # === SECOND DIVISIONS ===
    ("soccer_efl_champ",                    "EFL Championship"),
    ("soccer_england_league1",              "EFL League One"),
    ("soccer_england_league2",              "EFL League Two"),
    ("soccer_germany_bundesliga2",          "Bundesliga 2"),
    ("soccer_germany_liga3",                "3. Liga"),
    ("soccer_spain_segunda_division",       "La Liga 2"),
    ("soccer_italy_serie_b",               "Serie B"),
    ("soccer_france_ligue_two",            "Ligue 2"),
    ("soccer_netherlands_eerste_divisie",   "Eerste Divisie"),
    ("soccer_portugal_segunda_liga",        "Liga Portugal 2"),
    ("soccer_turkey_1_lig",                "Turkish 1. Lig"),
    ("soccer_scotland_championship",        "Scottish Championship"),
    ("soccer_belgium_first_div_b",          "Belgian First Division B"),
    # === CUPS ===
    ("soccer_fa_cup",                       "FA Cup"),
    ("soccer_efl_cup",                      "EFL Cup"),
    ("soccer_spain_copa_del_rey",           "Copa del Rey"),
    ("soccer_italy_coppa_italia",           "Coppa Italia"),
    ("soccer_germany_dfb_pokal",            "DFB-Pokal"),
    ("soccer_france_coupe_de_france",       "Coupe de France"),
    # === SOUTH AMERICA ===
    ("soccer_brazil_campeonato",            "Brasileirão"),
    ("soccer_brazil_serie_b",              "Brasileirão Série B"),
    ("soccer_argentina_primera_division",   "Argentine Liga"),
    ("soccer_conmebol_copa_libertadores",   "Copa Libertadores"),
    ("soccer_conmebol_copa_sudamericana",   "Copa Sudamericana"),
    ("soccer_chile_primera_division",       "Chilean Primera"),
    ("soccer_colombia_primera_a",           "Colombian Primera A"),
    ("soccer_peru_primera_division",        "Peruvian Primera"),
    ("soccer_ecuador_serie_a",              "Ecuadorian Serie A"),
    ("soccer_uruguay_primera_division",     "Uruguayan Primera"),
    ("soccer_paraguay_primera_division",    "Paraguayan Primera"),
    # === NORTH & CENTRAL AMERICA ===
    ("soccer_usa_mls",                      "MLS"),
    ("soccer_usa_usl_championship",         "USL Championship"),
    ("soccer_mexico_ligamx",               "Liga MX"),
    ("soccer_costa_rica_primera_division",  "Costa Rica Primera"),
    # === ASIA ===
    ("soccer_japan_j_league",               "J-League"),
    ("soccer_korea_kleague1",              "K-League"),
    ("soccer_china_superleague",            "Chinese Super League"),
    ("soccer_saudi_professional_league",    "Saudi Pro League"),
    ("soccer_uae_arabian_gulf_league",      "UAE Pro League"),
    ("soccer_india_super_league",           "Indian Super League"),
    ("soccer_thailand_thai_league_1",       "Thai League 1"),
    # === AFRICA ===
    ("soccer_south_africa_psl",             "South African PSL"),
    ("soccer_egypt_premier_league",         "Egyptian Premier League"),
    # === OCEANIA ===
    ("soccer_australia_aleague",            "A-League"),
    # === INTERNATIONAL ===
    ("soccer_fifa_world_cup_qualifier",     "WC Qualifiers"),
    ("soccer_uefa_european_championship",   "Euro Championship"),
    ("soccer_conmebol_copa_america",        "Copa América"),
    ("soccer_africa_cup_of_nations",        "AFCON"),
]


def fetch_odds(sport_key: str, league_name: str) -> list:
    """Fetch odds with exponential backoff retry."""
    url = (
        f"https://api.the-odds-api.com/v4/sports/{sport_key}/odds/"
        f"?apiKey={API_KEY}"
        f"&regions=eu,uk,au"
        f"&markets=totals"
        f"&oddsFormat=decimal"
        f"&dateFormat=iso"
    )

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=15) as resp:
                remaining = resp.headers.get("x-requests-remaining", "?")
                print(f" 📡{remaining}", end="")
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 429:
                wait = 3 * (2 ** attempt)
                print(f" ⏳{wait}s", end="")
                time.sleep(wait)
            elif e.code in (404, 422):
                return []
            elif e.code == 401:
                print(" ❌KEY!")
                return []
            else:
                return []
        except Exception:
            return []
    return []


def extract_candidates(events: list, sport_key: str, league_name: str) -> list:
    """Extract Over 2.5 candidates with odds between MIN_ODDS and MAX_ODDS."""
    now = datetime.now(timezone.utc)
    cutoff = now + timedelta(hours=HOURS_AHEAD)
    candidates = []

    for event in events:
        commence = event.get("commence_time", "")
        try:
            dt = datetime.fromisoformat(commence.replace("Z", "+00:00"))
        except ValueError:
            continue

        if dt < now or dt > cutoff:
            continue

        home = event.get("home_team", "?")
        away = event.get("away_team", "?")

        over25_odds = []
        for bm in event.get("bookmakers", []):
            for mkt in bm.get("markets", []):
                if mkt.get("key") != "totals":
                    continue
                for out in mkt.get("outcomes", []):
                    if out.get("name") == "Over" and out.get("point") == 2.5:
                        price = float(out.get("price", 0))
                        if price > 0:
                            over25_odds.append(price)

        if not over25_odds:
            continue

        best = max(over25_odds)
        avg = sum(over25_odds) / len(over25_odds)

        if best >= MIN_ODDS and best <= MAX_ODDS:
            candidates.append({
                "League": league_name,
                "Match": f"{home} vs {away}",
                "Tip": "Over 2.5",
                "Odds": f"{best:.2f}",
                "sport_key": sport_key,
                "best": best,
                "avg": avg,
                "bm_count": len(over25_odds),
            })

    return candidates


def select_best_tips(all_candidates: list, num: int = NUM_TIPS) -> list:
    """Pick the best 3 from different leagues. Score favors bookmaker consensus."""
    for c in all_candidates:
        c["score"] = c["avg"] * min(c["bm_count"], 8)

    all_candidates.sort(key=lambda x: x["score"], reverse=True)

    selected = []
    used_leagues = set()
    for c in all_candidates:
        if c["sport_key"] in used_leagues:
            continue
        selected.append(c)
        used_leagues.add(c["sport_key"])
        if len(selected) >= num:
            break

    if len(selected) < num:
        for c in all_candidates:
            if c not in selected:
                selected.append(c)
                if len(selected) >= num:
                    break

    random.shuffle(selected)
    return selected[:num]


def main():
    if not API_KEY:
        print("❌ API_FOOTBALL_KEY not set!")
        return

    print(f"🕐 {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"🔍 Over 2.5 | odds {MIN_ODDS}–{MAX_ODDS} | {HOURS_AHEAD}h window")
    print(f"🔑 API_FOOTBALL_KEY (100 req/day)")
    print(f"📋 Scanning ALL {len(LEAGUES)} leagues\n")

    all_candidates = []
    leagues_hit = 0

    for i, (sport_key, league_name) in enumerate(LEAGUES):
        if i > 0:
            time.sleep(DELAY_BETWEEN_REQUESTS)

        print(f"  [{i+1:2d}/{len(LEAGUES)}] {league_name:.<35s}", end="")
        events = fetch_odds(sport_key, league_name)

        if events:
            cands = extract_candidates(events, sport_key, league_name)
            if cands:
                print(f" ✅ {len(cands)} matches")
                all_candidates.extend(cands)
                leagues_hit += 1
            else:
                print(f" — no matches")
        else:
            print(f" — skip")

    print(f"\n{'='*55}")
    print(f"📊 COLLECTED: {len(all_candidates)} candidates from {leagues_hit} leagues")
    print(f"   Odds range: {MIN_ODDS}–{MAX_ODDS}")
    print(f"{'='*55}")

    if not all_candidates:
        print("❌ No matches found. Keeping previous tips.")
        return

    tips = select_best_tips(all_candidates)

    output = []
    for t in tips:
        output.append({
            "League": t["League"],
            "Match": t["Match"],
            "Tip": t["Tip"],
            "Odds": t["Odds"],
        })

    print(f"\n🎯 FINAL {len(output)} tips (from {len(all_candidates)} candidates):")
    for i, tip in enumerate(output, 1):
        label = "🔓" if i <= 2 else "🔒 (ad)"
        print(f"  {label} {tip['League']}: {tip['Match']} — {tip['Tip']} @ {tip['Odds']}")

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(f"\n✅ Written to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
