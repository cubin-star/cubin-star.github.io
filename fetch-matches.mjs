/**
 * fetch-matches.mjs
 *
 * Stahne fixtures z API-Football, odfiltruje blokovane zeme
 * a zapise vysledek do hot.json.
 *
 * Env: API_FOOTBALL_KEY1
 * Pouziti: node fetch-matches.mjs
 */

import { writeFileSync } from 'fs';

const API_KEY = process.env.API_FOOTBALL_KEY1;
if (!API_KEY) { console.error('Chybi API_FOOTBALL_KEY1 env promenna.'); process.exit(1); }

const FOOTBALL_API = 'https://v3.football.api-sports.io';
const MIN_ODDS = 1.75;
const MAX_ODDS = 3.0;
const PICK_COUNT = 6;
const MIN_PLAYED = 5;

// League-relative criteria (ratios of game baseline)
// Baseline = avg(h_for, a_for, h_agn, a_agn) → auto-scales to any league
const BOTH_FLOOR_R = 0.85;      // oba alespoň 85% baseline
const STRONG_MIN_R = 1.10;      // "výrazný" tým 110%+ baseline
const CONTRAST_MAX_R = 0.95;    // protějšek pod 95% baseline (kontrast ≥ 15%)
const MIN_BASELINE = 1.25;      // minimum avg per-team stat → expected ~2.5+ gólů celkem
const EXCLUDED_COUNTRIES = new Set(['Russia', 'Belarus']);
const TZ = 'Europe/Prague';
let reqCount = 0;

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

function balanceGroups(picks) {
    const n = picks.length;
    if (n <= 2) return picks.map(p => ({ ...p, group: 1 }));
    const indices = picks.map((_, i) => i);
    const allP = generatePairings(indices);

    function hasLeagueConflict(pairing) {
        return pairing.some(pair => {
            const leagues = pair.map(idx => picks[idx].league);
            return new Set(leagues).size < leagues.length;
        });
    }

    let bestP = null, bestD = Infinity;
    for (const pairing of allP) {
        if (hasLeagueConflict(pairing)) continue;
        const gO = pairing.map(pair => pair.reduce((a, idx) => a * parseFloat(picks[idx].odds), 1));
        const diff = Math.max(...gO) - Math.min(...gO);
        if (diff < bestD) { bestD = diff; bestP = pairing; }
    }
    if (!bestP) {
        for (const pairing of allP) {
            const gO = pairing.map(pair => pair.reduce((a, idx) => a * parseFloat(picks[idx].odds), 1));
            const diff = Math.max(...gO) - Math.min(...gO);
            if (diff < bestD) { bestD = diff; bestP = pairing; }
        }
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
    const max24h = new Date(now.getTime() + 24 * 60 * 60 * 1000);
    const max48h = new Date(now.getTime() + 48 * 60 * 60 * 1000);
    console.log('Window: ' + now.toUTCString() + ' -> ' + max48h.toUTCString() + ' (48h)\n');

    // Collect dates covering the 48h window
    const dates = new Set();
    for (let d = new Date(now); d <= max48h; d = new Date(d.getTime() + 24 * 60 * 60 * 1000)) {
        dates.add(fmtDate(d));
    }
    let fixtures = [];
    for (const d of dates) { console.log('Fixtures ' + d + '...'); fixtures.push(...await getFixtures(d)); await sleep(350); }
    console.log('   ' + fixtures.length + ' scheduled matches\n');

    // Filter: ban only Russia and Belarus
    fixtures = fixtures.filter(f => {
        const t = new Date(f.fixture.date);
        const c = f.league.country;
        return t >= now && t <= max48h
            && !EXCLUDED_COUNTRIES.has(c);
    });
    console.log('   ' + fixtures.length + ' in 48h window (excl. RU/BY)');

    // Map fixtures and leagues
    const fixtureMap = new Map(), leagueMap = new Map();
    for (const f of fixtures) {
        fixtureMap.set(f.fixture.id, f);
        const key = f.league.id + '_' + f.league.season;
        if (!leagueMap.has(key)) leagueMap.set(key, { id: f.league.id, season: f.league.season, name: f.league.name, country: f.league.country, dates: new Set() });
        leagueMap.get(key).dates.add(fmtDate(new Date(f.fixture.date)));
    }
    console.log('   ' + leagueMap.size + ' leagues\n');

    // Download Over 2.5 odds for all leagues
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
                    if (!candidates.has(matchKey)) candidates.set(matchKey, { fixtureId: matchKey, league: lg.name, country: lg.country, match: fix.teams.home.name + ' - ' + fix.teams.away.name, kickoff: fix.fixture.date, tip: 'Over 2.5', allOdds: [] });
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
    console.log('Candidates: ' + pool.length + ' with Over 2.5 (odds ' + MIN_ODDS + '-' + MAX_ODDS + ')');

    // Filter via predictions (strictest criteria only)
    shuffle(pool);
    console.log('Analyzing teams (predictions)...\n');
    const qualified = [];
    for (const m of pool) {
        const pred = await getPrediction(m.fixtureId);
        if (pred) {
            const home = pred.teams?.home;
            const away = pred.teams?.away;
            if (home && away) {
                const hPlayed = parseInt(home.league?.fixtures?.played?.total) || 0;
                const aPlayed = parseInt(away.league?.fixtures?.played?.total) || 0;
                if (hPlayed < MIN_PLAYED || aPlayed < MIN_PLAYED) {
                    console.log('   [SKIP] ' + m.match + ' | too few games: ' + hPlayed + '/' + aPlayed);
                    continue;
                }
                // Home/away split: domácí → home stats, hosté → away stats
                const hFor = parseFloat(home.league?.goals?.for?.average?.home) || 0;
                const aFor = parseFloat(away.league?.goals?.for?.average?.away) || 0;
                const hAgn = parseFloat(home.league?.goals?.against?.average?.home) || 0;
                const aAgn = parseFloat(away.league?.goals?.against?.average?.away) || 0;

                if (hFor === 0 && aFor === 0) continue;

                // League-relative baseline
                const baseline = (hFor + aFor + hAgn + aAgn) / 4;
                if (baseline === 0) continue;
                if (baseline < MIN_BASELINE) {
                    console.log('   [LOW] ' + m.match + ' | baseline ' + baseline.toFixed(2) + ' < ' + MIN_BASELINE);
                    continue;
                }

                const bothFloor = baseline * BOTH_FLOOR_R;
                const strongMin = baseline * STRONG_MIN_R;
                const contrastMax = baseline * CONTRAST_MAX_R;

                // A) oba inkasují >= floor + ofenzivní kontrast
                const variantA = (hAgn >= bothFloor && aAgn >= bothFloor)
                    && ((hFor >= strongMin && aFor < contrastMax) || (aFor >= strongMin && hFor < contrastMax));

                // B) oba střílí >= floor + defenzivní kontrast
                const variantB = (hFor >= bothFloor && aFor >= bothFloor)
                    && ((hAgn >= strongMin && aAgn < contrastMax) || (aAgn >= strongMin && hAgn < contrastMax));

                if (variantA || variantB) {
                    const tag = variantA ? 'A' : 'B';
                    const s = variantA ? [hFor, aFor].sort((a, b) => a - b) : [hAgn, aAgn].sort((a, b) => a - b);
                    const score = s[0] > 0 ? s[1] / s[0] : 99;
                    console.log('   [Q' + tag + '] ' + m.match + ' | scored ' + hFor.toFixed(1) + '/' + aFor.toFixed(1) + ', conceded ' + hAgn.toFixed(1) + '/' + aAgn.toFixed(1) + ' (base=' + baseline.toFixed(2) + ', score=' + score.toFixed(2) + ')');
                    qualified.push({ ...m, contrastScore: score });
                }
            }
        }
        await sleep(350);
    }
    console.log('\nQualified (strict): ' + qualified.length + '/' + pool.length);

    // Best per league: keep only the match with highest contrastScore from each league
    const bestByLeague = new Map();
    for (const m of qualified) {
        const prev = bestByLeague.get(m.league);
        if (!prev || m.contrastScore > prev.contrastScore) bestByLeague.set(m.league, m);
    }
    const deduped = [...bestByLeague.values()];
    if (deduped.length < qualified.length) console.log('Dedup: ' + qualified.length + ' → ' + deduped.length + ' (best per league)');

    const fullAccumulator = deduped.length >= PICK_COUNT;
    if (!fullAccumulator) {
        console.log('\nWARNING: Only ' + deduped.length + ' qualifying matches found (need ' + PICK_COUNT + ' for full accumulator).');
    }

    // Prefer matches within 24h, pick by highest contrastScore
    // Same league is allowed, but cap per league = number of groups so each lands in a different group
    const targetCount = fullAccumulator ? PICK_COUNT : deduped.length;
    const numGroups = Math.ceil(targetCount / 2);
    const within24h = deduped.filter(m => new Date(m.kickoff) <= max24h);
    const beyond24h = deduped.filter(m => new Date(m.kickoff) > max24h);
    console.log('Within 24h: ' + within24h.length + ', beyond 24h: ' + beyond24h.length);

    let selected;
    if (within24h.length >= targetCount) {
        // Pick by highest contrastScore from 24h pool (max numGroups per league)
        within24h.sort((a, b) => b.contrastScore - a.contrastScore);
        selected = [];
        const lc = new Map();
        for (const m of within24h) {
            if (selected.length >= targetCount) break;
            const cnt = lc.get(m.league) || 0;
            if (cnt >= numGroups) continue;
            selected.push(m);
            lc.set(m.league, cnt + 1);
        }
        console.log('Selected ' + selected.length + ' matches with highest contrastScore from 24h window');
    } else {
        // Take all from 24h + fill from beyond 24h (by contrastScore desc)
        selected = [];
        const lc = new Map();
        within24h.sort((a, b) => b.contrastScore - a.contrastScore);
        for (const m of within24h) {
            const cnt = lc.get(m.league) || 0;
            if (cnt >= numGroups) continue;
            selected.push(m);
            lc.set(m.league, cnt + 1);
        }
        beyond24h.sort((a, b) => b.contrastScore - a.contrastScore);
        for (const m of beyond24h) {
            if (selected.length >= targetCount) break;
            const cnt = lc.get(m.league) || 0;
            if (cnt >= numGroups) continue;
            selected.push(m);
            lc.set(m.league, cnt + 1);
        }
        console.log('Selected ' + selected.length + ' matches (' + within24h.length + ' candidates from 24h window)');
    }

    console.log('\nSelected: ' + selected.length + ' matches\n');

    if (selected.length === 0) {
        writeFileSync('hot.json', JSON.stringify([], null, 2), 'utf-8');
        console.log('No qualifying matches found in the 48h window.');
        console.log('Written empty array to hot.json (' + reqCount + ' API req)');
        process.exit(0);
    }

    // Balance into groups and write output (plain array)
    const grouped = balanceGroups(selected);
    const output = grouped.map(m => ({ league: m.league, match: m.match, kickoff: m.kickoff, tip: m.tip, odds: m.odds, group: m.group }));
    writeFileSync('hot.json', JSON.stringify(output, null, 2), 'utf-8');

    if (!fullAccumulator) console.log('NOTE: Today\'s accumulator could not be assembled - only ' + output.length + ' of ' + PICK_COUNT + ' matches found.');
    console.log(output.length + ' matches -> hot.json (' + reqCount + ' API req)\n');
    const gc = Math.ceil(output.length / 2);
    for (let g = 1; g <= gc; g++) {
        const gm = output.filter(m => m.group === g);
        const go = gm.reduce((a, m) => a * parseFloat(m.odds), 1);
        console.log('  Gr.' + g + ' (' + go.toFixed(2) + '):');
        gm.forEach(m => console.log('     [' + m.league + '] ' + m.match + ' | ' + m.tip + ' @ ' + m.odds + ' | ' + m.kickoff));
    }
}

main().catch(err => { console.error('Chyba:', err); process.exit(1); });
