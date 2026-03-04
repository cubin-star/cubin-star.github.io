import os
import json
import random
import requests
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

API_KEY = os.environ["ODDS_API_KEY2"]
OUTPUT_FILE = "basketbal.json"

MIN_ODDS = 1.75
MAX_TIPS = 2
WINDOW_HOURS = 24
TZ_CET = ZoneInfo("Europe/Prague")


def get_basketball_sports():
    """Dynamicky stahne VSECHNY dostupne basketbalove ligy z API."""
    url = f"https://api.the-odds-api.com/v4/sports/?apiKey={API_KEY}"
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()

    sports = []
    for s in resp.json():
        if s.get("group", "").lower() == "basketball" and s.get("active", False):
            sports.append(s["key"])

    print(f"Nalezeno {len(sports)} aktivnich basketbalovych lig:")
    for s in sports:
        print(f"  - {s}")
    return sports


def fetch_over_tips():
    """Stahne basketbalove zapasy s over/under trhem a vyfiltruje over tipy."""
    sports = get_basketball_sports()
    if not sports:
        print("Zadne basketbalove ligy nejsou dostupne!")
        return []

    candidates = []

    # Casove okno: od ted do +24h (vzdy dopredu)
    now_cet = datetime.now(TZ_CET)
    window_end = now_cet + timedelta(hours=WINDOW_HOURS)
    print(f"\nCasove okno: {now_cet.strftime('%Y-%m-%d %H:%M')} - {window_end.strftime('%Y-%m-%d %H:%M')} CET")

    for sport in sports:
        url = (
            f"https://api.the-odds-api.com/v4/sports/{sport}/odds/"
            f"?apiKey={API_KEY}"
            f"&regions=eu,us,uk"
            f"&markets=totals"
            f"&oddsFormat=decimal"
        )

        print(f"\nStahuji {sport}...")
        resp = requests.get(url, timeout=30)

        if resp.status_code in (422, 404):
            print(f"  Liga {sport} momentalne nema dostupne zapasy, preskakuji.")
            continue

        if resp.status_code == 401:
            print("  Neplatny API klic! Zkontrolujte ODDS_API_KEY2.")
            return candidates

        resp.raise_for_status()
        games = resp.json()

        remaining = resp.headers.get("x-requests-remaining", "?")
        print(f"  Nalezeno {len(games)} zapasu. Zbyvajici API requesty: {remaining}")

        for game in games:
            home = game.get("home_team", "")
            away = game.get("away_team", "")
            league = game.get("sport_title", sport)
            commence = game.get("commence_time", "")

            # Filtr: zapas musi zacinat v okne ted .. +24h
            if not commence:
                continue
            try:
                game_time = datetime.fromisoformat(commence.replace("Z", "+00:00")).astimezone(TZ_CET)
            except ValueError:
                continue
            if game_time < now_cet or game_time >= window_end:
                continue

            # Projdi vsechny bookmakers a najdi nejlepsi over kurz
            best_odds = 0
            best_point = 0
            for bookmaker in game.get("bookmakers", []):
                for market in bookmaker.get("markets", []):
                    if market.get("key") != "totals":
                        continue
                    for outcome in market.get("outcomes", []):
                        if outcome.get("name") != "Over":
                            continue
                        odds = outcome.get("price", 0)
                        point = outcome.get("point", 0)
                        if odds > best_odds:
                            best_odds = odds
                            best_point = point

            if best_odds >= MIN_ODDS:
                candidates.append({
                    "league": league,
                    "match": f"{home} vs {away}",
                    "tip": f"Over {best_point}",
                    "odds": f"{best_odds:.2f}",
                    "commence": commence,
                    "odds_value": best_odds,
                })
                print(f"  + {league}: {home} vs {away} — Over {best_point} @ {best_odds:.2f}")

    return candidates


def select_best_tips(candidates):
    """Nahodne vybere MAX_TIPS tipu, idealne kazdy z jine ligy."""
    # Odstran duplicitni zapasy
    seen = set()
    unique = []
    for c in candidates:
        if c["match"] not in seen:
            seen.add(c["match"])
            unique.append(c)

    if len(unique) <= MAX_TIPS:
        return unique

    # Seskup podle ligy
    by_league = {}
    for c in unique:
        by_league.setdefault(c["league"], []).append(c)

    tips = []
    leagues = list(by_league.keys())
    random.shuffle(leagues)

    # Nejdriv vyber po jednom z ruznych lig
    for league in leagues:
        if len(tips) >= MAX_TIPS:
            break
        pick = random.choice(by_league[league])
        tips.append(pick)
        by_league[league].remove(pick)

    # Pokud jeste neni dost, doplni z ostatnich
    if len(tips) < MAX_TIPS:
        remaining = [c for lst in by_league.values() for c in lst if c not in tips]
        random.shuffle(remaining)
        tips.extend(remaining[:MAX_TIPS - len(tips)])

    return tips


def main():
    candidates = fetch_over_tips()
    print(f"\nCelkem nalezeno {len(candidates)} kandidatu s kurzem >= {MIN_ODDS}")

    if not candidates:
        print("Zadne vhodne tipy nenalezeny. Zapisuji prazdny soubor.")
        tips = []
    else:
        tips = select_best_tips(candidates)
        print(f"\nVybrano {len(tips)} tipu:")
        for t in tips:
            print(f"  {t['league']}: {t['match']} - {t['tip']} @ {t['odds']}")

    output = [
        {
            "league": t["league"],
            "match": t["match"],
            "tip": t["tip"],
            "odds": t["odds"],
        }
        for t in tips
    ]

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\nZapsano do {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
