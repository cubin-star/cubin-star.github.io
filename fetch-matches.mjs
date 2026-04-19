/**
 * fetch-matches.mjs
 *
 * Stahne fixtures z API-Football, odfiltruje blokovane zeme
 * a zapise vysledek do hot.json.
 *
 * Env: API_FOOTBALL_KEY1
 * Pouziti: node fetch-matches.mjs
 */

import { writeFileSync, readFileSync, existsSync } from 'fs';

const API_KEY = process.env.API_FOOTBALL_KEY1;
if (!API_KEY) { console.error('Chybi API_FOOTBALL_KEY1 env promenna.'); process.exit(1); }

const FOOTBALL_API = 'https://v3.football.api-sports.io';
const MIN_ODDS = 2.1;
const MAX_ODDS = 3.0;
const PICK_COUNT = 6;
const MIN_PLAYED = 5;

// League-relative criteria (ratios of game baseline)
// Baseline = avg(h_for, a_for, h_agn, a_agn) → auto-scales to any league
const BOTH_FLOOR_R = 0.85;      // oba alespoň 85% baseline
const STRONG_MIN_R = 1.10;      // "výrazný" tým 110%+ baseline
const CONTRAST_MAX_R = 0.95;    // protějšek pod 95% baseline (kontrast ≥ 15%)
const MIN_BASELINE = 1.25;      // minimum avg per-team stat → expected ~2.5+ gólů celkem
const MIN_ATTACK = 0.80;        // oba týmy musí střílet ≥ 0.8 g/z (žádný "mrtvý" útok)
// 2nd-half filter: stejný A/B princip aplikovaný na 2. poločas
// Používá stejné poměry (BOTH_FLOOR_R, STRONG_MIN_R, CONTRAST_MAX_R) ale na 2H data
const MIN_2H_BASELINE     = 0.45; // minimum 2H baseline (avg scored+conceded ve 2H)
const EXCLUDED_COUNTRIES = new Set(['Russia', 'Belarus']);
const FALLBACK_MAX_ODDS = 2.6;
const TZ = 'Europe/Prague';
let reqCount = 0;

function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }
function shuffle(arr) { for (let i = arr.length - 1; i > 0; i--) { const j = Math.floor(Math.random() * (i + 1)); [arr[i], arr[j]] = [arr[j], arr[i]]; } return arr; }
function fmtDate(d) { return d.toISOString().split('T')[0]; }

function getHalfStats(teamData, side) {
    const minute = teamData?.league?.goals?.[side]?.minute;
    const played = parseInt(teamData?.league?.fixtures?.played?.total) || 0;
    if (!minute || played === 0) return null;
    const val = (k) => parseInt(minute[k]?.total) || 0;
    const firstHalf  = val('0-15') + val('16-30') + val('31-45');
    const secondHalf = val('46-60') + val('61-75') + val('76-90');
    return { first: firstHalf, second: secondHalf, avgFirst: firstHalf / played, avgSecond: secondHalf / played, played };
}

async function apiFetch(path) {
    const url = FOOTBALL_API + path;
    for (let attempt = 1; attempt <= 3; attempt++) {
        try {
            const res = await fetch(url, { headers: { 'x-apisports-key': API_KEY } });
            reqCount++;
            if (res.status === 429) {
                console.warn('  [429] Rate limit hit, waiting ' + (5 * attempt) + 's... (attempt ' + attempt + '/3)');
                await sleep(5000 * attempt);
                continue;
            }
            if (!res.ok) { console.warn('  HTTP ' + res.status + ': ' + path.split('?')[0]); return { response: [], paging: { total: 0 } }; }
            const data = await res.json();
            if (data.errors && Object.keys(data.errors).length > 0) {
                const errStr = JSON.stringify(data.errors);
                if (errStr.includes('rateLimit') || errStr.includes('Too many')) {
                    console.warn('  [RATE] ' + errStr + ' waiting ' + (5 * attempt) + 's... (attempt ' + attempt + '/3)');
                    await sleep(5000 * attempt);
                    continue;
                }
                console.warn('  ', errStr);
                return { response: [], paging: { total: 0 } };
            }
            return data;
        } catch (e) { console.warn('  Fetch error:', e.message); return { response: [], paging: { total: 0 } }; }
    }
    console.warn('  [FAIL] Max retries for: ' + path.split('?')[0]);
    return { response: [], paging: { total: 0 } };
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
        if (page <= totalPages) await sleep(450);
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

    if (!bestP) {
        // Fallback: simple sequential pairing
        return picks.map((p, i) => ({ ...p, group: Math.floor(i / 2) + 1 }));
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
    console.log('Window: ' + now.toUTCString() + ' -> ' + max24h.toUTCString() + ' (24h)\n');

    // Collect dates covering the 24h window
    const dates = new Set();
    for (let d = new Date(now); d <= max24h; d = new Date(d.getTime() + 24 * 60 * 60 * 1000)) {
        dates.add(fmtDate(d));
    }
    let fixtures = [];
    for (const d of dates) { console.log('Fixtures ' + d + '...'); fixtures.push(...await getFixtures(d)); await sleep(450); }
    console.log('   ' + fixtures.length + ' scheduled matches\n');

    // Filter: ban only Russia and Belarus
    fixtures = fixtures.filter(f => {
        const t = new Date(f.fixture.date);
        const c = f.league.country;
        return t >= now && t <= max24h
            && !EXCLUDED_COUNTRIES.has(c);
    });
    console.log('   ' + fixtures.length + ' in 24h window (excl. RU/BY)');

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
            await sleep(450);
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

                // Oba týmy musí mít minimální útočný výkon
                if (hFor < MIN_ATTACK || aFor < MIN_ATTACK) {
                    console.log('   [WEAK] ' + m.match + ' | attack ' + hFor.toFixed(1) + '/' + aFor.toFixed(1) + ' (min ' + MIN_ATTACK + ')');
                    continue;
                }

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
                    // 2nd-half filter: stejný A/B princip na 2H data (scored + conceded)
                    const h2f = getHalfStats(home, 'for');
                    const a2f = getHalfStats(away, 'for');
                    const h2a = getHalfStats(home, 'against');
                    const a2a = getHalfStats(away, 'against');

                    if (!h2f || !a2f || !h2a || !a2a) {
                        console.log('   [2H-NODATA] ' + m.match + ' | no minute breakdown');
                        continue;
                    }
                    const hScr2 = h2f.avgSecond;   // domácí střílí ve 2H
                    const aScr2 = a2f.avgSecond;   // hosté střílí ve 2H
                    const hCon2 = h2a.avgSecond;   // domácí inkasují ve 2H
                    const aCon2 = a2a.avgSecond;   // hosté inkasují ve 2H

                    // 2H baseline (stejný koncept jako celkový baseline)
                    const base2h = (hScr2 + aScr2 + hCon2 + aCon2) / 4;
                    if (base2h < MIN_2H_BASELINE) {
                        console.log('   [2H-LOW] ' + m.match + ' | 2Hbase=' + base2h.toFixed(2) + ' < ' + MIN_2H_BASELINE + ' (scr ' + hScr2.toFixed(2) + '/' + aScr2.toFixed(2) + ', con ' + hCon2.toFixed(2) + '/' + aCon2.toFixed(2) + ')');
                        continue;
                    }

                    // Stejné poměry jako hlavní A/B, aplikované na 2H baseline
                    const floor2h = base2h * BOTH_FLOOR_R;
                    const strong2h = base2h * STRONG_MIN_R;
                    const contrast2h = base2h * CONTRAST_MAX_R;

                    // 2H Varianta A: oba inkasují ve 2H >= floor + ofenzivní kontrast ve 2H
                    const var2hA = (hCon2 >= floor2h && aCon2 >= floor2h)
                        && ((hScr2 >= strong2h && aScr2 < contrast2h) || (aScr2 >= strong2h && hScr2 < contrast2h));

                    // 2H Varianta B: oba střílí ve 2H >= floor + defenzivní kontrast ve 2H
                    const var2hB = (hScr2 >= floor2h && aScr2 >= floor2h)
                        && ((hCon2 >= strong2h && aCon2 < contrast2h) || (aCon2 >= strong2h && hCon2 < contrast2h));

                    if (!var2hA && !var2hB) {
                        console.log('   [2H-FAIL] ' + m.match + ' | 2H contrast fail: scr ' + hScr2.toFixed(2) + '/' + aScr2.toFixed(2) + ', con ' + hCon2.toFixed(2) + '/' + aCon2.toFixed(2) + ' (2Hbase=' + base2h.toFixed(2) + ', floor=' + floor2h.toFixed(2) + ', strong=' + strong2h.toFixed(2) + ')');
                        continue;
                    }

                    const tag = variantA ? 'A' : 'B';
                    const tag2h = var2hA ? '2A' : '2B';
                    const s = variantA ? [hFor, aFor].sort((a, b) => a - b) : [hAgn, aAgn].sort((a, b) => a - b);
                    const score = s[0] > 0 ? s[1] / s[0] : 99;
                    console.log('   [Q' + tag + '+' + tag2h + '] ' + m.match + ' | scored ' + hFor.toFixed(1) + '/' + aFor.toFixed(1) + ', conceded ' + hAgn.toFixed(1) + '/' + aAgn.toFixed(1) + ' | 2H: scr=' + hScr2.toFixed(2) + '/' + aScr2.toFixed(2) + ' con=' + hCon2.toFixed(2) + '/' + aCon2.toFixed(2) + ' (base=' + baseline.toFixed(2) + ', 2Hb=' + base2h.toFixed(2) + ', score=' + score.toFixed(2) + ')');
                    qualified.push({ ...m, contrastScore: score });
                }
            }
        }
        await sleep(450);
    }
    console.log('\nQualified (strict): ' + qualified.length + '/' + pool.length);

    // --- live1.json: all strict-qualified matches within 24h (no dedup, no limit) ---
    const live1 = qualified
        .filter(m => new Date(m.kickoff) <= max24h)
        .map(m => ({ league: m.league, match: m.match, kickoff: m.kickoff, tip: m.tip, odds: m.odds }));
    writeFileSync('live1.json', JSON.stringify(live1, null, 2), 'utf-8');
    console.log('live1.json: ' + live1.length + ' qualified matches in 24h window');

    // Best per league: keep only the match with highest contrastScore from each league
    // Normalize league names: "Serie D - Girone A" → "Serie D", etc.
    // Exception: international tournaments keep their groups as separate competitions.
    const TOURNAMENT_KW = ['world cup','euro ','european','copa america','africa cup','asian cup','nations league','champions league','europa league','conference league','libertadores','sudamericana','concacaf','afc cup','afc champions','olympic'];
    function normalizeLeague(name) {
        const low = name.toLowerCase();
        if (TOURNAMENT_KW.some(kw => low.includes(kw))) return name;
        return name.replace(/\s*[-–]\s*(Gir(?:one|\.)\s*\w+|Gr(?:oup|p\.?)\s*\w+|CFL\s*\w+|Zone\s*\w+|Conference\s*\w+|Division\s*\w+|North(?:ern)?|South(?:ern)?|East(?:ern)?|West(?:ern)?|[A-I])\s*$/i, '').trim();
    }

    const bestByLeague = new Map();
    for (const m of qualified) {
        const lg = normalizeLeague(m.league);
        const prev = bestByLeague.get(lg);
        if (!prev || m.contrastScore > prev.contrastScore) bestByLeague.set(lg, m);
    }
    const deduped = [...bestByLeague.values()];
    if (deduped.length < qualified.length) console.log('Dedup: ' + qualified.length + ' → ' + deduped.length + ' (best per league, normalized)');

    const fullAccumulator = deduped.length >= PICK_COUNT;

    // --- Priority 1: load matches from live2.json ---
    let selected = [];
    if (existsSync('live2.json')) {
        try {
            const live2 = JSON.parse(readFileSync('live2.json', 'utf-8'));
            if (Array.isArray(live2)) {
                for (const raw of live2) {
                    if (selected.length >= PICK_COUNT) break;
                    // live2.json uses PascalCase keys; normalize to camelCase
                    const m = {
                        league: raw.League || raw.league || '',
                        match: raw.Match || raw.match || '',
                        tip: raw.Tip || raw.tip || 'Over 2.5',
                        odds: raw.Odds || raw.odds || '0',
                        kickoff: raw.Date || raw.date || raw.kickoff || '',
                        fixtureId: raw.fixtureId || null,
                        contrastScore: raw.contrastScore || 0,
                    };
                    selected.push(m);
                    console.log('   [LIVE2] ' + m.match + ' | ' + m.tip + ' @ ' + m.odds);
                }
                console.log('From live2.json: ' + selected.length + ' matches');
            }
        } catch (e) { console.warn('Failed to read live2.json:', e.message); }
    } else {
        console.log('live2.json not found, skipping priority source');
    }

    // --- Priority 2: fill from qualified (deduped) matches ---
    if (selected.length < PICK_COUNT) {
        const selectedKeys = new Set(selected.map(m => m.match + '|' + m.kickoff));
        const within24h = deduped.filter(m => new Date(m.kickoff) <= max24h && !selectedKeys.has(m.match + '|' + m.kickoff));
        shuffle(within24h);
        console.log('Qualified pool (hot): ' + within24h.length + ' available');
        for (const m of within24h) {
            if (selected.length >= PICK_COUNT) break;
            selected.push(m);
            console.log('   [HOT] ' + m.match + ' | ' + m.tip + ' @ ' + m.odds);
        }
        console.log('After hot fill: ' + selected.length + '/' + PICK_COUNT);
    }

    // --- Priority 3: fill remaining with random picks from pool (no criteria, just odds range) ---
    if (selected.length < PICK_COUNT) {
        const selectedKeys = new Set(selected.map(m => m.match + '|' + m.kickoff));
        const fallback = pool.filter(m => !selectedKeys.has(m.match + '|' + m.kickoff) && new Date(m.kickoff) <= max24h && parseFloat(m.odds) <= FALLBACK_MAX_ODDS);
        shuffle(fallback);
        console.log('Random fallback pool: ' + fallback.length + ' (24h), odds ≤' + FALLBACK_MAX_ODDS);
        for (const m of fallback) {
            if (selected.length >= PICK_COUNT) break;
            m.contrastScore = m.contrastScore || 0;
            m.forced = true;
            selected.push(m);
            console.log('   [RANDOM] ' + m.match + ' | ' + m.tip + ' @ ' + m.odds);
        }
    }

    const forcedCount = selected.filter(m => m.forced).length;
    if (forcedCount > 0) console.log('Ticket: ' + (selected.length - forcedCount) + ' strict + ' + forcedCount + ' random');
    if (selected.length < PICK_COUNT) console.log('WARNING: Only ' + selected.length + '/' + PICK_COUNT + ' matches available even after fallback.');

    // --- best.json: top 3 from final selected (strict + random) by highest odds ---
    const best = [...selected]
        .sort((a, b) => parseFloat(b.odds) - parseFloat(a.odds))
        .slice(0, 3)
        .map(m => ({ league: m.league, match: m.match, kickoff: m.kickoff, tip: m.tip, odds: m.odds }));
    writeFileSync('best.json', JSON.stringify(best, null, 2), 'utf-8');
    console.log('best.json: ' + best.length + ' matches (top odds)');

    if (selected.length === 0) {
        writeFileSync('hot.json', JSON.stringify([], null, 2), 'utf-8');
        console.log('No matches at all. Written empty hot.json (' + reqCount + ' API req)');
        process.exit(0);
    }

    // Balance into groups and write output
    const grouped = balanceGroups(selected);
    const output = grouped.map(m => ({ league: m.league, match: m.match, kickoff: m.kickoff, tip: m.tip, odds: m.odds, group: m.group }));
    writeFileSync('hot.json', JSON.stringify(output, null, 2), 'utf-8');

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
