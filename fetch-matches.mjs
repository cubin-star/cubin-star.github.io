/**
 * Bot pro denní výběr 6 fotbalových zápasů s Over góly a kurzem >= 2.0
 *
 * Používá the-odds-api.com (zdarma 500 req/měsíc).
 * Výstup: hot.json ve formátu kompatibilním s Kombík frontendem.
 *
 * Env proměnné:
 *   ODDS_API_KEY4 – API klíč z https://the-odds-api.com
 *
 * Použití:
 *   node fetch-matches.mjs
 */

import { writeFileSync } from 'fs';

const API_KEY = process.env.ODDS_API_KEY4;
if (!API_KEY) {
    console.error('Chybí ODDS_API_KEY4 env proměnná.');
    process.exit(1);
}

const MIN_ODDS = 2.0;
const PICK_COUNT = 6;

const SOCCER_SPORTS = [
    'soccer_epl',
    'soccer_germany_bundesliga',
    'soccer_spain_la_liga',
    'soccer_italy_serie_a',
    'soccer_france_ligue_one',
    'soccer_uefa_champs_league',
    'soccer_uefa_europa_league',
    'soccer_efl_champ',
    'soccer_netherlands_eredivisie',
    'soccer_portugal_primeira_liga',
    'soccer_turkey_super_league',
    'soccer_brazil_campeonato',
    'soccer_usa_mls',
];

const LEAGUE_NAMES = {
    soccer_epl: 'Premier League',
    soccer_germany_bundesliga: 'Bundesliga',
    soccer_spain_la_liga: 'La Liga',
    soccer_italy_serie_a: 'Serie A',
    soccer_france_ligue_one: 'Ligue 1',
    soccer_uefa_champs_league: 'Champions League',
    soccer_uefa_europa_league: 'Europa League',
    soccer_efl_champ: '2. England',
    soccer_netherlands_eredivisie: 'Eredivisie',
    soccer_portugal_primeira_liga: 'Primeira Liga',
    soccer_turkey_super_league: 'Turkey Super Lig',
    soccer_brazil_campeonato: 'Brazil Serie A',
    soccer_usa_mls: 'MLS',
};

async function fetchOdds(sport) {
    const url = new URL(`https://api.the-odds-api.com/v4/sports/${sport}/odds/`);
    url.searchParams.set('apiKey', API_KEY);
    url.searchParams.set('regions', 'eu');
    url.searchParams.set('markets', 'totals');
    url.searchParams.set('oddsFormat', 'decimal');

    const res = await fetch(url);
    if (!res.ok) {
        console.warn(`  ⚠ ${sport}: HTTP ${res.status}`);
        return [];
    }
    return res.json();
}

function extractOverPicks(events, sportKey) {
    const picks = [];

    for (const event of events) {
        for (const bookmaker of event.bookmakers) {
            for (const market of bookmaker.markets) {
                if (market.key !== 'totals') continue;

                for (const outcome of market.outcomes) {
                    if (outcome.name !== 'Over') continue;
                    if (outcome.price < MIN_ODDS) continue;

                    picks.push({
                        league: LEAGUE_NAMES[sportKey] || sportKey,
                        match: `${event.home_team} - ${event.away_team}`,
                        tip: `Over ${outcome.point}`,
                        odds: outcome.price.toFixed(2),
                        commence: event.commence_time,
                        bookmaker: bookmaker.title,
                    });
                }
            }
        }
    }

    return picks;
}

async function main() {
    console.log('🤖 Kombík Bot – stahuji kurzy…\n');

    let allPicks = [];

    for (const sport of SOCCER_SPORTS) {
        console.log(`📡 ${LEAGUE_NAMES[sport] || sport}…`);
        const events = await fetchOdds(sport);
        const picks = extractOverPicks(events, sport);
        console.log(`   → ${picks.length} tipů s Over a kurzem >= ${MIN_ODDS}`);
        allPicks.push(...picks);
    }

    const best = new Map();
    for (const p of allPicks) {
        const key = p.match;
        if (!best.has(key) || parseFloat(p.odds) > parseFloat(best.get(key).odds)) {
            best.set(key, p);
        }
    }

    let unique = [...best.values()];
    unique.sort((a, b) => parseFloat(b.odds) - parseFloat(a.odds));

    const selected = [];
    const usedLeagues = new Set();

    for (const pick of unique) {
        if (selected.length >= PICK_COUNT) break;
        if (!usedLeagues.has(pick.league)) {
            selected.push(pick);
            usedLeagues.add(pick.league);
        }
    }

    for (const pick of unique) {
        if (selected.length >= PICK_COUNT) break;
        if (!selected.includes(pick)) {
            selected.push(pick);
        }
    }

    if (selected.length === 0) {
        console.warn('\n⚠ Žádné zápasy s Over a kurzem >= 2.0 nenalezeny.');
        process.exit(0);
    }

    const output = selected.map(({ league, match, tip, odds }) => ({
        league,
        match,
        tip,
        odds,
    }));

    writeFileSync('hot.json', JSON.stringify(output, null, 2), 'utf-8');

    console.log(`\n✅ Vybráno ${output.length} zápasů → hot.json\n`);
    for (const m of output) {
        console.log(`  ⚽ [${m.league}] ${m.match} | ${m.tip} @ ${m.odds}`);
    }
}

main().catch((err) => {
    console.error('Chyba:', err);
    process.exit(1);
});
