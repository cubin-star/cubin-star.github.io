/**
 * Bot pro denni vyber 6 fotbalovych zapasu s Over 2.5 goly a kurzem 2.0-3.0
 *
 * Pouziva API-Football (api-sports.io) - 7500 req/den.
 * Vystup: hot.json
 *
 * Env: API_FOOTBALL_KEY1
 * Pouziti: node fetch-matches.mjs
 */

import { writeFileSync } from 'fs';

const API_KEY = process.env.API_FOOTBALL_KEY1;
if (!API_KEY) { console.error('Chybi API_FOOTBALL_KEY1 env promenna.'); process.exit(1); }

const FOOTBALL_API = 'https://v3.football.api-sports.io';
const MIN_ODDS = 2.2;
const MAX_ODDS = 3.0;
const PICK_COUNT = 6;
const MAX_ANALYZE = 50;
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

function isBlockedLeague(name) {
    return /\b(u1[0-9]|u2[0-3]|youth|juniors?|reserves?|amateur|friendl|simulation|esports?|cyber|women|feminine|feminin|frauen|damer|kvinner|ladies|femenin|naiset|kobiety|feminino|girls)\b/i.test(name);
}

function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }
function shuffle(arr) { for (let i = arr.length - 1; i > 0; i--) { const j = Math.floor(Math.random() * (i + 1)); [arr[i], arr[j]] = [arr[j], arr[i]]; } return arr; }
function fmtDate(d) { return d.toISOString().split('T')[0]; }

async function apiFetch(baseUrl, path) {
    const url = baseUrl + path;
    try {
        const res = await fetch(url, { headers: { 'x-apisports-key': API_KEY } });
        reqCount++;
        if (!res.ok) { console.warn('  HTTP ' + res.status + ': ' + path.split('?')[0]); return { response: [], paging: { total: 0 } }; }
        const data = await res.json();
        if (data.errors && Object.keys(data.errors).length > 0) { console.warn('  ', JSON.stringify(data.errors)); return { response: [], paging: { total: 0 } }; }
        return data;
    } catch (e) { console.warn('  Fetch error:', e.message); return { response: [], paging: { total: 0 } }; }
}

async function getFootballFixtures(date) {
    const data = await apiFetch(FOOTBALL_API, '/fixtures?date=' + date + '&timezone=' + TZ + '&status=NS');
    return data.response || [];
}

async function getFootballLeagueOdds(leagueId, season, date) {
    let all = [], page = 1, totalPages = 1;
    do {
        const data = await apiFetch(FOOTBALL_API, '/odds?league=' + leagueId + '&season=' + season + '&date=' + date + '&bet=5&page=' + page);
        all.push(...(data.response || []));
        totalPages = data.paging?.total || 0;
        page++;
        if (page <= totalPages) await sleep(350);
    } while (page <= totalPages);
    return all;
}

async function getMatchPrediction(fixtureId) {
    const data = await apiFetch(FOOTBALL_API, '/predictions?fixture=' + fixtureId);
    return (data.response && data.response[0]) || null;
}

function scoreByTeamStats(pred) {
    const home = pred.teams?.home;
    const away = pred.teams?.away;
    if (!home || !away) return { total: 0, detail: 'no data' };

    const hFor5 = parseFloat(home.last_5?.goals?.for?.average) || 0;
    const hAgn5 = parseFloat(home.last_5?.goals?.against?.average) || 0;
    const aFor5 = parseFloat(away.last_5?.goals?.for?.average) || 0;
    const aAgn5 = parseFloat(away.last_5?.goals?.against?.average) || 0;

    const hForS = parseFloat(home.league?.goals?.for?.average?.total) || hFor5;
    const hAgnS = parseFloat(home.league?.goals?.against?.average?.total) || hAgn5;
    const aForS = parseFloat(away.league?.goals?.for?.average?.total) || aFor5;
    const aAgnS = parseFloat(away.league?.goals?.against?.average?.total) || aAgn5;

    const recentAttack = hFor5 + aFor5;
    const recentDefWeak = hAgn5 + aAgn5;
    const seasonAttack = hForS + aForS;
    const seasonDefWeak = hAgnS + aAgnS;

    const expectedRecent = (recentAttack + recentDefWeak) / 2;
    const expectedSeason = (seasonAttack + seasonDefWeak) / 2;
    const expectedGoals = expectedRecent * 0.6 + expectedSeason * 0.4;

    let h2hAvg = 0;
    const h2h = pred.h2h || [];
    if (h2h.length > 0) {
        const totalG = h2h.reduce((a, g) => a + (g.goals?.home || 0) + (g.goals?.away || 0), 0);
        h2hAvg = totalG / h2h.length;
    }
    const h2hBonus = h2hAvg > 2.5 ? 0.3 : (h2hAvg > 2.0 ? 0.1 : 0);

    const apiTip = pred.predictions?.under_over;
    const apiBonus = (apiTip === '+2.5' || apiTip === '+3.5') ? 0.4 : 0;

    const total = expectedGoals + h2hBonus + apiBonus;
    const detail = 'exp ' + expectedGoals.toFixed(1) + 'g, L5atk ' + recentAttack.toFixed(1) + ', H2H ' + h2hAvg.toFixed(1) + (apiTip === '+2.5' || apiTip === '+3.5' ? ', API' : '');
    return { total, detail };
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
    console.log('Kombik Bot - API-Sports\n');
    const now = new Date();
    const maxTime = new Date(now.getTime() + 24 * 60 * 60 * 1000);
    console.log('Okno: ' + now.toUTCString() + ' -> ' + maxTime.toUTCString() + '\n');

    const today = fmtDate(now), tomorrow = fmtDate(maxTime);
    const dates = [today]; if (tomorrow !== today) dates.push(tomorrow);
    let fixtures = [];
    for (const d of dates) { console.log('Fixtures ' + d + '...'); fixtures.push(...await getFootballFixtures(d)); await sleep(350); }
    console.log('   ' + fixtures.length + ' naplanovanych zapasu\n');

    fixtures = fixtures.filter(f => {
        const t = new Date(f.fixture.date);
        const c = f.league.country;
        return t >= now && t <= maxTime && !EXCLUDED_COUNTRIES.has(c) && !BLOCKED_AFRICAN.has(c) && !isBlockedLeague(f.league.name);
    });
    console.log('   ' + fixtures.length + ' v 24h okne (bez RU/BY/Afrika/zen/mladez/esport)');

    const fixtureMap = new Map(), leagueMap = new Map();
    for (const f of fixtures) {
        fixtureMap.set(f.fixture.id, f);
        const key = f.league.id + '_' + f.league.season;
        if (!leagueMap.has(key)) leagueMap.set(key, { id: f.league.id, season: f.league.season, name: f.league.name, country: f.league.country, dates: new Set() });
        leagueMap.get(key).dates.add(fmtDate(new Date(f.fixture.date)));
    }
    console.log('   ' + leagueMap.size + ' lig\n');

    const matchMap = new Map();
    for (const [, lg] of leagueMap) {
        for (const d of lg.dates) {
            const oddsData = await getFootballLeagueOdds(lg.id, lg.season, d);
            for (const entry of oddsData) {
                const fix = fixtureMap.get(entry.fixture?.id); if (!fix) continue;
                const matchKey = fix.fixture.id;
                for (const bm of entry.bookmakers || []) { for (const bet of bm.bets || []) { for (const v of bet.values || []) {
                    if (v.value !== 'Over 2.5') continue;
                    const odd = parseFloat(v.odd); if (isNaN(odd) || odd < MIN_ODDS || odd > MAX_ODDS) continue;
                    if (!matchMap.has(matchKey)) matchMap.set(matchKey, { fixtureId: matchKey, league: lg.name, match: fix.teams.home.name + ' - ' + fix.teams.away.name, tip: 'Over 2.5', allOdds: [] });
                    matchMap.get(matchKey).allOdds.push(odd);
                } } }
            }
            await sleep(350);
        }
    }
    const candidates = [...matchMap.values()];
    console.log('Kandidatu: ' + candidates.length + ' s Over 2.5 (kurz ' + MIN_ODDS + '-' + MAX_ODDS + ')');

    shuffle(candidates);
    const toAnalyze = candidates.slice(0, MAX_ANALYZE);
    console.log('Analyzuji ' + toAnalyze.length + ' zapasu (predictions)...\n');

    const picks = [];
    for (let i = 0; i < toAnalyze.length; i++) {
        const m = toAnalyze[i];
        const avg = m.allOdds.reduce((a, b) => a + b, 0) / m.allOdds.length;
        const pred = await getMatchPrediction(m.fixtureId);
        if (pred) {
            const sc = scoreByTeamStats(pred);
            picks.push({ league: m.league, match: m.match, tip: m.tip, odds: avg.toFixed(2), score: sc.total, detail: sc.detail });
        }
        if (i < toAnalyze.length - 1) await sleep(350);
    }
    shuffle(picks);

    console.log('Analyzovano: ' + picks.length + ' zapasu');
    if (picks.length > 0) { console.log('   Top 10:'); picks.slice(0, 10).forEach((p, i) => console.log('   ' + (i + 1) + '. [' + p.league + '] ' + p.match + ' | ' + p.detail + ' | score ' + p.score.toFixed(2))); }

    const selected = [], usedLeagues = new Set();
    for (const pick of picks) { if (selected.length >= PICK_COUNT) break; if (!usedLeagues.has(pick.league)) { selected.push(pick); usedLeagues.add(pick.league); } }

    // Fallback 1: pokud < 6, povol vice zapasu ze stejne ligy
    if (selected.length < PICK_COUNT) {
        console.log('   Fallback 1: povoluju vice zapasu ze stejne ligy...');
        for (const pick of picks) { if (selected.length >= PICK_COUNT) break; if (!selected.some(s => s.match === pick.match)) { selected.push(pick); } }
    }

    // Fallback 2: pokud < 6, rozsir kurzy na 1.8-3.5 a analyzuj dalsi
    if (selected.length < PICK_COUNT) {
        console.log('   Fallback 2: rozsiruji kurzy na 1.8-3.5...');
        const wideMap = new Map();
        for (const [, lg] of leagueMap) {
            for (const d of lg.dates) {
                const oddsData = await getFootballLeagueOdds(lg.id, lg.season, d);
                for (const entry of oddsData) {
                    const fix = fixtureMap.get(entry.fixture?.id); if (!fix) continue;
                    const mk = fix.fixture.id;
                    if (matchMap.has(mk)) continue;
                    for (const bm of entry.bookmakers || []) { for (const bet of bm.bets || []) { for (const v of bet.values || []) {
                        if (v.value !== 'Over 2.5') continue;
                        const odd = parseFloat(v.odd); if (isNaN(odd) || odd < 1.8 || odd > 3.5) continue;
                        if (!wideMap.has(mk)) wideMap.set(mk, { fixtureId: mk, league: lg.name, match: fix.teams.home.name + ' - ' + fix.teams.away.name, tip: 'Over 2.5', allOdds: [] });
                        wideMap.get(mk).allOdds.push(odd);
                    } } }
                }
                await sleep(350);
            }
        }
        const wideCandidates = [...wideMap.values()].slice(0, 20);
        for (let i = 0; i < wideCandidates.length && selected.length < PICK_COUNT; i++) {
            const m = wideCandidates[i];
            const avg = m.allOdds.reduce((a, b) => a + b, 0) / m.allOdds.length;
            const pred = await getMatchPrediction(m.fixtureId);
            if (pred) {
                const sc = scoreByTeamStats(pred);
                if (!selected.some(s => s.match === m.match)) {
                    selected.push({ league: m.league, match: m.match, tip: m.tip, odds: avg.toFixed(2), score: sc.total, detail: sc.detail });
                }
            }
            if (i < wideCandidates.length - 1) await sleep(350);
        }
    }

    // Fallback 3: pokud stale < 6, vezmi jakykoliv fixture s Over 2.5 kurzem
    if (selected.length < PICK_COUNT) {
        console.log('   Fallback 3: beru jakykoliv Over 2.5 zapas...');
        for (const f of fixtures) {
            if (selected.length >= PICK_COUNT) break;
            const name = f.teams.home.name + ' - ' + f.teams.away.name;
            if (selected.some(s => s.match === name)) continue;
            selected.push({ league: f.league.name, match: name, tip: 'Over 2.5', odds: '2.50', score: 0, detail: 'fallback' });
        }
    }

    if (selected.length === 0) { console.warn('\nZadne zapasy nalezeny.'); process.exit(0); }
    console.log('Vybrano: ' + selected.length + ' zapasu');

    const grouped = balanceGroups(selected);
    const output = grouped.map(m => ({ league: m.league, match: m.match, tip: m.tip, odds: m.odds, group: m.group }));
    writeFileSync('hot.json', JSON.stringify(output, null, 2), 'utf-8');

    console.log('\n' + output.length + ' zapasu -> hot.json (' + reqCount + ' API req)\n');
    const gc = Math.ceil(output.length / 2);
    for (let g = 1; g <= gc; g++) { const gm = output.filter(m => m.group === g); const go = gm.reduce((a, m) => a * parseFloat(m.odds), 1); console.log('  Sk.' + g + ' (' + go.toFixed(2) + '):'); gm.forEach(m => console.log('     [' + m.league + '] ' + m.match + ' | ' + m.tip + ' @ ' + m.odds)); }
}

main().catch(err => { console.error('Chyba:', err); process.exit(1); });
