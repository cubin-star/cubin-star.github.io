import os
import json
import requests
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

API_KEY = os.environ["ODDS_API_KEY2"]
OUTPUT_FILE = "basketbal.json"

# Basketbalove ligy dostupne v The Odds API
SPORTS = [
    "basketball_nba",
    "basketball_euroleague",
    "basketball_ncaab",
]

MIN_ODDS = 1.75
MAX_TIPS = 2
WINDOW_HOURS = 24
TZ_CET = ZoneInfo("Europe/Prague")


def fetch_over_tips():
    """Stahne basketbalove zapasy s over/under trhem a vyfiltruje over tipy."""
    candidates = []

    for sport in SPORTS:
        url = (
            f"https://api.the-odds-api.com/v4/sports/{sport}/odds/"
            f"?apiKey={API_KEY}"
            f"&regions=eu"
            f"&markets=totals"
            f"&oddsFormat=decimal"
        )

        print(f"Stahuji {sport}...")
        resp = requests.get(url, timeout=30)

        if resp.status_code == 422:
            print(f"  Liga {sport} momentalne nema dostupne zapasy, preskakuji.")
            continue

        if resp.status_code == 401:
            print("  Neplatny API klic! Zkontrolujte ODDS_API_KEY2.")
            continue

        resp.raise_for_status()
        games = resp.json()

        remaining = resp.headers.get("x-requests-remaining", "?")
        print(f"  Nalezeno {len(games)} zapasu. Zbyvajici API requesty: {remaining}")

        # Casove okno: od 8:00 CET dnes do 8:00 CET + 24h
        now_cet = datetime.now(TZ_CET)
        window_start = now_cet.replace(hour=8, minute=0, second=0, microsecond=0)
        if now_cet < window_start:
            window_start -= timedelta(days=1)
        window_end = window_start + timedelta(hours=WINDOW_HOURS)

        for game in games:
            home = game.get("home_team", "")
            away = game.get("away_team", "")
            league = game.get("sport_title", sport)
            commence = game.get("commence_time", "")

            # Filtr: zapas musi zacinat v okne 8:00 CET .. 8:00 CET + 24h
            if commence:
                try:
                    game_time = datetime.fromisoformat(commence.replace("Z", "+00:00")).astimezone(TZ_CET)
                except ValueError:
                    continue
                if game_time < window_start or game_time >= window_end:
                    continue
            else:
                continue

            for bookmaker in game.get("bookmakers", []):
                for market in bookmaker.get("markets", []):
                    if market.get("key") != "totals":
                        continue

                    for outcome in market.get("outcomes", []):
                        if outcome.get("name") != "Over":
                            continue

                        odds = outcome.get("price", 0)
                        point = outcome.get("point", 0)

                        if odds >= MIN_ODDS:
                            candidates.append({
                                "league": league,
                                "match": f"{home} vs {away}",
                                "tip": f"Over {point}",
                                "odds": f"{odds:.2f}",
                                "commence": commence,
                                "odds_value": odds,
                            })
                            break
                    break

    return candidates


def select_best_tips(candidates):
    """Vybere MAX_TIPS nejlepsich tipu - preferuje vyssi kurzy."""
    seen = set()
    unique = []
    for c in candidates:
        if c["match"] not in seen:
            seen.add(c["match"])
            unique.append(c)

    unique.sort(key=lambda x: x["odds_value"], reverse=True)
    return unique[:MAX_TIPS]


def main():
    candidates = fetch_over_tips()
    print(f"\nCelkem nalezeno {len(candidates)} kandidatu s kurzem >= {MIN_ODDS}")

    if not candidates:
        print("Zadne vhodne tipy nenalezeny. Zapisuji prazdny soubor.")
        tips = []
    else:
        tips = select_best_tips(candidates)
        print(f"Vybrano {len(tips)} tipu:")
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
