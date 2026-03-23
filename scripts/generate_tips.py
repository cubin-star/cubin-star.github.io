"""Denní generátor hokejových tipů Over 5.5 pro MAUI appku.

Stáhne kurzy z API-Sports Hockey API, najde Over 5.5 gólů s kurzem >= 1.75
a vybere 2 zápasy z různých lig na základě sezónních statistik týmů.

Používá endpoint /teams/statistics, který vrací:
  - goals.for.average  (home / away / all)  … avg vstřelených
  - goals.against.average (home / away / all) … avg obdržených
  - games.played, wins/loses s procenty

Metoda výběru (3 kola):
  1. Stáhne dnešní zápasy, odfiltruje blokované země/ligy
  2. Pro každý zápas stáhne kurzy, hledá Over 5.5 >= 1.75
  3. Pro kandidáty stáhne sezónní statistiky obou týmů
     (domácí: home split, hosté: away split)
  4. Výběr ve 3 kolech:
       1. kolo (strict): oba conceded >= 3.0, min. jeden scored >= 2.8
       2. kolo (relaxed): oba conceded >= 2.5, min. jeden scored >= 2.5
       3. kolo (fallback): zbývající kandidáti
  5. Vážený náhodný výběr 2 zápasů z různých lig (váha = expectedGoals)

API: https://v1.hockey.api-sports.io
Premium plan: 7 500 requestů/den, 300 req/min

Výstup: hokey.json (formát kompatibilní s TodaysTipsPage.xaml.cs)
"""

import os
import sys
import json
import time
import random
import requests
from datetime import datetime, timezone

API_KEY = os.environ.get("API_HOCKEY_KEY", "")
BASE_URL = "https://v1.hockey.api-sports.io"
MIN_ODDS = 1.75
OUTPUT_FILE = "hokey.json"
PICK_COUNT = 2

# Prahy pro výběr (sezónní průměry, home/away split)
MIN_CONCEDED_STRICT = 3.0    # 1. kolo: oba týmy inkasují >= 3.0 g/z
MIN_CONCEDED_RELAXED = 2.5   # 2. kolo: oba týmy inkasují >= 2.5 g/z
MIN_SCORED_STRICT = 2.8      # 1. kolo: min. jeden tým střílí >= 2.8 g/z
MIN_SCORED_RELAXED = 2.5     # 2. kolo: min. jeden tým střílí >= 2.5 g/z
MIN_PLAYED = 5               # Minimum odehraných zápasů

# Země, ze kterých se v ČR nedá sázet
BLOCKED_COUNTRIES = {"russia", "belarus"}

# Klíčová slova v názvu ligy → přeskočit
BLOCKED_LEAGUE_KEYWORDS = {
    "university", "universiade", "college", "ncaa", "u18", "u20", "ullh",
    "women", "feminine", "feminin", "frauen", "damer", "kvinner",
    "ladies", "femenin", "naiset", "kobiety", "feminino", "girls",
    "youth", "juniors", "junior", "reserves", "reserve", "amateur",
    "friendly", "simulation", "esports", "esport", "cyber",
}

# Bookmaři s nespolehlivými kurzy (exchange)
SKIP_BOOKMAKERS = {"betfair", "betfair exchange", "smarkets", "matchbook"}

# Počítadlo requestů
_request_count = 0


def api_get(endpoint: str, params: dict | None = None):
    """API-Sports Hockey GET request (Premium: 300 req/min).

    Vrací response z API — list nebo dict (záleží na endpointu).
    """
    global _request_count
    url = f"{BASE_URL}/{endpoint}"
    headers = {"x-apisports-key": API_KEY}
    resp = requests.get(url, headers=headers, params=params or {}, timeout=20)
    _request_count += 1

    remaining = resp.headers.get("x-ratelimit-requests-remaining", "?")
    print(f"   (req #{_request_count}, API zbývá: {remaining})")
    resp.raise_for_status()
    data = resp.json()

    errors = data.get("errors")
    if errors and (isinstance(errors, dict) and errors or isinstance(errors, list) and errors):
        print(f"   ⚠ API chyba: {errors}")
        return []

    results = data.get("response", [])
    if isinstance(results, list):
        print(f"   → {len(results)} výsledků")
    else:
        print(f"   → statistiky načteny")

    # Premium plan: 300 req/min → pauza 0.3s (bezpečná rezerva)
    time.sleep(0.3)
    return results


def fetch_team_season_stats(team_id: int, league_id: int, season: int,
                           team_name: str, date: str) -> dict | None:
    """Stáhne sezónní statistiky týmu přes /teams/statistics.

    Vrátí:
      avg_scored_home  – průměr vstřelených doma
      avg_scored_away  – průměr vstřelených venku
      avg_scored_all   – průměr vstřelených celkem
      avg_conceded_home – průměr obdržených doma
      avg_conceded_away – průměr obdržených venku
      avg_conceded_all  – průměr obdržených celkem
      games            – počet odehraných zápasů
      win_pct          – procento výher
    """
    print(f"      📊 {team_name} (id={team_id}, league={league_id}, season={season})...")
    data = api_get("teams/statistics", {
        "team": team_id,
        "league": league_id,
        "season": season,
        "date": date,
    })

    # /teams/statistics vrací objekt (ne list), api_get ho zabalí do []
    # nebo může vrátit přímo dict v response
    if not data:
        return None

    # API vrací response jako dict (ne list)
    stats = data if isinstance(data, dict) else data[0] if data else None
    if not stats:
        return None

    try:
        games_played = int(stats.get("games", {}).get("played", {}).get("all", 0))
        goals_for = stats.get("goals", {}).get("for", {})
        goals_against = stats.get("goals", {}).get("against", {})

        avg_scored_home = float(goals_for.get("average", {}).get("home", 0))
        avg_scored_away = float(goals_for.get("average", {}).get("away", 0))
        avg_scored_all = float(goals_for.get("average", {}).get("all", 0))

        avg_conceded_home = float(goals_against.get("average", {}).get("home", 0))
        avg_conceded_away = float(goals_against.get("average", {}).get("away", 0))
        avg_conceded_all = float(goals_against.get("average", {}).get("all", 0))

        win_pct = float(stats.get("games", {}).get("wins", {})
                        .get("all", {}).get("percentage", 0))
    except (ValueError, TypeError, AttributeError):
        print(f"         → chyba parsování statistik")
        return None

    if games_played == 0:
        return None

    print(f"         → {games_played} zápasů | scored(h/a/all)="
          f"{avg_scored_home:.1f}/{avg_scored_away:.1f}/{avg_scored_all:.1f}"
          f" conceded(h/a/all)="
          f"{avg_conceded_home:.1f}/{avg_conceded_away:.1f}/{avg_conceded_all:.1f}"
          f" win%={win_pct:.1%}")

    return {
        "avg_scored_home": avg_scored_home,
        "avg_scored_away": avg_scored_away,
        "avg_scored_all": avg_scored_all,
        "avg_conceded_home": avg_conceded_home,
        "avg_conceded_away": avg_conceded_away,
        "avg_conceded_all": avg_conceded_all,
        "games": games_played,
        "win_pct": win_pct,
    }


def weighted_pick(items: list[dict], count: int) -> list[dict]:
    """Vážený náhodný výběr bez opakování; váha = expected_goals. Každý z jiné ligy."""
    result = []
    used_leagues: set[str] = set()
    remaining = list(items)

    for _ in range(count):
        if not remaining:
            break
        available = [m for m in remaining if m["league"] not in used_leagues]
        if not available:
            break

        weights = [m.get("_expected_goals", 1.0) for m in available]
        total_w = sum(weights)
        r = random.random() * total_w
        idx = 0
        for i, w in enumerate(weights):
            r -= w
            if r <= 0:
                idx = i
                break

        pick = available[idx]
        result.append(pick)
        used_leagues.add(pick["league"])
        remaining.remove(pick)

    return result


def clean_candidate(c: dict) -> dict:
    """Odstraní interní pole začínající na '_'."""
    return {k: v for k, v in c.items() if not k.startswith("_")}


def main():
    if not API_KEY:
        print("❌ API_HOCKEY_KEY není nastaven!")
        sys.exit(1)

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    print(f"🏒 Generuji hokejové tipy – {today}")
    print(f"   Over 5.5, kurz >= {MIN_ODDS}")
    print()

    # ─── Krok 1: Dnešní zápasy ───
    print("📡 Stahuji dnešní hokejové zápasy...")
    games = api_get("games", {"date": today})

    if not games:
        print("⚠ Žádné zápasy dnes. Zapisuji prázdný JSON.")
        with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
            json.dump([], f, indent=2, ensure_ascii=False)
        return

    # Filtruj nezačaté + blokované
    ns_games = [g for g in games if g.get("status", {}).get("short") == "NS"]
    print(f"   Celkem: {len(games)}, nezačatých: {len(ns_games)}")

    before = len(ns_games)
    ns_games = [
        g for g in ns_games
        if g.get("country", {}).get("name", "").lower() not in BLOCKED_COUNTRIES
        and g.get("league", {}).get("country", "").lower() not in BLOCKED_COUNTRIES
    ]
    blocked = before - len(ns_games)
    if blocked:
        print(f"   🚫 Vyřazeno {blocked} zápasů (Rusko/Bělorusko)")

    before = len(ns_games)
    ns_games = [
        g for g in ns_games
        if not any(kw in g.get("league", {}).get("name", "").lower()
                   for kw in BLOCKED_LEAGUE_KEYWORDS)
    ]
    blocked = before - len(ns_games)
    if blocked:
        print(f"   🚫 Vyřazeno {blocked} zápasů (ženy/mládež/univerzity/...)")

    print(f"   Zápasy k analýze: {len(ns_games)}")
    print()

    if not ns_games:
        print("⚠ Žádné nezačaté zápasy po filtraci. Zapisuji prázdný JSON.")
        with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
            json.dump([], f, indent=2, ensure_ascii=False)
        return

    # ─── Krok 2: Kurzy Over 5.5 ───
    all_candidates = []

    for game in ns_games:
        game_id = game["id"]
        home_name = game.get("teams", {}).get("home", {}).get("name", "?")
        away_name = game.get("teams", {}).get("away", {}).get("name", "?")
        home_id = game.get("teams", {}).get("home", {}).get("id")
        away_id = game.get("teams", {}).get("away", {}).get("id")
        league_info = game.get("league", {})
        league_name = league_info.get("name", "?")
        league_id = league_info.get("id")
        season = league_info.get("season")
        match_label = f"{home_name} vs {away_name}"

        print(f"📋 {league_name}: {match_label}")
        odds_data = api_get("odds", {"game": game_id})

        if not odds_data:
            print(f"   (žádné kurzy)")
            print()
            continue

        best_price = None
        best_bookmaker = None

        for entry in odds_data:
            for bookmaker in entry.get("bookmakers", []):
                bk_name = bookmaker.get("name", "?")

                if bk_name.lower() in SKIP_BOOKMAKERS:
                    continue

                for bet in bookmaker.get("bets", []):
                    for value in bet.get("values", []):
                        val = str(value.get("value", ""))
                        odd_str = str(value.get("odd", "0"))

                        try:
                            price = float(odd_str)
                        except (ValueError, TypeError):
                            continue

                        if val == "Over 5.5" and 1.50 <= price <= 5.00:
                            if best_price is None or price < best_price:
                                best_price = price
                                best_bookmaker = bk_name

        if best_price is not None and best_price >= MIN_ODDS:
            print(f"   ✅ Over 5.5 @ {best_price} ({best_bookmaker})")
            all_candidates.append({
                "league": league_name,
                "match": match_label,
                "tip": "Over 5.5",
                "odds": str(round(best_price, 2)),
                "_home_id": home_id,
                "_away_id": away_id,
                "_home_name": home_name,
                "_away_name": away_name,
                "_league_id": league_id,
                "_season": season,
            })
        elif best_price is not None:
            print(f"   ⚠ Over 5.5 nalezen, ale kurz {best_price} < {MIN_ODDS}")
        else:
            print(f"   ❌ žádný Over 5.5")
        print()

    print(f"📊 Kandidátů s Over 5.5: {len(all_candidates)}")

    if not all_candidates:
        print("⚠ Žádní kandidáti. Zapisuji prázdný JSON.")
        with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
            json.dump([], f, indent=2, ensure_ascii=False)
        return

    # ─── Krok 3: Sezónní statistiky týmů (/teams/statistics) ───
    print()
    print("📈 Analýza týmů (sezónní statistiky, home/away split)...")
    print()

    random.shuffle(all_candidates)

    # Cache klíč = (team_id, league_id, season)
    team_cache: dict[tuple, dict | None] = {}
    qualified_strict: list[dict] = []
    qualified_relaxed: list[dict] = []
    rest_candidates: list[dict] = []

    for c in all_candidates:
        home_id = c["_home_id"]
        away_id = c["_away_id"]
        league_id = c["_league_id"]
        season = c["_season"]

        print(f"   ⚔ {c['match']} ({c['league']})")

        if not league_id or not season:
            print(f"      → SKIP (chybí league_id nebo season)")
            rest_candidates.append(c)
            print()
            continue

        # Team stats (s cache per team+league+season)
        hk = (home_id, league_id, season)
        ak = (away_id, league_id, season)
        if hk not in team_cache:
            team_cache[hk] = fetch_team_season_stats(
                home_id, league_id, season, c["_home_name"], today)
        if ak not in team_cache:
            team_cache[ak] = fetch_team_season_stats(
                away_id, league_id, season, c["_away_name"], today)

        hs = team_cache[hk]
        as_ = team_cache[ak]

        if not hs or not as_:
            print(f"      → SKIP (nedostatek dat)")
            rest_candidates.append(c)
            print()
            continue

        if hs["games"] < MIN_PLAYED or as_["games"] < MIN_PLAYED:
            print(f"      → SKIP (málo zápasů: {hs['games']}/{as_['games']})")
            rest_candidates.append(c)
            print()
            continue

        # Home/away split: domácí tým → domácí statistiky, hosté → venkovní
        h_scored = hs["avg_scored_home"]
        h_conceded = hs["avg_conceded_home"]
        a_scored = as_["avg_scored_away"]
        a_conceded = as_["avg_conceded_away"]

        # Expected goals = (home scored + away conceded + away scored + home conceded) / 2
        expected_goals = (h_scored + a_conceded + a_scored + h_conceded) / 2
        c["_expected_goals"] = expected_goals

        # 1. kolo (strict): oba conceded >= 3.0, min. jeden > 3.0,
        #                    min. jeden scored >= 2.8
        if (h_conceded >= MIN_CONCEDED_STRICT
                and a_conceded >= MIN_CONCEDED_STRICT
                and (h_conceded > MIN_CONCEDED_STRICT
                     or a_conceded > MIN_CONCEDED_STRICT)
                and (h_scored >= MIN_SCORED_STRICT
                     or a_scored >= MIN_SCORED_STRICT)):
            tag = "Q-STRICT"
            qualified_strict.append(c)

        # 2. kolo (relaxed): oba conceded >= 2.5, min. jeden scored >= 2.5
        elif (h_conceded >= MIN_CONCEDED_RELAXED
                and a_conceded >= MIN_CONCEDED_RELAXED
                and (h_scored >= MIN_SCORED_RELAXED
                     or a_scored >= MIN_SCORED_RELAXED)):
            tag = "Q-RELAX"
            qualified_relaxed.append(c)

        else:
            tag = "---"
            rest_candidates.append(c)

        print(f"      → [{tag}] home: scored={h_scored:.1f} conc={h_conceded:.1f}"
              f" | away: scored={a_scored:.1f} conc={a_conceded:.1f}"
              f" => expG={expected_goals:.1f}")
        print()

    print(f"1. kolo (strict, conceded >= {MIN_CONCEDED_STRICT}): "
          f"{len(qualified_strict)}")
    print(f"2. kolo (relaxed, conceded >= {MIN_CONCEDED_RELAXED}): "
          f"{len(qualified_relaxed)}")
    print(f"Zbytek: {len(rest_candidates)}")

    # ─── Krok 4: Výběr ve 3 kolech ───
    selected: list[dict] = []

    # 1. kolo
    print(f"\n--- 1. kolo výběru (strict: conceded >= {MIN_CONCEDED_STRICT}) ---")
    picked1 = weighted_pick(qualified_strict, PICK_COUNT)
    selected.extend(picked1)
    print(f"   Vybráno: {len(picked1)}")

    # 2. kolo – doplnit z relaxed
    if len(selected) < PICK_COUNT and qualified_relaxed:
        print(f"\n--- 2. kolo výběru (relaxed: conceded >= {MIN_CONCEDED_RELAXED}) ---")
        used_leagues = {m["league"] for m in selected}
        avail = [m for m in qualified_relaxed if m["league"] not in used_leagues]
        picked2 = weighted_pick(avail, PICK_COUNT - len(selected))
        selected.extend(picked2)
        print(f"   Doplněno: {len(picked2)}, celkem: {len(selected)}")

    # 3. kolo – fallback ze zbytku (z různých lig)
    if len(selected) < PICK_COUNT and rest_candidates:
        print(f"\n--- 3. kolo výběru (fallback) ---")
        used_leagues = {m["league"] for m in selected}
        avail = [m for m in rest_candidates if m["league"] not in used_leagues]
        random.shuffle(avail)
        for m in avail:
            if len(selected) >= PICK_COUNT:
                break
            selected.append(m)
            used_leagues.add(m["league"])
        print(f"   Doplněno na: {len(selected)}")

    # Pokud stále < 2, povolíme i stejnou ligu
    if len(selected) < PICK_COUNT:
        used_ids = {id(m) for m in selected}
        remaining = [m for m in all_candidates if id(m) not in used_ids]
        random.shuffle(remaining)
        for m in remaining:
            if len(selected) >= PICK_COUNT:
                break
            selected.append(m)

    print(f"\n✅ Vybrané tipy ({len(selected)}):")
    for t in selected:
        eg = t.get("_expected_goals", 0)
        print(f"   {t['league']}: {t['match']} → {t['tip']}"
              f" @ {t['odds']} (expG={eg:.1f})")

    # Vyčistit interní pole před zápisem
    result = [clean_candidate(t) for t in selected]

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    print(f"\n💾 Zapsáno do {OUTPUT_FILE}")
    print(f"📊 Celkem spotřebováno {_request_count} requestů (z 7500)")


if __name__ == "__main__":
    main()

