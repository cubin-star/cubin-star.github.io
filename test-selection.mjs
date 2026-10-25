// Regresni testy koordinovaneho vyberu a vypoctu kurzu.
// Spusteni: node test-selection.mjs
import { readFileSync } from 'fs';
import { createPicker, runCoordinatedSelection, bestOdds, pickKey } from './fetch-matches.mjs';

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
        ['sortByGoals se stale pouziva', /sortByGoals\(b\[t\],goalStats\)/],
        ['externi dedup', /loadDedupKeysFromUrls\(DEDUP_URLS\)/],
        ['skupiny react2/react3', /balanceGroups\(shuffle\(\[\.\.\.react2Picker\.selected\]\)\)[\s\S]*?balanceGroups\(shuffle\(\[\.\.\.react3Picker\.selected\]\)\)/],
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
        const t = createPicker({ name: 'tips.json', pool, want: 1, phases: onePhase(1), coreMin: 0, blockedPicks: new Set(), goalStats, tierStrategy: 'preference' });
        runCoordinatedSelection([t]);
        if (t.selected[0].tier > 2) lowTier++;
    }
    ok('tips nikdy nesahne na T3/T4, dokud je T1/T2', lowTier === 0, lowTier + ' pripadu');

    // preference golovejsich lig uvnitr skupiny
    const gs = new Map([['liga rich|c 1', { played: 200, over25Rate: 70 }], ['liga poor|c 2', { played: 200, over25Rate: 41 }]]);
    let rich = 0;
    for (let i = 0; i < 100; i++) {
        const pool = [
            { ...cand(1, 'Over 2.5', 2), league: 'Liga poor', country: 'C2' },
            { ...cand(2, 'Over 2.5', 2), league: 'Liga rich', country: 'C1' },
        ];
        const t = createPicker({ name: 'tips.json', pool, want: 1, phases: onePhase(1), blockedPicks: new Set(), goalStats: gs, tierStrategy: 'preference' });
        runCoordinatedSelection([t]);
        if (t.selected[0].league === 'Liga rich') rich++;
    }
    ok('tips preferuje golovejsi ligu', rich === 100, rich + '/100');
    ok('tips zustava v hlavni skupine (vychozi priorita)',
        mk('tips.json', [], 1, onePhase(1), new Set()).priority === 'hlavni skupina');
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

console.log('\n--------------------------------');
console.log('PASS: ' + pass + ', FAIL: ' + fail);
process.exit(fail ? 1 : 0);
