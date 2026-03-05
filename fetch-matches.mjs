/**
 * Bot pro denní výběr 6 fotbalových zápasů s Over 2.5 góly a kurzem >= 2.0
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
    // Anglie
    'soccer_epl',
    'soccer_efl_champ',
    'soccer_england_league1',
    'soccer_england_league2',
    'soccer_fa_cup',
    'soccer_england_efl_cup',
    // Německo
    'soccer_germany_bundesliga',
    'soccer_germany_bundesliga2',
    'soccer_germany_liga3',
    'soccer_germany_dfb_pokal',
    // Španělsko
    'soccer_spain_la_liga',
    'soccer_spain_segunda_division',
    'soccer_spain_copa_del_rey',
    // Itálie
    'soccer_italy_serie_a',
    'soccer_italy_serie_b',
    'soccer_italy_coppa_italia',
    // Francie
    'soccer_france_ligue_one',
    'soccer_france_ligue_two',
    'soccer_france_coupe_de_france',
    // Portugalsko, Nizozemsko, Turecko
    'soccer_portugal_primeira_liga',
    'soccer_netherlands_eredivisie',
    'soccer_turkey_super_league',
    // Skandinávie
    'soccer_norway_eliteserien',
    'soccer_sweden_allsvenskan',
    'soccer_sweden_superettan',
    'soccer_denmark_superliga',
    'soccer_finland_veikkausliiga',
    // Střední / Východní Evropa
    'soccer_poland_ekstraklasa',
    'soccer_austria_bundesliga',
    'soccer_switzerland_superleague',
    'soccer_greece_super_league',
    'soccer_belgium_first_div',
    'soccer_czech_football_league',
    'soccer_romania_liga_1',
    'soccer_serbia_super_league',
    'soccer_croatia_1_hnl',
    'soccer_bulgaria_first_league',
    'soccer_russia_premier_league',
    // UEFA
    'soccer_uefa_champs_league',
    'soccer_uefa_europa_league',
    'soccer_uefa_europa_conference_league',
    // Jižní Amerika
    'soccer_brazil_campeonato',
    'soccer_brazil_serie_b',
    'soccer_argentina_primera_division',
    'soccer_colombia_primera_a',
    'soccer_chile_campeonato',
    'soccer_conmebol_copa_libertadores',
    'soccer_conmebol_copa_sudamericana',
    // Severní Amerika
    'soccer_usa_mls',
    'soccer_mexico_ligamx',
    // Asie / Oceánie
    'soccer_japan_j_league',
    'soccer_korea_kleague1',
    'soccer_china_superleague',
    'soccer_australia_aleague',
    'soccer_uzbekistan_super_league',
    'soccer_india_superleague',
    'soccer_saudi_professional_league',
    // Hokej
    'icehockey_nhl',
    'icehockey_sweden_hockey_league',
    'icehockey_finland_liiga',
    'icehockey_czech_extraliga',
    'icehockey_switzerland_nlb',
    'icehockey_germany_del',
];

const LEAGUE_NAMES = {
    // Anglie
    soccer_epl: 'Premier League',
    soccer_efl_champ: '2. England',
    soccer_england_league1: '3. England',
    soccer_england_league2: '4. England',
    soccer_fa_cup: 'FA Cup',
    soccer_england_efl_cup: 'EFL Cup',
    // Německo
    soccer_germany_bundesliga: 'Bundesliga',
    soccer_germany_bundesliga2: '2. Bundesliga',
    soccer_germany_liga3: '3. Liga',
    soccer_germany_dfb_pokal: 'DFB Pokal',
    // Španělsko
    soccer_spain_la_liga: 'La Liga',
    soccer_spain_segunda_division: 'La Liga 2',
    soccer_spain_copa_del_rey: 'Copa del Rey',
    // Itálie
    soccer_italy_serie_a: 'Serie A',
    soccer_italy_serie_b: 'Serie B',
    soccer_italy_coppa_italia: 'Coppa Italia',
    // Francie
    soccer_france_ligue_one: 'Ligue 1',
    soccer_france_ligue_two: 'Ligue 2',
    soccer_france_coupe_de_france: 'Coupe de France',
    // Portugalsko, Nizozemsko, Turecko
    soccer_portugal_primeira_liga: 'Primeira Liga',
    soccer_netherlands_eredivisie: 'Eredivisie',
    soccer_turkey_super_league: 'Turkey Süper Lig',
    // Skandinávie
    soccer_norway_eliteserien: 'Eliteserien',
    soccer_sweden_allsvenskan: 'Allsvenskan',
    soccer_sweden_superettan: 'Superettan',
    soccer_denmark_superliga: 'Superliga DK',
    soccer_finland_veikkausliiga: 'Veikkausliiga',
    // Střední / Východní Evropa
    soccer_poland_ekstraklasa: 'Ekstraklasa',
    soccer_austria_bundesliga: 'Austria BL',
    soccer_switzerland_superleague: 'Swiss Super League',
    soccer_greece_super_league: 'Super League GR',
    soccer_belgium_first_div: 'Jupiler Pro',
    soccer_czech_football_league: 'Fortuna liga CZ',
    soccer_romania_liga_1: 'Liga 1 RO',
    soccer_serbia_super_league: 'SuperLiga RS',
    soccer_croatia_1_hnl: '1. HNL',
    soccer_bulgaria_first_league: 'First League BG',
    soccer_russia_premier_league: 'RPL',
    // UEFA
    soccer_uefa_champs_league: 'Champions League',
    soccer_uefa_europa_league: 'Europa League',
    soccer_uefa_europa_conference_league: 'Conference League',
    // Jižní Amerika
    soccer_brazil_campeonato: 'Brazil Serie A',
    soccer_brazil_serie_b: 'Brazil Serie B',
    soccer_argentina_primera_division: 'Argentina Liga',
    soccer_colombia_primera_a: 'Colombia Liga',
    soccer_chile_campeonato: 'Chile Primera',
    soccer_conmebol_copa_libertadores: 'Copa Libertadores',
    soccer_conmebol_copa_sudamericana: 'Copa Sudamericana',
    // Severní Amerika
    soccer_usa_mls: 'MLS',
    soccer_mexico_ligamx: 'Liga MX',
    // Asie / Oceánie
    soccer_japan_j_league: 'J. League',
    soccer_korea_kleague1: 'K League',
    soccer_china_superleague: 'China Super League',
    soccer_australia_aleague: 'A-League',
    soccer_uzbekistan_super_league: 'Uzbek League',
    soccer_india_superleague: 'Indian Super League',
    soccer_saudi_professional_league: 'Saudi Pro League',
    // Hokej
    icehockey_nhl: 'NHL',
    icehockey_sweden_hockey_league: 'SHL',
    icehockey_finland_liiga: 'Liiga FI',
    icehockey_czech_extraliga: 'Extraliga CZ',
    icehockey_switzerland_nlb: 'NL Hockey CH',
    icehockey_germany_del: 'DEL',
};

function sleep(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
}

async function getActiveSports() {
    const url = `https://api.the-odds-api.com/v4/sports/?apiKey=${API_KEY}`;
    const res = await fetch(url);
    if (!res.ok) {
        console.warn(`⚠ Nepodařilo se načíst seznam sportů: HTTP ${res.status}`);
        return SOCCER_SPORTS;
    }
    const sports = await res.json();
    const active = sports
        .filter(s => (s.group === 'Soccer' || s.group === 'Ice Hockey') && s.active && !s.has_outrights)
        .map(s => s.key);
    const soccer = active.filter(k => k.startsWith('soccer'));
    const hockey = active.filter(k => k.startsWith('icehockey'));
    console.log(`📋 Aktivní: ${soccer.length} fotbal + ${hockey.length} hokej = ${active.length} celkem\n`);
    return active;
}

async function fetchOdds(sport) {
    const url = new URL(`https://api.the-odds-api.com/v4/sports/${sport}/odds/`);
    url.searchParams.set('apiKey', API_KEY);
    url.searchParams.set('regions', 'eu');
    url.searchParams.set('markets', 'totals');
    url.searchParams.set('oddsFormat', 'decimal');

    const res = await fetch(url);

    const remaining = res.headers.get('x-requests-remaining');
    if (remaining) console.log(`   (zbývá ${remaining} API req)`);

    if (res.status === 429) {
        console.warn(`  ⚠ Rate limit – čekám 5s…`);
        await sleep(5000);
        const retry = await fetch(url);
        if (!retry.ok) return [];
        return retry.json();
    }
    if (res.status === 422 || !res.ok) {
        return [];
    }
    return res.json();
}

function getOverLine(sportKey) {
    return sportKey.startsWith('icehockey') ? 5.5 : 2.5;
}

function getLeagueName(sportKey) {
    if (LEAGUE_NAMES[sportKey]) return LEAGUE_NAMES[sportKey];
    return sportKey
        .replace('soccer_', '')
        .replace('icehockey_', '')
        .replace(/_/g, ' ');
}

function extractOverPicks(events, sportKey, now, maxTime) {
    const picks = [];
    const overLine = getOverLine(sportKey);

    for (const event of events) {
        const kickoff = new Date(event.commence_time);
        if (kickoff < now || kickoff > maxTime) continue;

        for (const bookmaker of event.bookmakers) {
            for (const market of bookmaker.markets) {
                if (market.key !== 'totals') continue;

                for (const outcome of market.outcomes) {
                    if (outcome.name !== 'Over') continue;
                    if (outcome.point !== overLine) continue;
                    if (outcome.price < MIN_ODDS) continue;

                    picks.push({
                        league: getLeagueName(sportKey),
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

    const now = new Date();
    const in24h = new Date(now.getTime() + 24 * 60 * 60 * 1000);
    console.log(`⏰ Okno: ${now.toUTCString()} → ${in24h.toUTCString()}\n`);

    const activeSports = await getActiveSports();

    let allPicks = [];
    let queried = 0;

    for (let i = 0; i < activeSports.length; i++) {
        const sport = activeSports[i];
        const name = LEAGUE_NAMES[sport] || sport.replace('soccer_', '').replace(/_/g, ' ');
        console.log(`📡 ${name}…`);

        const events = await fetchOdds(sport);
        queried++;
        if (events.length > 0) {
            const overLine = getOverLine(sport);
            const picks = extractOverPicks(events, sport, now, in24h);
            if (picks.length > 0) {
                console.log(`   → ${picks.length} tipů Over ${overLine} >= ${MIN_ODDS}`);
            } else {
                console.log(`   → ${events.length} zápasů, ale žádný Over ${overLine} >= ${MIN_ODDS}`);
            }
            allPicks.push(...picks);
        }

        if (i < activeSports.length - 1) await sleep(1200);
    }

    console.log(`\n📊 Dotazováno ${queried} lig, nalezeno ${allPicks.length} tipů`);

    const best = new Map();
    for (const p of allPicks) {
        const key = p.match;
        if (!best.has(key) || parseFloat(p.odds) > parseFloat(best.get(key).odds)) {
            best.set(key, p);
        }
    }

    let unique = [...best.values()];
    unique.sort((a, b) => parseFloat(b.odds) - parseFloat(a.odds));

    console.log(`📊 Unikátní zápasy: ${unique.length}`);

    const selected = [];
    const usedLeagues = new Set();

    for (const pick of unique) {
        if (selected.length >= PICK_COUNT) break;
        if (!usedLeagues.has(pick.league)) {
            selected.push(pick);
            usedLeagues.add(pick.league);
        }
    }

    console.log(`📊 Vybráno ${selected.length} zápasů (každý z jiné soutěže)`);

    if (selected.length === 0) {
        console.warn('\n⚠ Žádné zápasy s Over a kurzem >= 2.0 v příštích 24h.');
        console.warn('   Tip: V některé dny (pondělí, úterý) se hraje méně zápasů.');
        process.exit(0);
    }

    // Rozdělit do skupin po 2 s co nejrovnoměrnějšími celkovými kurzy
    const grouped = balanceGroups(selected);

    const output = grouped.map(m => ({
        league: m.league,
        match: m.match,
        tip: m.tip,
        odds: m.odds,
        group: m.group,
    }));

    writeFileSync('hot.json', JSON.stringify(output, null, 2), 'utf-8');

    console.log(`\n✅ Vybráno ${output.length} zápasů → hot.json\n`);
    const groupCount = Math.ceil(output.length / 2);
    for (let g = 1; g <= groupCount; g++) {
        const gm = output.filter(m => m.group === g);
        const gOdds = gm.reduce((a, m) => a * parseFloat(m.odds), 1);
        console.log(`  📦 Skupina ${g} (kurz ${gOdds.toFixed(2)}):`);
        for (const m of gm) {
            console.log(`     ⚽ [${m.league}] ${m.match} | ${m.tip} @ ${m.odds}`);
        }
    }
}

/**
 * Rozřadí zápasy do skupin po 2 tak, aby součiny kurzů skupin byly co nejbližší.
 * Zkouší všechny možné kombinace párů a vybere tu s nejmenším rozdílem.
 */
function balanceGroups(picks) {
    const n = picks.length;
    const groupCount = Math.ceil(n / 2);

    if (n <= 2) {
        return picks.map((p, i) => ({ ...p, group: 1 }));
    }

    // Pro 6 zápasů – vyzkoušej všechny možné rozdělení do 3 párů
    // a vyber to, kde jsou součiny kurzů skupin co nejblíž
    const indices = picks.map((_, i) => i);
    const allPairings = generatePairings(indices);

    let bestPairing = null;
    let bestDiff = Infinity;

    for (const pairing of allPairings) {
        const groupOdds = pairing.map(pair =>
            pair.reduce((a, idx) => a * parseFloat(picks[idx].odds), 1)
        );
        const max = Math.max(...groupOdds);
        const min = Math.min(...groupOdds);
        const diff = max - min;

        if (diff < bestDiff) {
            bestDiff = diff;
            bestPairing = pairing;
        }
    }

    const result = [];
    for (let g = 0; g < bestPairing.length; g++) {
        for (const idx of bestPairing[g]) {
            result.push({ ...picks[idx], group: g + 1 });
        }
    }

    return result;
}

/**
 * Generuje všechny způsoby jak rozdělit pole indexů na páry.
 * Pro [0,1,2,3,4,5] vrátí všechny kombinace 3 párů (15 kombinací).
 */
function generatePairings(indices) {
    const results = [];

    function recurse(remaining, current) {
        if (remaining.length === 0) {
            results.push([...current]);
            return;
        }
        if (remaining.length === 1) {
            results.push([...current, [remaining[0]]]);
            return;
        }
        const first = remaining[0];
        const rest = remaining.slice(1);
        for (let i = 0; i < rest.length; i++) {
            const pair = [first, rest[i]];
            const nextRemaining = rest.filter((_, j) => j !== i);
            current.push(pair);
            recurse(nextRemaining, current);
            current.pop();
        }
    }

    recurse(indices, []);
    return results;
}

main().catch((err) => {
    console.error('Chyba:', err);
    process.exit(1);
});
