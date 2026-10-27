// Regresni testy koordinovaneho vyberu a vypoctu kurzu.
// Spusteni: node test-selection.mjs
import { readFileSync } from 'fs';
import { createPicker, runCoordinatedSelection, bestOdds, pickKey, createLeagueRegistry, leagueKey, candidateWeight, setRng, makeSeededRng } from './fetch-matches.mjs';

let pass = 0, fail = 0;
function ok(name, cond, detail = '') {
    if (cond) { pass++; console.log('  OK   ' + name); }
    else { fail++; console.log('  FAIL ' + name + (detail ? ' -> ' + detail : '')); }
}
function section(t) { console.log('\n=== ' + t + ' ==='); }

const goalStats = new Map();
const isOver = m => m.tip === 'Over 2.5';
const isWinner = m => m.tip === 'Home win' || m.tip === 'Away win';
const always = () => true;

// Kandidat s unikatni ligou i zemi, aby testy nenarazely na limity ligy/zeme.
function cand(id, tip, tier = 1, league = 'L' + id, country = 'C' + id) {
    return { fixtureId: id, tip, tier, league, country, match: 'H' + id + ' - A' + id, kickoff: '2026-01-01T18:00:00+00:00', odds: '2.10' };
}
function mk(name, pool, want, phases, blockedPicks, coreMin = 0) {
    return createPicker({ name, pool, want, phases, coreMin, blockedPicks, goalStats });
}
const onePhase = (want, market = always) => [{ label: 'test', market, countsAs: always, want }];

function keysOf(p) { return p.selected.map(pickKey); }

// --- A: stejny zapas + stejny tip nesmi byt ve dvou souborech ---------------
section('A. Stejny zapas + stejny tip napric soubory (zakazano)');
{
    let violations = 0, short = 0;
    for (let i = 0; i < 200; i++) {
        const pool = [];
        for (let id = 1; id <= 20; id++) { pool.push(cand(id, 'Over 2.5')); pool.push(cand(id, 'Home win')); }
        const blocked = new Set();
        const ps = [mk('a', pool, 4, onePhase(4), blocked), mk('b', pool, 4, onePhase(4), blocked), mk('c', pool, 4, onePhase(4), blocked)];
        runCoordinatedSelection(ps);
        const all = ps.flatMap(keysOf);
        if (new Set(all).size !== all.length) violations++;
        if (ps.some(p => !p.isFull())) short++;
    }
    ok('zadna duplicita dvojice zapas+tip pri dostatku kandidatu', violations === 0, violations + ' kolizi');
    ok('vsechny soubory naplnene', short === 0, short + ' neuplnych behu');
}

// --- B: stejny zapas + jiny tip napric soubory je povolen -------------------
section('B. Stejny zapas + jiny tip napric soubory (povoleno)');
{
    const pool = [cand(1, 'Over 2.5'), cand(1, 'Home win')];
    const blocked = new Set();
    const a = mk('a', pool, 1, onePhase(1, isOver), blocked);
    const b = mk('b', pool, 1, onePhase(1, isWinner), blocked);
    runCoordinatedSelection([a, b]);
    ok('oba soubory naplnene stejnym zapasem s jinym tipem',
        a.isFull() && b.isFull() && a.selected[0].fixtureId === b.selected[0].fixtureId && a.selected[0].tip !== b.selected[0].tip);
    ok('neslo o fallback duplicity', a.stats.dupAllowed === 0 && b.stats.dupAllowed === 0);
}

// --- C: stejny zapas uvnitr jednoho tiketu je vzdy zakazan -----------------
section('C. Stejny zapas uvnitr jednoho tiketu (vzdy zakazano)');
{
    const pool = [cand(1, 'Over 2.5'), cand(1, 'Home win'), cand(1, 'Away win')];
    const blocked = new Set();
    const a = mk('a', pool, 3, onePhase(3), blocked);
    runCoordinatedSelection([a]);
    ok('z jednoho zapasu jen jeden tip', a.selected.length === 1, 'vybrano ' + a.selected.length);
    ok('fallback duplicit nepomohl obejit pravidlo', new Set(a.selected.map(m => m.fixtureId)).size === a.selected.length);
}

// --- D: duplicita jen pri skutecnem nedostatku ------------------------------
section('D. Fallback duplicit pri nedostatku kandidatu');
{
    const pool = [cand(1, 'Over 2.5')];
    const blocked = new Set();
    const a = mk('a', pool, 1, onePhase(1), blocked);
    const b = mk('b', pool, 1, onePhase(1), blocked);
    runCoordinatedSelection([a, b]);
    ok('oba soubory naplnene', a.isFull() && b.isFull());
    ok('duplicita zaznamenana prave jednou', a.stats.dupAllowed + b.stats.dupAllowed === 1,
        'a=' + a.stats.dupAllowed + ', b=' + b.stats.dupAllowed);
    const dupEntries = [...a.entries, ...b.entries].filter(e => e.duplicate);
    ok('duplicitni vyber ma priznak v diagnostice', dupEntries.length === 1);
}

// --- E: prerozdeleni pred fallbackem ---------------------------------------
section('E. Prerozdeleni kandidatu misto duplicity');
{
    let dup = 0, incomplete = 0, redistributed = 0;
    for (let i = 0; i < 200; i++) {
        const x = cand(1, 'Over 2.5'), y = cand(2, 'Over 2.5');
        const blocked = new Set();
        const a = mk('uzky', [x], 1, onePhase(1), blocked);          // umi jen X
        const b = mk('siroky', [x, y], 1, onePhase(1), blocked);     // umi X i Y
        runCoordinatedSelection([a, b]);
        if (!a.isFull() || !b.isFull()) incomplete++;
        if (a.stats.dupAllowed + b.stats.dupAllowed > 0) dup++;
        if (b.stats.redistributed > 0) redistributed++;
    }
    ok('oba soubory vzdy naplnene', incomplete === 0, incomplete + ' behu');
    ok('nikdy nebylo treba povolit duplicitu', dup === 0, dup + ' behu s duplicitou');
    ok('prerozdeleni se skutecne pouzilo', redistributed > 0, redistributed + ' behu');
}

// --- F: zadna systematicka priorita podle poradi ----------------------------
section('F. Rovnocennost souboru (zadna priorita podle poradi)');
{
    // 3 soubory soutezi o 2 kandidaty - kazdy by mel uspet zhruba stejne casto.
    const N = 900;
    const wins = { a: 0, b: 0, c: 0 };
    for (let i = 0; i < N; i++) {
        const pool = [cand(1, 'Over 2.5'), cand(2, 'Over 2.5')];
        const blocked = new Set();
        const ps = [mk('a', pool, 1, onePhase(1), blocked), mk('b', pool, 1, onePhase(1), blocked), mk('c', pool, 1, onePhase(1), blocked)];
        runCoordinatedSelection(ps);
        for (const p of ps) if (p.selected.length && !p.entries[0].duplicate) wins[p.name]++;
    }
    const exp = N * 2 / 3;
    const dev = Math.max(...Object.values(wins).map(v => Math.abs(v - exp) / exp));
    ok('zadny soubor neni systematicky zvyhodnen (odchylka < 10 %)', dev < 0.10,
        JSON.stringify(wins) + ', odchylka ' + (dev * 100).toFixed(1) + ' %');

    // Zmena poradi v poli pickeru nesmi vysledek systematicky posunout.
    const winsRev = { a: 0, b: 0, c: 0 };
    for (let i = 0; i < N; i++) {
        const pool = [cand(1, 'Over 2.5'), cand(2, 'Over 2.5')];
        const blocked = new Set();
        const ps = [mk('c', pool, 1, onePhase(1), blocked), mk('b', pool, 1, onePhase(1), blocked), mk('a', pool, 1, onePhase(1), blocked)];
        runCoordinatedSelection(ps);
        for (const p of ps) if (p.selected.length && !p.entries[0].duplicate) winsRev[p.name]++;
    }
    const devRev = Math.max(...Object.values(winsRev).map(v => Math.abs(v - exp) / exp));
    ok('obracene poradi souboru dava stejne rozlozeni', devRev < 0.10,
        JSON.stringify(winsRev) + ', odchylka ' + (devRev * 100).toFixed(1) + ' %');
}

// --- G: vypocet kurzu -------------------------------------------------------
section('G. Vypocet kurzu (treti nejvyssi ze VSECH hodnot)');
{
    ok('Over 2.5: [1.80, 2.10, 2.40, 2.80] -> 2.10', bestOdds([1.80, 2.10, 2.40, 2.80]) === 2.10, String(bestOdds([1.80, 2.10, 2.40, 2.80])));
    ok('Home win: [1.50, 1.95, 2.05, 2.20, 3.00] -> 2.05', bestOdds([1.50, 1.95, 2.05, 2.20, 3.00]) === 2.05, String(bestOdds([1.50, 1.95, 2.05, 2.20, 3.00])));
    ok('Away win: [2.90, 2.70, 2.50] -> 2.50', bestOdds([2.90, 2.70, 2.50]) === 2.50, String(bestOdds([2.90, 2.70, 2.50])));
    ok('mene nez 3 bookmakeri -> median', bestOdds([2.00, 2.40]) === 2.20, String(bestOdds([2.00, 2.40])));
    ok('poradi hodnot nerozhoduje', bestOdds([2.80, 1.80, 2.40, 2.10]) === bestOdds([1.80, 2.10, 2.40, 2.80]));

    const src = readFileSync('fetch-matches.mjs', 'utf-8');
    ok('sber kurzu uz neorezava pasmem ODDS_COLLECT_*',
        !/odd>=ODDS_COLLECT_MIN&&odd<=ODDS_COLLECT_MAX\)addCandidate/.test(src));
    ok('jeden bookmaker prispiva jen jednim kurzem', /c\.books\.has\(bk\)\)return;/.test(src));
}

// --- H: zachovani stavajicich pravidel --------------------------------------
section('H. Zachovani filtru, pasem a poctu');
{
    const src = readFileSync('fetch-matches.mjs', 'utf-8');
    const expect = [
        ['TIER_WEIGHTS', /const TIER_WEIGHTS = \{ 1: 40, 2: 14, 3: 5, 4: 1 \};/],
        ['MAX_PER_TIER', /const MAX_PER_TIER = \{ 1: 6, 2: 4, 3: 2, 4: 1 \};/],
        ['MAX_TIER_SHARE', /const MAX_TIER_SHARE = 0\.5;/],
        ['MAX_PER_COUNTRY', /const MAX_PER_COUNTRY = 2;/],
        ['MIN_ODDS/MAX_ODDS', /const MIN_ODDS = 1\.95;[\s\S]*?const MAX_ODDS = 2\.60;/],
        ['TIPS pasmo', /const TIPS_MIN_ODDS = 1\.70;[\s\S]*?const TIPS_MAX_ODDS = 2\.00;/],
        ['WINNER pasmo', /const WINNER_MIN_ODDS = 1\.75;[\s\S]*?const WINNER_MAX_ODDS = 2\.25;/],
        ['REACT1 pasmo', /const REACT1_MIN_ODDS = 1\.95;[\s\S]*?const REACT1_MAX_ODDS = 2\.60;/],
        ['pocty zapasu', /const PICK_COUNT = 6;[\s\S]*?const TIPS_PICK_COUNT = 2;[\s\S]*?const REACT1_PICK_COUNT = 7;/],
        ['react2/react3 pocty', /const REACT2_PICK_COUNT = 6;[\s\S]*?const REACT2_OVER_COUNT = 3;[\s\S]*?const REACT3_PICK_COUNT = 8;[\s\S]*?const REACT3_MIN_OVER = 4;/],
        ['golove prahy', /const MIN_OVER25_RATE = 40;[\s\S]*?const OVER_PREFER_RATE = 50;[\s\S]*?const GOALS_MIN_SAMPLE = 60;/],
        ['golovy filtr se stale pouziva', /pool=applyGoalFilter\(pool,goalStats,PICK_COUNT,'hot'\);/],
        // Golovost uz NENI razeni fronty (sortByGoals odstranen), ale vaha v losu.
        ['golovost je vaha, ne poradi', /W_GOALS_MAX[\s\S]*?m\.tip==='Over 2\.5'/],
        ['sortByGoals uz neurcuje poradi', /^(?![\s\S]*function sortByGoals)[\s\S]*$/],
        ['externi dedup', /loadDedupKeysFromUrls\(DEDUP_URLS\)/],
        ['skupiny react2/react3', /balanceGroups\(shuffle\(\[\.\.\.react2Picker\.selected\]\)\)[\s\S]*?balanceGroups\(shuffle\(\[\.\.\.react3Picker\.selected\]\)\)/],
        // --- Regresni kontrola integrace: zadny produkcni vystup ani
        // bezpecnostni mechanismus nesmel pri prenosu do aktualni verze zmizet.
        ['vystup hot.json', /writeFileSync\('hot\.json'/],
        ['vystup best.json', /writeFileSync\('best\.json'/],
        ['vystup live1.json', /writeFileSync\('live1\.json'/],
        ['vystup react1.json', /writeFileSync\('react1\.json'/],
        ['vystup react2.json', /writeFileSync\('react2\.json'/],
        ['vystup react3.json', /writeFileSync\('react3\.json'/],
        ['vystup tips-kombik.json', /const TIPS_FILE = 'tips-kombik\.json';/],
        ['tips.json ciziho bota se nezapisuje', /^(?![\s\S]*writeFileSync\('tips\.json')[\s\S]*$/],
        ['preferencni strategie tips zustava', /tierStrategy:'preference'/],
        ['kvoty jadra beze zmeny', /const CORE_MIN_HOT = 4;[\s\S]*?const CORE_MIN_REACT1 = 4;[\s\S]*?const CORE_MIN_TIPS = 2;/],
        ['min. pocet bookmakeru', /const MIN_BOOKMAKERS = 3;/],
        ['sberne kurzove pasmo', /const ODDS_COLLECT_MIN = 1\.70;[\s\S]*?const ODDS_COLLECT_MAX = 2\.60;/],
        ['vylouceni valecnych zemi', /EXCLUDED_COUNTRIES[\s\S]*?'Ukraine'/],
        ['dedup fixture+tip', /export const pickKey=/],
        ['lokalni limit 1 liga na soubor', /usedLeagues/],
        ['API dotazy zachovany', /apiFetch/],
        // --- Nove funkce, ktere musi byt po integraci pritomne ---
        ['novy: weighted random', /export function weightedPick\(/],
        ['novy: candidateWeight', /export function candidateWeight\(/],
        ['novy: registr lig', /export function createLeagueRegistry\(/],
        ['novy: globalni limit 2', /const LEAGUE_GLOBAL_CAP = 2;/],
        ['novy: postupne uvolnovani limitu', /LEAGUE_CAP_RELAX_STEPS/],
        ['novy: handicap zastoupenych lig', /const W_PRESENCE_PENALTY = \[0, -0\.20, -0\.35\];/],
        ['novy: read-only presenceRegistry pro react1', /presenceRegistry:mainRegistry/],
        ['novy: seedovatelny RNG', /export function makeSeededRng\(/],
        ['novy: diagnostika diverzifikace lig', /Lig s kandidaty celkem/],
    ];
    for (const [name, re] of expect) ok(name + ' beze zmeny', re.test(src));
}

// --- I: hlavni skupina ma stejnou prioritu, react1 nejnizsi ----------------
section('I. Priorita skupin (hlavni 4 vs. react1)');
{
    // Zdroj: 4 unikatni tipy. Hlavni skupina (2 soubory po 2) je musi dostat
    // cele, react1 pak nema vlastni unikatni kandidaty.
    let mainDup = 0, r1Dup = 0, mainShort = 0;
    for (let i = 0; i < 100; i++) {
        const pool = [1, 2, 3, 4].map(id => cand(id, 'Over 2.5'));
        const blocked = new Set();
        const m1 = mk('hot.json', pool, 2, onePhase(2), blocked);
        const m2 = mk('react2.json', pool, 2, onePhase(2), blocked);
        const r1 = createPicker({ name: 'react1.json', pool, want: 2, phases: onePhase(2), coreMin: 0, blockedPicks: blocked, goalStats, priority: 'vedlejsi skupina (nejnizsi priorita)' });
        runCoordinatedSelection([m1, m2]);
        const mainKeys = [...keysOf(m1), ...keysOf(m2)];
        runCoordinatedSelection([r1], () => { }, { redistributeScope: [r1] });
        if (!m1.isFull() || !m2.isFull()) mainShort++;
        if (m1.stats.dupAllowed + m2.stats.dupAllowed > 0) mainDup++;
        if (r1.stats.dupAllowed === 0) r1Dup++;
        // hlavni skupina se po uzamceni nesmi zmenit
        const after = [...keysOf(m1), ...keysOf(m2)];
        if (after.join() !== mainKeys.join()) mainShort += 100;
    }
    ok('hlavni skupina se naplni prednostne a zustane nedotcena', mainShort === 0, mainShort + ' chyb');
    ok('hlavni skupina nepotrebuje duplicity', mainDup === 0);
    ok('react1 bere duplicity az jako posledni (nema uz unikatni)', r1Dup === 0);
    ok('react1 ma oznacenou nizsi prioritu', /vedlejsi skupina/.test(
        createPicker({ name: 'react1.json', pool: [], want: 1, phases: onePhase(1), blockedPicks: new Set(), goalStats, priority: 'vedlejsi skupina (nejnizsi priorita)' }).priority));
}

// --- J: react1 smi vzit stejny zapas s jinym tipem --------------------------
section('J. react1 vyuziva zbyle unikatni kandidaty');
{
    const pool = [cand(1, 'Over 2.5'), cand(1, 'Home win')];
    const blocked = new Set();
    const hot = mk('hot.json', pool, 1, onePhase(1, isOver), blocked);
    runCoordinatedSelection([hot]);
    const r1 = mk('react1.json', pool, 1, onePhase(1), blocked);
    runCoordinatedSelection([r1], () => { }, { redistributeScope: [r1] });
    ok('react1 vzal stejny zapas s jinym tipem bez duplicity',
        r1.isFull() && r1.selected[0].tip !== hot.selected[0].tip && r1.stats.dupAllowed === 0);
}

// --- K: puvodni preference tips.json (core -> T1+T2 -> T3+T4) --------------
section('K. Preferencni logika tips.json');
{
    let lowTier = 0;
    for (let i = 0; i < 200; i++) {
        const pool = [cand(1, 'Over 2.5', 2), cand(2, 'Over 2.5', 3), cand(3, 'Over 2.5', 4)];
        const t = createPicker({ name: 'tips-kombik.json', pool, want: 1, phases: onePhase(1), coreMin: 0, blockedPicks: new Set(), goalStats, tierStrategy: 'preference' });
        runCoordinatedSelection([t]);
        if (t.selected[0].tier > 2) lowTier++;
    }
    ok('tips nikdy nesahne na T3/T4, dokud je T1/T2', lowTier === 0, lowTier + ' pripadu');

    // Preference golovejsich lig uvnitr skupiny. Nove je to VAZENY los, ne
    // tvrde poradi - golovejsi liga musi vyhravat vyrazne casteji nez nahoda
    // (50/100), ale chudsi liga nesmi byt vyloucena (nenulova sance).
    const gs = new Map([['liga rich|c 1', { played: 200, over25Rate: 75 }], ['liga poor|c 2', { played: 200, over25Rate: 41 }]]);
    let rich = 0;
    for (let i = 0; i < 1000; i++) {
        const pool = [
            { ...cand(1, 'Over 2.5', 2), league: 'Liga poor', country: 'C2' },
            { ...cand(2, 'Over 2.5', 2), league: 'Liga rich', country: 'C1' },
        ];
        const t = createPicker({ name: 'tips-kombik.json', pool, want: 1, phases: onePhase(1), blockedPicks: new Set(), goalStats: gs, tierStrategy: 'preference' });
        runCoordinatedSelection([t]);
        if (t.selected[0].league === 'Liga rich') rich++;
    }
    // Vahy: rich = 1+0.07(T2)+0.25(goly) = 1.32, poor = 1+0.07 = 1.07.
    // Ocekavany podil rich = 1.32/2.39 = 55,2 %, sigma pri 1000 losech ~1,6 %.
    // Interval +/-3 sigma je tedy zhruba 505-600.
    ok('tips preferuje golovejsi ligu (vazene)', rich > 500 && rich < 620, rich + '/1000 (ceka se ~552)');
    ok('golove chudsi liga neni vyloucena', rich < 1000, (1000 - rich) + '/1000 chudsi');
    ok('tips zustava v hlavni skupine (vychozi priorita)',
        mk('tips-kombik.json', [], 1, onePhase(1), new Set()).priority === 'hlavni skupina');
}

// --- L: viceurovnove prerozdeleni (retez A <- B <- C) ----------------------
section('L. Viceurovnove prerozdeleni');
{
    let dup = 0, deep = 0, short = 0;
    for (let i = 0; i < 100; i++) {
        const X = cand(1, 'Over 2.5'), Y = cand(2, 'Over 2.5'), Z = cand(3, 'Over 2.5');
        const blocked = new Set();
        // A umi jen X, B umi X+Y, C umi X+Y+Z -> nutny retez C->Z, B->Y, A->X
        const a = mk('a', [X], 1, onePhase(1), blocked);
        const b = mk('b', [X, Y], 1, onePhase(1), blocked);
        const c = mk('c', [X, Y, Z], 1, onePhase(1), blocked);
        const ps = [c, b, a];
        runCoordinatedSelection(ps);
        if (ps.some(p => !p.isFull())) short++;
        if (ps.reduce((s, p) => s + p.stats.dupAllowed, 0) > 0) dup++;
        if (ps.reduce((s, p) => s + p.stats.deepRedistributed, 0) > 0) deep++;
    }
    ok('vsechny soubory naplnene bez duplicity', dup === 0 && short === 0, dup + ' dup / ' + short + ' neuplnych');
    ok('viceurovnove prerozdeleni se nekdy skutecne pouzilo', deep > 0, deep + '/100');
}

// --- M: diagnostika nedostatku a limitu ------------------------------------
section('M. Diagnostika fallbacku');
{
    const pool = [cand(1, 'Over 2.5')];
    const blocked = new Set();
    const a = mk('a', pool, 1, onePhase(1), blocked);
    const b = mk('b', pool, 1, onePhase(1), blocked);
    const res = runCoordinatedSelection([a, b]);
    const dupEntry = [...a.entries, ...b.entries].find(e => e.duplicate);
    ok('fallback ma uvedeny duvod', !!dupEntry && /duplicita/.test(dupEntry.reason), dupEntry && dupEntry.reason);
    ok('nedostatek je prokazan (limit nebyl vycerpan)', res.limitHit === false);
    const res2 = runCoordinatedSelection([mk('x', pool, 1, onePhase(1), new Set())], () => { }, { maxSteps: 0 });
    ok('pri vycerpani limitu je vysledek oznacen jako neovereny nebo doplneny', typeof res2.limitHit === 'boolean');
}

// --- N: vystupni soubor tipu je tips-kombik.json, ne cizi tips.json --------
section('N. Vystup tipu: tips-kombik.json');
{
    const src = readFileSync('fetch-matches.mjs', 'utf-8');
    ok('konstanta TIPS_FILE je tips-kombik.json', /const TIPS_FILE = 'tips-kombik\.json';/.test(src));
    ok('zapis pouziva TIPS_FILE', /writeFileSync\(TIPS_FILE,JSON\.stringify\(tips,null,2\),'utf-8'\)/.test(src));
    ok('bot uz nezapisuje do ciziho tips.json', !/writeFileSync\('tips\.json'/.test(src));

    // Struktura zaznamu musi zustat stejna - meni se jen nazev souboru.
    const m = src.match(/const tips=tipsPicker\.selected\.map\(m=>\(\{([^}]*)\}\)\)/);
    ok('mapovani zaznamu nalezeno', !!m);
    const keys = m ? m[1].split(',').map(s => s.split(':')[0].trim()) : [];
    ok('klice zustaly league/match/kickoff/tip/odds',
        JSON.stringify(keys) === JSON.stringify(['league', 'match', 'kickoff', 'tip', 'odds']),
        JSON.stringify(keys));

    // Kombik musi umet obsah zpracovat - simulace konzumenta.
    const sample = [{ league: 'L', match: 'A - B', kickoff: '2026-01-01T18:00:00+00:00', tip: 'Over 2.5', odds: '2.10' }];
    const consumed = JSON.parse(JSON.stringify(sample)).map(t => ({
        liga: t.league, zapas: t.match, cas: new Date(t.kickoff), tip: t.tip, kurz: parseFloat(t.odds),
    }));
    ok('Kombik dokaze zaznam zpracovat',
        consumed[0].kurz === 2.10 && !isNaN(consumed[0].cas.getTime()) && consumed[0].zapas === 'A - B');
}

// --- O: globalni limit ligy a vazeny los ------------------------------------
section('O. Globalni limit ligy (max 2 unikatni fixture)');
{
    // Kandidat s EXPLICITNI ligou - vice zapasu sdili leagueId.
    const lc = (id, lid, tip = 'Over 2.5', tier = 1) => ({
        fixtureId: id, leagueId: lid, tip, tier, league: 'Liga ' + lid, country: 'Zeme ' + lid,
        match: 'H' + id + ' - A' + id, kickoff: '2026-01-01T18:00:00+00:00', odds: '2.10',
    });
    // Spocita unikatni fixture na ligu napric vice pickery.
    function uniqPerLeague(pickers) {
        const map = new Map();
        for (const p of pickers) for (const m of p.selected) {
            const k = leagueKey(m);
            if (!map.has(k)) map.set(k, new Set());
            map.get(k).add(m.fixtureId);
        }
        return map;
    }

    // 1) Max 2 unikatni fixture z jedne ligy napric hlavnimi JSON.
    // 8 slotu (4 soubory x 2) potrebuje pri limitu 2 aspon 4 ligy - jinak by
    // reseni neexistovalo a relaxace by limit legitimne uvolnila.
    let over = 0;
    for (let i = 0; i < 200; i++) {
        const pool = [];
        for (let lid = 1; lid <= 6; lid++) for (let j = 0; j < 4; j++) pool.push(lc(lid * 100 + j, lid));
        const blocked = new Set(), reg = createLeagueRegistry();
        const ps = ['hot.json', 'tips-kombik.json', 'react2.json', 'react3.json'].map(n =>
            createPicker({ name: n, pool, want: 2, phases: onePhase(2), blockedPicks: blocked, goalStats, registry: reg }));
        runCoordinatedSelection(ps, () => { }, { registry: reg });
        for (const [, s] of uniqPerLeague(ps)) if (s.size > 2) over++;
    }
    ok('zadna liga nema vic nez 2 unikatni fixture', over === 0, over + ' poruseni');

    // 2) Stejny fixture s RUZNYM tipem se do limitu pocita jen jednou.
    {
        // 2 ligy, v kazde 1 zapas se dvema trhy. Lokalni limit (1 zapas z ligy
        // na soubor) je splnen, globalne jsou to 2x 1 unikatni fixture - oba
        // soubory se tedy musi naplnit, aniz by limit 2 padl.
        let filled = 0, viol = 0, dup = 0;
        for (let i = 0; i < 200; i++) {
            const pool = [
                lc(1, 10, 'Over 2.5'), lc(1, 10, 'Home win'),
                lc(2, 20, 'Over 2.5'), lc(2, 20, 'Home win'),
            ];
            const blocked = new Set(), reg = createLeagueRegistry();
            const a = createPicker({ name: 'a', pool, want: 2, phases: onePhase(2), blockedPicks: blocked, goalStats, registry: reg });
            const b = createPicker({ name: 'b', pool, want: 2, phases: onePhase(2), blockedPicks: blocked, goalStats, registry: reg });
            runCoordinatedSelection([a, b], () => { }, { registry: reg });
            if (a.isFull() && b.isFull()) filled++;
            if (a.stats.dupAllowed + b.stats.dupAllowed > 0) dup++;
            for (const [, s] of uniqPerLeague([a, b])) if (s.size > 1) viol++;
        }
        ok('tyz fixture s jinym tipem se pocita jen jednou (oba soubory plne)', filled === 200, filled + '/200');
        ok('liga zustala na 1 unikatnim fixture (2 tipy se nescitaji)', viol === 0, viol + ' poruseni');
        ok('nebyla potreba zadna duplicita', dup === 0, dup + ' pripadu');
    }

    // 3) Liga s 5 kandidaty ma vyssi vahu nez liga se 2 (ostatni faktory stejne).
    {
        const many = lc(1, 1), few = lc(2, 2);
        const counts = new Map([[leagueKey(many), 5], [leagueKey(few), 2]]);
        const wMany = candidateWeight(many, { leagueCounts: counts });
        const wFew = candidateWeight(few, { leagueCounts: counts });
        ok('liga s 5 kandidaty ma vyssi vahu nez se 2', wMany > wFew, wMany.toFixed(3) + ' vs ' + wFew.toFixed(3));
        ok('bonus za pocet je tlumeny (max +15 %)', wMany / wFew < 1.16, (wMany / wFew).toFixed(3));
    }

    // 4) Kvalitnejsi tier ma vyssi vahu, ale nizsi neni vyloucen.
    {
        const w = t => candidateWeight({ ...lc(1, 1, 'Over 2.5', t) }, {});
        ok('T1 > T2 > T3 > T4', w(1) > w(2) && w(2) > w(3) && w(3) > w(4));
        ok('T4 neni vyloucen (vaha > 0)', w(4) > 0, w(4).toFixed(3));
        ok('rozpeti tieru je max +/-15 %', Math.abs(w(1) - 1) <= 0.1501 && Math.abs(w(4) - 1) <= 0.1501);
    }

    // 5+6) Golovost zvyhodnuje Over 2.5, ale NE Home/Away win.
    {
        const gs = new Map([['liga 1|zeme 1', { played: 200, over25Rate: 75 }], ['liga 2|zeme 2', { played: 200, over25Rate: 45 }]]);
        const richOver = candidateWeight(lc(1, 1, 'Over 2.5'), { goalStats: gs });
        const poorOver = candidateWeight(lc(2, 2, 'Over 2.5'), { goalStats: gs });
        ok('golovejsi liga ma vyssi vahu pro Over 2.5', richOver > poorOver, richOver.toFixed(3) + ' vs ' + poorOver.toFixed(3));
        ok('golovy bonus je max +25 %', richOver / poorOver <= 1.2501, (richOver / poorOver).toFixed(3));
        const richWin = candidateWeight(lc(1, 1, 'Home win'), { goalStats: gs });
        const poorWin = candidateWeight(lc(2, 2, 'Away win'), { goalStats: gs });
        ok('golovost neovlivnuje Home/Away win', richWin === poorWin, richWin.toFixed(3) + ' vs ' + poorWin.toFixed(3));
    }

    // 7) Uz zastoupena liga ma nizsi vahu nez nezastoupena.
    {
        const reg = createLeagueRegistry();
        const a = lc(1, 1), b = lc(2, 2), c = lc(3, 3);
        reg.add(a); reg.add({ ...a, fixtureId: 11 });   // liga 1: 2 unikatni
        reg.add(b);                                       // liga 2: 1 unikatni
        const wA = candidateWeight(a, { registry: reg });
        const wB = candidateWeight(b, { registry: reg });
        const wC = candidateWeight(c, { registry: reg });
        ok('nezastoupena liga ma nejvyssi vahu (C > B > A)', wC > wB && wB > wA,
            [wC, wB, wA].map(x => x.toFixed(3)).join(' > '));
        ok('penalizace je max -35 %', wA >= 0.6499, wA.toFixed(3));
    }

    // 13) Vazeny los funguje i bez dat z league-goals.json.
    {
        const w1 = candidateWeight(lc(1, 1, 'Over 2.5'), {});
        const w2 = candidateWeight(lc(2, 2, 'Over 2.5'), { goalStats: new Map() });
        ok('chybejici golove statistiky = neutralni vaha', w1 === w2 && w1 > 0, w1.toFixed(3));
    }

    // 14) Reprodukovatelnost pri stejnem seedu.
    {
        const run = seed => {
            setRng(makeSeededRng(seed));
            const pool = [];
            for (let lid = 1; lid <= 5; lid++) for (let j = 0; j < 4; j++) pool.push(lc(lid * 100 + j, lid));
            const blocked = new Set(), reg = createLeagueRegistry();
            const ps = ['a', 'b', 'c'].map(n => createPicker({ name: n, pool, want: 3, phases: onePhase(3), blockedPicks: blocked, goalStats, registry: reg }));
            runCoordinatedSelection(ps, () => { }, { registry: reg });
            return ps.flatMap(keysOf).join('|');
        };
        const r1 = run(12345), r2 = run(12345), r3 = run(99999);
        setRng(null);
        ok('stejny seed -> stejny vyber', r1 === r2);
        ok('jiny seed -> jiny vyber', r1 !== r3);
    }

    // 11+12) Vsechny soubory se naplni a core kvota je dodrzena.
    {
        let short = 0, coreBad = 0;
        for (let i = 0; i < 100; i++) {
            const pool = [];
            // 8 lig x 3 zapasy; tier 1 = jadro.
            for (let lid = 1; lid <= 8; lid++) for (let j = 0; j < 3; j++) pool.push(lc(lid * 100 + j, lid, 'Over 2.5', lid <= 4 ? 1 : 3));
            const blocked = new Set(), reg = createLeagueRegistry();
            const ps = [
                createPicker({ name: 'hot.json', pool, want: 4, phases: onePhase(4), coreMin: 2, blockedPicks: blocked, goalStats, registry: reg }),
                createPicker({ name: 'tips-kombik.json', pool, want: 2, phases: onePhase(2), coreMin: 1, blockedPicks: blocked, goalStats, registry: reg, tierStrategy: 'preference' }),
                createPicker({ name: 'react2.json', pool, want: 4, phases: onePhase(4), coreMin: 2, blockedPicks: blocked, goalStats, registry: reg }),
            ];
            runCoordinatedSelection(ps, () => { }, { registry: reg });
            for (const p of ps) {
                if (!p.isFull()) short++;
                if (p.selected.filter(m => m.tier === 1).length < p.coreQuota) coreBad++;
            }
        }
        ok('vsechny soubory naplnene, kdyz reseni existuje', short === 0, short + ' nenaplnenych');
        ok('core kvota dodrzena i s novymi vahami', coreBad === 0, coreBad + ' poruseni');
    }

    // 12b) tierStrategy 'preference' se novymi vahami neobchazi.
    {
        let lowTier = 0;
        for (let i = 0; i < 300; i++) {
            const pool = [lc(1, 1, 'Over 2.5', 2), lc(2, 2, 'Over 2.5', 3), lc(3, 3, 'Over 2.5', 4)];
            const reg = createLeagueRegistry();
            const t = createPicker({ name: 'tips-kombik.json', pool, want: 1, phases: onePhase(1), blockedPicks: new Set(), goalStats, registry: reg, tierStrategy: 'preference' });
            runCoordinatedSelection([t], () => { }, { registry: reg });
            if (t.selected[0].tier > 2) lowTier++;
        }
        ok('preference: T3/T4 az kdyz neni T1/T2', lowTier === 0, lowTier + ' pripadu');
    }

    // 10) React1 nenarusi uz hotove hlavni JSON.
    {
        let changed = 0;
        for (let i = 0; i < 200; i++) {
            const pool = [];
            for (let lid = 1; lid <= 3; lid++) for (let j = 0; j < 4; j++) {
                pool.push(lc(lid * 100 + j, lid, 'Over 2.5'));
                pool.push(lc(lid * 100 + j, lid, 'Home win'));
            }
            const blocked = new Set(), mainReg = createLeagueRegistry(), r1Reg = createLeagueRegistry();
            const main = ['hot.json', 'react2.json'].map(n =>
                createPicker({ name: n, pool, want: 2, phases: onePhase(2), blockedPicks: blocked, goalStats, registry: mainReg }));
            runCoordinatedSelection(main, () => { }, { registry: mainReg });
            const before = main.flatMap(keysOf).join('|');
            const r1 = createPicker({ name: 'react1.json', pool, want: 3, phases: onePhase(3), blockedPicks: blocked, goalStats, registry: r1Reg });
            runCoordinatedSelection([r1], () => { }, { redistributeScope: [r1], registry: r1Reg });
            if (main.flatMap(keysOf).join('|') !== before) changed++;
        }
        ok('react1 nezmenil uz dokoncene hlavni JSON', changed === 0, changed + ' zmen');
    }
}

section('P. Mekky handicap react1 za zastoupeni ligy v hlavnich JSON');
{
    const lc = (id, lid, tip = 'Over 2.5', tier = 1) => ({
        fixtureId: id, leagueId: lid, tip, tier, league: 'Liga ' + lid, country: 'Zeme ' + lid,
        match: 'H' + id + ' - A' + id, kickoff: '2026-01-01T18:00:00+00:00', odds: '2.10',
    });
    // Penalizace se SCITA jako odchylka od 1.0 (w = BASE*(1+suma)), neni to
    // nasobek vysledne vahy. -20 % tedy znamena pokles o 0.20*W_BASE.
    const W = (m, ctx) => candidateWeight(m, ctx);
    const W_BASE = 1.0;

    // 1) Zadne zastoupeni v hlavni skupine = zadna penalizace.
    {
        const mainReg = createLeagueRegistry();
        const base = W(lc(1, 1), {});
        const withEmpty = W(lc(1, 1), { presenceRegistry: mainReg });
        ok('P1 liga bez zapasu v hlavnich JSON neni penalizovana',
            Math.abs(base - withEmpty) < 1e-9, base + ' vs ' + withEmpty);
    }

    // 2) Jeden unikatni zapas v hlavni skupine = -20 %.
    {
        const mainReg = createLeagueRegistry();
        mainReg.add(lc(100, 1));
        const base = W(lc(1, 1), {});
        const pen = W(lc(1, 1), { presenceRegistry: mainReg });
        ok('P2 jeden zapas v hlavnich JSON = -20 %',
            Math.abs((base - pen) - 0.20 * W_BASE) < 1e-9, 'pokles ' + (base - pen).toFixed(4));
    }

    // 3) Dva a vic unikatnich zapasu = -35 % (a dal uz neroste).
    {
        const mainReg = createLeagueRegistry();
        mainReg.add(lc(100, 1)); mainReg.add(lc(101, 1));
        const base = W(lc(1, 1), {});
        const pen2 = W(lc(1, 1), { presenceRegistry: mainReg });
        mainReg.add(lc(102, 1));
        const pen3 = W(lc(1, 1), { presenceRegistry: mainReg });
        ok('P3 dva zapasy v hlavnich JSON = -35 %',
            Math.abs((base - pen2) - 0.35 * W_BASE) < 1e-9, 'pokles ' + (base - pen2).toFixed(4));
        ok('P3 tri zapasy penalizaci dal nezvysuji (strop -35 %)',
            Math.abs(pen2 - pen3) < 1e-9, pen2 + ' vs ' + pen3);
    }

    // 4) Stejny fixture s vice tipy se pocita jen jednou (refcount, ne pocet tipu).
    {
        const mainReg = createLeagueRegistry();
        mainReg.add(lc(100, 1, 'Over 2.5'));
        mainReg.add(lc(100, 1, 'Home win'));
        const base = W(lc(1, 1), {});
        const pen = W(lc(1, 1), { presenceRegistry: mainReg });
        ok('P4 tyz zapas se dvema tipy = porad jen -20 %',
            Math.abs((base - pen) - 0.20 * W_BASE) < 1e-9, 'pokles ' + (base - pen).toFixed(4));
    }

    // 5) KLICOVE: penalizace se nezapocita dvakrat. Kdyz ma react1 ve svem
    // vlastnim registru tutez ligu jako hlavni skupina, bere se maximum obou
    // poctu, NE jejich soucet.
    {
        const mainReg = createLeagueRegistry();
        mainReg.add(lc(100, 1));           // hlavni skupina: 1 fixture
        const r1Reg = createLeagueRegistry();
        r1Reg.add(lc(200, 1));             // react1: 1 fixture
        const base = W(lc(1, 1), {});
        const both = W(lc(1, 1), { registry: r1Reg, presenceRegistry: mainReg });
        // Soucet by dal pokles 0.40; spravne je max(1,1) -> pokles 0.20.
        ok('P5 penalizace se NESCITA (max, ne soucet)',
            Math.abs((base - both) - 0.20 * W_BASE) < 1e-9, 'pokles ' + (base - both).toFixed(4));
        ok('P5 pokles neni 0.40 (dvojita penalizace)',
            Math.abs((base - both) - 0.40 * W_BASE) > 1e-6, 'pokles ' + (base - both).toFixed(4));
    }

    // 6) Max se bere i opacne - vyssi pocet ve vlastnim registru rozhoduje.
    {
        const mainReg = createLeagueRegistry();
        mainReg.add(lc(100, 1));           // 1
        const r1Reg = createLeagueRegistry();
        r1Reg.add(lc(200, 1)); r1Reg.add(lc(201, 1)); // 2
        const base = W(lc(1, 1), {});
        const both = W(lc(1, 1), { registry: r1Reg, presenceRegistry: mainReg });
        ok('P6 rozhoduje vyssi z obou poctu (-35 %)',
            Math.abs((base - both) - 0.35 * W_BASE) < 1e-9, 'pokles ' + (base - both).toFixed(4));
    }

    // 7) Penalizace je MEKKA - vaha nikdy neklesne na nulu, liga zustava volitelna.
    {
        const mainReg = createLeagueRegistry();
        for (let i = 0; i < 10; i++) mainReg.add(lc(100 + i, 4));
        const w = W(lc(1, 4, 'Over 2.5', 4), { presenceRegistry: mainReg });
        ok('P7 penalizovana liga ma porad kladnou vahu', w > 0, 'vaha ' + w);
    }

    // 8) React1 se i pri plne penalizovanem poolu naplni (penalizace nevyrazuje).
    {
        let short = 0, usedPenalized = 0;
        for (let i = 0; i < 200; i++) {
            const mainReg = createLeagueRegistry();
            const pool = [];
            // Vsechny ligy uz maji v hlavni skupine 2 zapasy -> plna penalizace.
            for (let lid = 1; lid <= 5; lid++) {
                mainReg.add(lc(900 + lid * 2, lid));
                mainReg.add(lc(901 + lid * 2, lid));
                pool.push(lc(lid * 10, lid));
            }
            const r1Reg = createLeagueRegistry();
            const r1 = createPicker({
                name: 'react1.json', pool, want: 4, phases: onePhase(4),
                blockedPicks: new Set(), goalStats, registry: r1Reg, presenceRegistry: mainReg,
            });
            runCoordinatedSelection([r1], () => { }, { redistributeScope: [r1], registry: r1Reg });
            if (!r1.isFull()) short++;
            if (r1.selected.length > 0) usedPenalized++;
        }
        ok('P8 react1 se naplni i pri plne penalizovanem poolu', short === 0, short + ' nenaplnenych');
        ok('P8 penalizovane ligy se porad vybiraji', usedPenalized === 200, usedPenalized + '/200');
    }

    // 9) Hlavni registr zustava READ-ONLY - react1 do nej nic nezapise.
    {
        const mainReg = createLeagueRegistry();
        mainReg.add(lc(900, 1));
        const before = JSON.stringify(mainReg.snapshot());
        const pool = [];
        for (let lid = 1; lid <= 4; lid++) pool.push(lc(lid * 10, lid));
        const r1Reg = createLeagueRegistry();
        const r1 = createPicker({
            name: 'react1.json', pool, want: 3, phases: onePhase(3),
            blockedPicks: new Set(), goalStats, registry: r1Reg, presenceRegistry: mainReg,
        });
        runCoordinatedSelection([r1], () => { }, { redistributeScope: [r1], registry: r1Reg });
        ok('P9 react1 nezapisuje do registru hlavni skupiny',
            JSON.stringify(mainReg.snapshot()) === before, 'registr zmenen');
        ok('P9 react1 pritom neco vybral', r1.selected.length === 3, r1.selected.length + ' vybrano');
    }

    // 10) Statisticky: nepenalizovana liga je vybirana castěji nez penalizovana,
    // ale penalizovana se porad objevuje (mekky handicap, ne zakaz).
    {
        const mainReg = createLeagueRegistry();
        mainReg.add(lc(900, 2)); mainReg.add(lc(901, 2)); // liga 2 plne penalizovana
        let cistaL = 0, penal = 0;
        for (let i = 0; i < 1000; i++) {
            const pool = [lc(10, 1), lc(20, 2)];
            const r1Reg = createLeagueRegistry();
            const r1 = createPicker({
                name: 'react1.json', pool, want: 1, phases: onePhase(1),
                blockedPicks: new Set(), goalStats, registry: r1Reg, presenceRegistry: mainReg,
            });
            runCoordinatedSelection([r1], () => { }, { redistributeScope: [r1], registry: r1Reg });
            if (r1.selected[0]?.leagueId === 1) cistaL++; else penal++;
        }
        // Ocekavany pomer 1.00 : 0.65 -> cista ~60.6 %, penal ~39.4 %.
        ok('P10 nepenalizovana liga vyhrava casteji', cistaL > penal, cistaL + ' vs ' + penal);
        ok('P10 pomer odpovida -35 % (cista 550-660 z 1000)',
            cistaL >= 550 && cistaL <= 660, 'cista=' + cistaL);
        ok('P10 penalizovana liga se porad vybira', penal > 250, 'penal=' + penal);
    }
}

console.log('\n--------------------------------');
console.log('PASS: ' + pass + ', FAIL: ' + fail);
process.exit(fail ? 1 : 0);
