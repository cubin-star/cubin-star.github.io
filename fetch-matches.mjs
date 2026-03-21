/**
 * fetch-matches.mjs
 *
 * Stahne fixtures z API-Football, odfiltruje blokovane zeme/ligy
 * a zapise vysledek do hot.json.
 *
 * Env: API_FOOTBALL_KEY1
 * Pouziti: node fetch-matches.mjs
 */

import { writeFileSync } from 'fs';

const API_KEY = process.env.API_FOOTBALL_KEY1;
if (!API_KEY) { console.error('Chybi API_FOOTBALL_KEY1 env promenna.'); process.exit(1); }

const FOOTBALL_API = 'https://v3.football.api-sports.io';
const MIN_ODDS = 2.0;
const MAX_ODDS = 3.0;
const PICK_COUNT = 6;
const MIN_SCORED = 1.3;
const MIN_CONCEDED_STRICT = 1.5;
const MIN_CONCEDED_RELAXED = 1.3;
const EXCLUDED_COUNTRIES = new Set(['Russia', 'Belarus']);
const BLOCKED_AFRICAN = new Set([
    'Algeria', 'Angola', 'Benin', 'Botswana', 'Burkina-Faso', 'Burundi', 'Cameroon',
    'Cape-Verde', 'Chad', 'Congo', 'Congo-DR', 'Djibouti', 'Equatorial-Guinea',
    'Eritrea', 'Eswatini', 'Ethiopia', 'Gabon', 'Gambia', 'Ghana', 'Guinea',
    'Guinea-Bissau', 'Ivory-Coast', 'Kenya', 'Lesotho', 'Liberia', 'Libya',
    'Madagascar', 'Malawi', 'Mali', 'Mauritania', 'Mauritius', 'Mozambique',
    'Namibia', 'Niger', 'Nigeria', 'Rwanda', 'Senegal', 'Seychelles',
    'Sierra-Leone', 'Somalia', 'South-Sudan', 'Sudan', 'Tanzania', 'Togo',
    'Uganda', 'Zambia', 'Zimbabwe',
]);
const TZ = 'Europe/Prague';
let reqCount = 0;

const EUROPEAN_COUNTRIES = new Set([
    'England', 'Spain', 'Germany', 'Italy', 'France', 'Netherlands', 'Portugal',
    'Belgium', 'Turkey', 'Austria', 'Switzerland', 'Scotland', 'Czech-Republic',
    'Poland', 'Denmark', 'Norway', 'Sweden', 'Greece', 'Croatia', 'Serbia',
    'Romania', 'Hungary', 'Ukraine', 'Slovakia', 'Bulgaria', 'Finland',
    'Ireland', 'Northern-Ireland', 'Wales', 'Iceland', 'Slovenia', 'Cyprus',
]);

function isSecondTier(name) {
    return /\b(2|II|segunda|championship|league two|league one|serie b|ligue 2|2\. liga|2\. bundesliga|eerste divisie|second|third|cup|pokal|coupe|copa|taca)\b/i.test(name);
}

function isBlockedLeague(name) {
    return /\b(u1[0-9]|u2[0-3]|youth|juniors?|reserves?|amateur|friendl|simulation|esports?|cyber|women|feminine|feminin|frauen|damer|kvinner|ladies|femenin|naiset|kobiety|feminino|girls)\b/i.test(name);
}

function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }
function shuffle(arr) { for (let i = arr.length - 1; i > 0; i--) { const j = Math.floor(Math.random() * (i + 1)); [arr[i], arr[j]] = [arr[j], arr[i]]; } return arr; }
function fmtDate(d) { return d.toISOString().split('T')[0]; }

async function apiFetch(path) {
    const url = FOOTBALL_API + path;
    try {
        const res = await fetch(url, { headers: { 'x-apisports-key': API_KEY } });
        reqCount++;
        if (!res.ok) { console.warn('  HTTP ' + res.status + ': ' + path.split('?')[0]); return { response: [], paging: { total: 0 } }; }
        const data = await res.json();
        if (data.errors && Object.keys(data.errors).length > 0) { console.warn('  ', JSON.stringify(data.errors)); return { response: [], paging: { total: 0 } }; }
        return data;
    } catch (e) { console.warn('  Fetch error:', e.message); return { response: [], paging: { total: 0 } }; }
}

async function getFixtures(date) {
    const data = await apiFetch('/fixtures?date=' + date + '&timezone=' + TZ + '&status=NS');
    return data.response || [];
}

async function getLeagueOdds(leagueId, season, date) {
    let all = [], page = 1, totalPages = 1;
    do {
        const data = await apiFetch('/odds?league=' + leagueId + '&season=' + season + '&date=' + date + '&bet=5&page=' + page);
        all.push(...(data.response || []));
        totalPages = data.paging?.total || 0;
        page++;
        if (page <= totalPages) await sleep(350);
    } while (page <= totalPages);
    return all;
}

async function getPrediction(fixtureId) {
    const data = await apiFetch('/predictions?fixture=' + fixtureId);
    return (data.response && data.response[0]) || null;
}

async function getTeamLastFixtures(teamId, count) {
    const data = await apiFetch('/fixtures?team=' + teamId + '&last=' + count + '&status=FT');
    return data.response || [];
}

async function getFixtureStats(fixtureId) {
    const data = await apiFetch('/fixtures/statistics?fixture=' + fixtureId);
    return data.response || [];
}

// Cache pro statistiky tymu (abychom nevolali API 2x pro stejny tym)
const teamStatsCache = new Map();

async function getTeamShootingStats(teamId) {
    if (teamStatsCache.has(teamId)) return teamStatsCache.get(teamId);

    const lastFixtures = await getTeamLastFixtures(teamId, 10);
    await sleep(350);

    let totalShots = 0, totalShotsOn = 0, totalXg = 0, games = 0;
    for (const fix of lastFixtures) {
        const stats = await getFixtureStats(fix.fixture.id);
        await sleep(350);
        // najdi statistiky pro nas tym
        const teamStats = stats.find(s => s.team?.id === teamId);
        if (!teamStats) continue;
        const vals = teamStats.statistics || [];
        const get = (type) => { const v = vals.find(s => s.type === type); return v ? parseFloat(v.value) || 0 : 0; };
        totalShots += get('Total Shots');
        totalShotsOn += get('Shots on Goal');
        totalXg += get('Expected Goals');
        games++;
    }

    const result = games > 0
        ? { shots: totalShots / games, shotsOn: totalShotsOn / games, xg: totalXg / games, games }
        : { shots: 0, shotsOn: 0, xg: 0, games: 0 };
    teamStatsCache.set(teamId, result);
    return result;
}

// Oba tymy daji 1.3+ golu/zapas && aspon jeden inkasuje minConceded+ golu/zapas
function meetsGoalCriteria(pred, minConceded) {
    const home = pred.teams?.home;
    const away = pred.teams?.away;
    if (!home || !away) return false;

    const hFor = parseFloat(home.league?.goals?.for?.average?.total) || parseFloat(home.last_5?.goals?.for?.average) || 0;
    const aFor = parseFloat(away.league?.goals?.for?.average?.total) || parseFloat(away.last_5?.goals?.for?.average) || 0;
    const hAgn = parseFloat(home.league?.goals?.against?.average?.total) || parseFloat(home.last_5?.goals?.against?.average) || 0;
    const aAgn = parseFloat(away.league?.goals?.against?.average?.total) || parseFloat(away.last_5?.goals?.against?.average) || 0;

    if (hFor < MIN_SCORED || aFor < MIN_SCORED) return false;
    if (hAgn < minConceded && aAgn < minConceded) return false;
    return true;
}

// Vazeny nahodny vyber bez opakovani; vaha = prumer golu ligy * prumer xG zapasu
// Kazdy zapas z jine ligy
function weightedPick(items, leagueStats, count) {
    const result = [];
    const usedLeagues = new Set();
    const remaining = [...items];
    for (let i = 0; i < count && remaining.length > 0; i++) {
        // odfiltruj uz pouzite ligy
        const available = remaining.filter(m => !usedLeagues.has(m.league));
        if (available.length === 0) break;
        const weights = available.map(m => {
            const lgAvg = leagueStats.has(m.league) ? leagueStats.get(m.league).total / leagueStats.get(m.league).count : 1;
            const xgAvg = ((m.homeStats?.xg || 0) + (m.awayStats?.xg || 0)) || 1;
            return lgAvg * xgAvg;
        });
        const totalW = weights.reduce((a, b) => a + b, 0);
        let r = Math.random() * totalW;
        let idx = 0;
        for (; idx < weights.length - 1; idx++) {
            r -= weights[idx];
            if (r <= 0) break;
        }
        const pick = available[idx];
        result.push(pick);
        usedLeagues.add(pick.league);
        remaining.splice(remaining.indexOf(pick), 1);
    }
    return result;
}

function balanceGroups(picks) {
    const n = picks.length;
    if (n <= 2) return picks.map(p => ({ ...p, group: 1 }));
    const indices = picks.map((_, i) => i);
    const allP = generatePairings(indices);
    let bestP = null, bestD = Infinity;
    for (const pairing of allP) {
        const gO = pairing.map(pair => pair.reduce((a, idx) => a * parseFloat(picks[idx].odds), 1));
        const diff = Math.max(...gO) - Math.min(...gO);
        if (diff < bestD) { bestD = diff; bestP = pairing; }
    }
    const result = [];
    for (let g = 0; g < bestP.length; g++) { for (const idx of bestP[g]) result.push({ ...picks[idx], group: g + 1 }); }
    return result;
}

function generatePairings(indices) {
    const results = [];
    function recurse(rem, cur) {
        if (rem.length === 0) { results.push([...cur]); return; }
        if (rem.length === 1) { results.push([...cur, [rem[0]]]); return; }
        const first = rem[0], rest = rem.slice(1);
        for (let i = 0; i < rest.length; i++) { cur.push([first, rest[i]]); recurse(rest.filter((_, j) => j !== i), cur); cur.pop(); }
    }
    recurse(indices, []);
    return results;
}

async function main() {
    console.log('Kombik Bot - fetch-matches\n');
    const now = new Date();
    const maxTime = new Date(now.getTime() + 24 * 60 * 60 * 1000);
    console.log('Okno: ' + now.toUTCString() + ' -> ' + maxTime.toUTCString() + '\n');

    const today = fmtDate(now), tomorrow = fmtDate(maxTime);
    const dates = [today]; if (tomorrow !== today) dates.push(tomorrow);
    let fixtures = [];
    for (const d of dates) { console.log('Fixtures ' + d + '...'); fixtures.push(...await getFixtures(d)); await sleep(350); }
    console.log('   ' + fixtures.length + ' naplanovanych zapasu\n');

    fixtures = fixtures.filter(f => {
        const t = new Date(f.fixture.date);
        const c = f.league.country;
        return t >= now && t <= maxTime
            && !EXCLUDED_COUNTRIES.has(c)
            && !BLOCKED_AFRICAN.has(c)
            && !isBlockedLeague(f.league.name);
    });
    console.log('   ' + fixtures.length + ' v 24h okne (bez RU/BY/Afrika/zen/mladez/esport)');

    // Mapovani fixtures a lig
    const fixtureMap = new Map(), leagueMap = new Map();
    for (const f of fixtures) {
        fixtureMap.set(f.fixture.id, f);
        const key = f.league.id + '_' + f.league.season;
        if (!leagueMap.has(key)) leagueMap.set(key, { id: f.league.id, season: f.league.season, name: f.league.name, country: f.league.country, dates: new Set() });
        leagueMap.get(key).dates.add(fmtDate(new Date(f.fixture.date)));
    }
    console.log('   ' + leagueMap.size + ' lig\n');

    // Stazeni kurzu Over 2.5 pro vsechny ligy
    const candidates = new Map();
    for (const [, lg] of leagueMap) {
        for (const d of lg.dates) {
            const oddsData = await getLeagueOdds(lg.id, lg.season, d);
            for (const entry of oddsData) {
                const fix = fixtureMap.get(entry.fixture?.id); if (!fix) continue;
                const matchKey = fix.fixture.id;
                for (const bm of entry.bookmakers || []) { for (const bet of bm.bets || []) { for (const v of bet.values || []) {
                    if (v.value !== 'Over 2.5') continue;
                    const odd = parseFloat(v.odd); if (isNaN(odd) || odd < MIN_ODDS || odd > MAX_ODDS) continue;
                    if (!candidates.has(matchKey)) candidates.set(matchKey, { fixtureId: matchKey, league: lg.name, country: lg.country, match: fix.teams.home.name + ' - ' + fix.teams.away.name, tip: 'Over 2.5', allOdds: [] });
                    candidates.get(matchKey).allOdds.push(odd);
                } } }
            }
            await sleep(350);
        }
    }
    const pool = [...candidates.values()].map(m => ({
        ...m,
        odds: (m.allOdds.reduce((a, b) => a + b, 0) / m.allOdds.length).toFixed(2),
    }));
    console.log('Kandidatu: ' + pool.length + ' s Over 2.5 (kurz ' + MIN_ODDS + '-' + MAX_ODDS + ')');

    // Filtrace pres predictions: 1. kolo >= 1.5 obdrzenych, 2. kolo >= 1.3
    shuffle(pool);
    console.log('Analyza tymu (predictions)...\n');
    const qualified15 = [];
    const qualified13 = [];
    for (const m of pool) {
        const pred = await getPrediction(m.fixtureId);
        if (pred) {
            const home = pred.teams?.home;
            const away = pred.teams?.away;
            if (home && away) {
                const hFor = parseFloat(home.league?.goals?.for?.average?.total) || parseFloat(home.last_5?.goals?.for?.average) || 0;
                const aFor = parseFloat(away.league?.goals?.for?.average?.total) || parseFloat(away.last_5?.goals?.for?.average) || 0;
                if (hFor >= MIN_SCORED && aFor >= MIN_SCORED) {
                    const hAgn = parseFloat(home.league?.goals?.against?.average?.total) || parseFloat(home.last_5?.goals?.against?.average) || 0;
                    const aAgn = parseFloat(away.league?.goals?.against?.average?.total) || parseFloat(away.last_5?.goals?.against?.average) || 0;
                    const expG = (hFor + aFor + hAgn + aAgn) / 2;
                    const entry = { ...m, expectedGoals: expG };
                    if (hAgn >= MIN_CONCEDED_STRICT || aAgn >= MIN_CONCEDED_STRICT) {
                        console.log('   [1.5] ' + m.match + ' | scored ' + hFor.toFixed(1) + '/' + aFor.toFixed(1) + ', conceded ' + hAgn.toFixed(1) + '/' + aAgn.toFixed(1) + ' => ' + expG.toFixed(2) + 'g');
                        qualified15.push(entry);
                    } else if (hAgn >= MIN_CONCEDED_RELAXED || aAgn >= MIN_CONCEDED_RELAXED) {
                        console.log('   [1.3] ' + m.match + ' | scored ' + hFor.toFixed(1) + '/' + aFor.toFixed(1) + ', conceded ' + hAgn.toFixed(1) + '/' + aAgn.toFixed(1) + ' => ' + expG.toFixed(2) + 'g');
                        qualified13.push(entry);
                    }
                }
            }
        }
        await sleep(350);
    }
    console.log('\n1. kolo (>= 1.5 obdrzenych): ' + qualified15.length + '/' + pool.length);
    console.log('2. kolo (>= 1.3 obdrzenych): ' + qualified13.length + '/' + pool.length);

    // Stazeni xG a strel z poslednich 10 zapasu pro kazdy kvalifikovany tym
    const allQualified = [...qualified15, ...qualified13];
    if (allQualified.length > 0) {
        console.log('\nStrely a xG (posledni zapasy)...\n');

        for (const m of allQualified) {
            const fix = fixtureMap.get(m.fixtureId);
            if (!fix) continue;
            const homeId = fix.teams.home.id;
            const awayId = fix.teams.away.id;
            const hs = await getTeamShootingStats(homeId);
            const as = await getTeamShootingStats(awayId);
            m.homeStats = hs;
            m.awayStats = as;
            console.log('   ' + m.match);
            console.log('      ' + fix.teams.home.name + ': ' + hs.shots.toFixed(1) + ' strel, ' + hs.shotsOn.toFixed(1) + ' na branu, xG ' + hs.xg.toFixed(2) + ' (' + hs.games + ' zapasu)');
            console.log('      ' + fix.teams.away.name + ': ' + as.shots.toFixed(1) + ' strel, ' + as.shotsOn.toFixed(1) + ' na branu, xG ' + as.xg.toFixed(2) + ' (' + as.games + ' zapasu)');
        }
    }

    // Statistiky lig - prumerne goly na zapas
    const leagueStats = new Map();
    for (const m of allQualified) {
        if (!leagueStats.has(m.league)) leagueStats.set(m.league, { total: 0, count: 0 });
        const s = leagueStats.get(m.league);
        s.total += m.expectedGoals;
        s.count++;
    }
    if (leagueStats.size > 0) {
        const leagueRanking = [...leagueStats.entries()]
            .map(([name, s]) => ({ name, avg: s.total / s.count, count: s.count }))
            .sort((a, b) => b.avg - a.avg);
        console.log('\nLigy podle prumeru golu:');
        leagueRanking.forEach(l => console.log('   ' + l.avg.toFixed(2) + ' g/z  ' + l.name + ' (' + l.count + ' zapasu)'));
    }

    // 1. kolo vyberu: z qualified15 (obdrzene >= 1.5)
    const selected = [];
    console.log('\n--- 1. kolo vyberu (obdrzene >= 1.5) ---');
    const picked15 = weightedPick(qualified15, leagueStats, PICK_COUNT);
    for (const m of picked15) { m._qualified15 = true; selected.push(m); }
    console.log('Vybrano z 1. kola: ' + selected.length + ' zapasu');

    // 2. kolo vyberu: pokud < 6, doplnit z qualified13 (obdrzene >= 1.3)
    if (selected.length < PICK_COUNT && qualified13.length > 0) {
        console.log('\n--- 2. kolo vyberu (obdrzene >= 1.3) ---');
        const usedLeagues2 = new Set(selected.map(s => s.league));
        const available13 = qualified13.filter(m => !usedLeagues2.has(m.league));
        const picked13 = weightedPick(available13, leagueStats, PICK_COUNT - selected.length);
        for (const m of picked13) { m._qualified13 = true; selected.push(m); }
        console.log('Doplneno z 2. kola: ' + picked13.length + ', celkem: ' + selected.length);
    }

    // Fallback: pokud neni 6, doplnit z evropskych prvnich lig, pak z poolu
    if (selected.length < PICK_COUNT) {
        const usedIds = new Set(selected.map(s => s.fixtureId));
        const usedLeagues = new Set(selected.map(s => s.league));
        const remaining = pool.filter(m => !usedIds.has(m.fixtureId) && !usedLeagues.has(m.league));

        // 1) preferuj evropske prvni ligy
        const euroTop = remaining.filter(m => EUROPEAN_COUNTRIES.has(m.country) && !isSecondTier(m.league));
        shuffle(euroTop);
        for (const m of euroTop) {
            if (selected.length >= PICK_COUNT) break;
            if (usedLeagues.has(m.league)) continue;
            selected.push(m);
            usedLeagues.add(m.league);
            usedIds.add(m.fixtureId);
        }

        // 2) pokud stale < 6, jakykoli zbyvajici z poolu (unikatni liga)
        if (selected.length < PICK_COUNT) {
            const rest = remaining.filter(m => !usedIds.has(m.fixtureId) && !usedLeagues.has(m.league));
            shuffle(rest);
            for (const m of rest) {
                if (selected.length >= PICK_COUNT) break;
                if (usedLeagues.has(m.league)) continue;
                selected.push(m);
                usedLeagues.add(m.league);
            }
        }

        console.log('Fallback: doplneno na ' + selected.length + ' (evropske 1. ligy, pak pool, unikatni ligy)');
    }

    console.log('\nVybrano: ' + selected.length + ' zapasu\n');

    if (selected.length === 0) { console.warn('Zadne zapasy nenalezeny.'); process.exit(0); }

    // Rozdeleni do 3 kurzove vyrovnanych skupin po 2
    const grouped = balanceGroups(selected);
    const output = grouped.map(m => ({ league: m.league, match: m.match, tip: m.tip, odds: m.odds, group: m.group, qualified15: !!m._qualified15, qualified13: !!m._qualified13 }));
    writeFileSync('hot.json', JSON.stringify(output, null, 2), 'utf-8');

    console.log(output.length + ' zapasu -> hot.json (' + reqCount + ' API req)\n');
    const gc = Math.ceil(output.length / 2);
    for (let g = 1; g <= gc; g++) {
        const gm = output.filter(m => m.group === g);
        const go = gm.reduce((a, m) => a * parseFloat(m.odds), 1);
        console.log('  Sk.' + g + ' (' + go.toFixed(2) + '):');
        gm.forEach(m => console.log('     [' + m.league + '] ' + m.match + ' | ' + m.tip + ' @ ' + m.odds));
    }
}

main().catch(err => { console.error('Chyba:', err); process.exit(1); });
