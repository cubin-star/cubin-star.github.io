"""Denní generátor hokejových tipů Over 5.5 pro MAUI appku.

Stáhne kurzy z API-Sports Hockey API, najde Over 5.5 gólů s kurzem >= 1.75
a vybere 2 zápasy z různých lig na základě statistik týmů (scored/conceded).

Metoda výběru (3 kola, inspirováno fotbalovým Kombik Botem):
  1. Stáhne dnešní zápasy, odfiltruje blokované země/ligy
  2. Pro každý zápas stáhne kurzy, hledá Over 5.5 >= 1.75
  3. Pro kandidáty stáhne posledních 10 zápasů obou týmů
  4. Spočítá avg scored / avg conceded pro oba týmy
  5. Výběr ve 3 kolech:
       1. kolo (strict): oba conceded >= 3.0  + min. jeden scored >= 2.5
       2. kolo (relaxed): oba conceded >= 2.5 + min. jeden scored >= 2.5
       3. kolo (fallback): zbývající kandidáti
  6. Vážený náhodný výběr 2 zápasů z různých lig (váha = expectedGoals)

Poměr prahů fotbal → hokej (×2.0–2.2):
  Fotbal: conceded 1.5/1.3, scored 1.3, celkem ~2.5 g/z
  Hokej:  conceded 3.0/2.5, scored 2.5, celkem ~5.5 g/z

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
LAST_N_GAMES = 10
PICK_COUNT = 2

# Prahy pro výběr (proporcionálně z fotbalu: ×2.0–2.2)
MIN_CONCEDED_STRICT = 3.0    # 1. kolo: oba týmy inkasují >= 3.0 g/z
MIN_CONCEDED_RELAXED = 2.5   # 2. kolo: oba týmy inkasují >= 2.5 g/z
MIN_SCORED = 2.5             # Alespoň jeden tým střílí >= 2.5 g/z
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


def api_get(endpoint: str, params: dict | None = None) -> list:
    """API-Sports Hockey GET request (Premium: 300 req/min)."""
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
    print(f"   → {len(results)} výsledků")

    # Premium plan: 300 req/min → pauza 0.3s (bezpečná rezerva)
    time.sleep(0.3)
    return results


def fetch_team_stats(team_id: int, team_name: str) -> dict | None:
    """Stáhne posledních N zápasů týmu a spočítá avg scored/conceded.

    Vrátí:
      avg_scored   – průměr vstřelených gólů
      avg_conceded – průměr obdržených gólů
      avg_total    – průměr celkových gólů na zápas
      games        – počet analyzovaných zápasů
    """
    print(f"      📊 {team_name} (id={team_id})...")
    games = api_get("games", {"team": team_id, "last": LAST_N_GAMES})

    if not games:
        return None

    scored_list = []
    conceded_list = []
    total_list = []

    for g in games:
        home_id = g.get("teams", {}).get("home", {}).get("id")
        scores = g.get("scores", {})
        h = scores.get("home")
        a = scores.get("away")

        if h is None or a is None:
            continue

        h, a = int(h), int(a)
        total = h + a
        total_list.append(total)

        if team_id == home_id:
            scored_list.append(h)
            conceded_list.append(a)
        else:
            scored_list.append(a)
            conceded_list.append(h)

    n = len(total_list)
    if n == 0:
        return None

    avg_scored = sum(scored_list) / n
    avg_conceded = sum(conceded_list) / n
    avg_total = sum(total_list) / n

    print(f"         → {n} zápasů | scored={avg_scored:.1f}"
          f" conceded={avg_conceded:.1f} total={avg_total:.1f}")

    return {
        "avg_scored": avg_scored,
        "avg_conceded": avg_conceded,
        "avg_total": avg_total,
        "games": n,
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
        league_name = game.get("league", {}).get("name", "?")
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

    # ─── Krok 3: Statistiky týmů (scored/conceded) ───
    print()
    print(f"📈 Analýza týmů (posledních {LAST_N_GAMES} zápasů)...")
    print()

    random.shuffle(all_candidates)

    team_cache: dict[int, dict | None] = {}
    qualified_strict: list[dict] = []
    qualified_relaxed: list[dict] = []
    rest_candidates: list[dict] = []

    for c in all_candidates:
        home_id = c["_home_id"]
        away_id = c["_away_id"]

        print(f"   ⚔ {c['match']} ({c['league']})")

        # Team stats (s cache)
        if home_id not in team_cache:
            team_cache[home_id] = fetch_team_stats(home_id, c["_home_name"])
        if away_id not in team_cache:
            team_cache[away_id] = fetch_team_stats(away_id, c["_away_name"])

        hs = team_cache[home_id]
        as_ = team_cache[away_id]

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

        expected_goals = (hs["avg_scored"] + as_["avg_scored"]
                          + hs["avg_conceded"] + as_["avg_conceded"]) / 2
        c["_expected_goals"] = expected_goals

        h_scored = hs["avg_scored"]
        a_scored = as_["avg_scored"]
        h_conceded = hs["avg_conceded"]
        a_conceded = as_["avg_conceded"]

        # 1. kolo (strict): oba conceded >= 3.0, min. jeden > 3.0,
        #                    min. jeden scored >= 2.5
        if (h_conceded >= MIN_CONCEDED_STRICT
                and a_conceded >= MIN_CONCEDED_STRICT
                and (h_conceded > MIN_CONCEDED_STRICT
                     or a_conceded > MIN_CONCEDED_STRICT)
                and (h_scored >= MIN_SCORED or a_scored >= MIN_SCORED)):
            tag = "Q-STRICT"
            qualified_strict.append(c)

        # 2. kolo (relaxed): oba conceded >= 2.5, min. jeden scored >= 2.5
        elif (h_conceded >= MIN_CONCEDED_RELAXED
                and a_conceded >= MIN_CONCEDED_RELAXED
                and (h_scored >= MIN_SCORED or a_scored >= MIN_SCORED)):
            tag = "Q-RELAX"
            qualified_relaxed.append(c)

        else:
            tag = "---"
            rest_candidates.append(c)

        print(f"      → [{tag}] scored={h_scored:.1f}/{a_scored:.1f}"
              f" conceded={h_conceded:.1f}/{a_conceded:.1f}"
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
