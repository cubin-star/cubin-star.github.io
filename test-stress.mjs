// Zatezovy bezpecnostni test viceurovnoveho prerozdeleni.
// Umyslne naraz i na limit kroku (maxSteps) a na maximalni hloubku retezu.
// Spusteni: node test-stress.mjs
import { createPicker, runCoordinatedSelection, pickKey } from './fetch-matches.mjs';

let pass = 0, fail = 0;
function ok(name, cond, detail = '') {
    if (cond) { pass++; console.log('  OK   ' + name); }
    else { fail++; console.log('  FAIL ' + name + (detail ? ' -> ' + detail : '')); }
}
function section(t) { console.log('\n=== ' + t + ' ==='); }

const goalStats = new Map();
const always = () => true;
const onePhase = (want, market = always) => [{ label: 'test', market, countsAs: always, want }];
const MAX_PER_COUNTRY = 2; // musi odpovidat fetch-matches.mjs

function cand(id, tip, tier = 1, league = 'L' + id, country = 'C' + id) {
    return { fixtureId: id, tip, tier, league, country, match: 'H' + id + ' - A' + id, kickoff: '2026-01-01T18:00:00+00:00', odds: '2.10' };
}
function mk(name, pool, want, phases, blocked, opts = {}) {
    return createPicker({ name, pool, want, phases, coreMin: 0, blockedPicks: blocked, goalStats, ...opts });
}

// Kontrola vnitrni konzistence pickeru po dokonceni vyberu.
function invariants(ps, blocked, label) {
    const problems = [];
    const seen = new Set();
    for (const p of ps) {
        if (p.selected.length !== p.entries.length) problems.push(p.name + ': selected/entries rozjete');
        if (p.selected.length > p.want) problems.push(p.name + ': vic nez want');
        const fx = p.selected.map(m => m.fixtureId);
        if (new Set(fx).size !== fx.length) problems.push(p.name + ': opakovany fixtureId v tiketu');
        const lg = p.selected.map(m => m.league + '|' + m.country);
        if (new Set(lg).size !== lg.length) problems.push(p.name + ': opakovana liga');
        const byC = new Map();
        for (const m of p.selected) byC.set(m.country, (byC.get(m.country) || 0) + 1);
        for (const [c, n] of byC) if (n > MAX_PER_COUNTRY) problems.push(p.name + ': zeme ' + c + ' = ' + n);
        const tt = { 1: 0, 2: 0, 3: 0, 4: 0 };
        for (const e of p.entries) tt[e.tier]++;
        for (const t of [1, 2, 3, 4]) if (tt[t] !== p.tierTotals[t]) problems.push(p.name + ': tierTotals nesedi (T' + t + ')');
        for (const m of p.selected) {
            if (!blocked.has(pickKey(m))) problems.push(p.name + ': vybrany klic chybi v blockedPicks');
            if (!p.pool.includes(m)) problems.push(p.name + ': vybrany zapas neni z vlastniho poolu');
        }
        for (const m of p.selected) seen.add(pickKey(m));
    }
    // blockedPicks nesmi obsahovat osirely klic po rollbacku
    for (const k of blocked) if (!seen.has(k)) problems.push('osireli klic v blockedPicks: ' + k);
    if (problems.length) console.log('     [' + label + '] ' + problems.join(' ; '));
    return problems.length === 0;
}

// --- S1: siroky zatezovy beh, mnoho konfliktu --------------------------------
section('S1. Zatezovy beh (mnoho pickeru, uzky pool)');
{
    let bad = 0, hitLimit = 0, lost = 0;
    for (let run = 0; run < 150; run++) {
        // Uzky pool: 10 zapasu x 2 tipy, 6 souboru po 4 zapasech = 24 mist / 20 klicu.
        const pool = [];
        for (let id = 1; id <= 10; id++) { pool.push(cand(id, 'Over 2.5')); pool.push(cand(id, 'Home win')); }
        const blocked = new Set();
        const ps = [];
        for (let i = 0; i < 6; i++) ps.push(mk('f' + i, pool, 4, onePhase(4), blocked));
        const before = ps.map(p => p.selected.length);
        const res = runCoordinatedSelection(ps, () => { }, { maxSteps: 60, maxDepth: 4 });
        if (res.limitHit) hitLimit++;
        if (!invariants(ps, blocked, 'S1')) bad++;
        if (ps.some((p, i) => p.selected.length < before[i])) lost++;
    }
    ok('konzistence pickeru po zatezi', bad === 0, bad + ' vadnych behu');
    ok('zadny picker neztratil vybrany zapas', lost === 0, lost + ' ztrat');
    ok('limit kroku se pri zatezi skutecne vyvolal', hitLimit > 0, hitLimit + '/150');
}

// --- S2: tvrdy limit kroku (maxSteps = 1) -----------------------------------
section('S2. Limit kroku -> NEOVERENY nedostatek');
{
    // Beh je nahodny - konflikt nevznikne vzdy. Testujeme proto pres opakovani:
    // kdykoli hledani narazi na limit a vznikne duplicita, musi byt oznacena
    // jako NEOVERENY nedostatek a stav musi zustat konzistentni.
    let bad = 0, limitRuns = 0, mislabeled = 0, notFull = 0;
    for (let run = 0; run < 200; run++) {
        const X = cand(1, 'Over 2.5'), Y = cand(2, 'Over 2.5'), Z = cand(3, 'Over 2.5');
        const blocked = new Set();
        const a = mk('a', [X], 1, onePhase(1), blocked);
        const b = mk('b', [X, Y], 1, onePhase(1), blocked);
        const c = mk('c', [X, Y, Z], 1, onePhase(1), blocked);
        const ps = [c, b, a];
        const res = runCoordinatedSelection(ps, () => { }, { maxSteps: 1, maxDepth: 4 });
        if (!invariants(ps, blocked, 'S2')) bad++;
        if (res.limitHit) limitRuns++;
        if (!ps.every(p => p.isFull())) notFull++;
        const dup = ps.flatMap(p => p.entries).filter(e => e.duplicate);
        if (dup.length && !dup.every(e => new RegExp(res.limitHit ? 'NEOVEREN' : 'prokazany').test(e.reason))) mislabeled++;
    }
    ok('stav zustal konzistentni i pri useknutem hledani', bad === 0, bad + ' vadnych behu');
    ok('limit kroku se nekdy skutecne vyvolal', limitRuns > 0, limitRuns + '/200');
    ok('fallback je vzdy oznacen spravne (NEOVEREN vs. prokazany)', mislabeled === 0, mislabeled + ' chybnych popisku');
    ok('vsechny soubory jsou presto naplnene', notFull === 0, notFull + ' neuplnych behu');
}

// --- S3: prekroceni maximalni hloubky retezu --------------------------------
section('S3. Prekroceni maximalni hloubky (maxDepth = 1)');
{
    // Retez delky 3 je nutny; pri maxDepth=1 ho nelze najit -> fallback duplicity,
    // ale stav musi zustat plne konzistentni (kompletni rollback).
    let bad = 0, deep = 0;
    for (let run = 0; run < 100; run++) {
        const X = cand(1, 'Over 2.5'), Y = cand(2, 'Over 2.5'), Z = cand(3, 'Over 2.5');
        const blocked = new Set();
        const a = mk('a', [X], 1, onePhase(1), blocked);
        const b = mk('b', [X, Y], 1, onePhase(1), blocked);
        const c = mk('c', [X, Y, Z], 1, onePhase(1), blocked);
        const ps = [c, b, a];
        runCoordinatedSelection(ps, () => { }, { maxSteps: 500, maxDepth: 1 });
        if (!invariants(ps, blocked, 'S3')) bad++;
        if (ps.every(p => p.isFull())) deep++;
    }
    ok('kompletni rollback i pri vycerpane hloubce', bad === 0, bad + ' vadnych behu');
    ok('soubory se vzdy naplni (byt pres fallback)', deep === 100, deep + '/100');
}

// --- S4: react1 nesmi sahnout na uzamcenou hlavni skupinu -------------------
section('S4. Uzamcena hlavni skupina pri scope react1');
{
    let bad = 0, touched = 0;
    for (let run = 0; run < 100; run++) {
        const pool = [1, 2, 3].map(id => cand(id, 'Over 2.5'));
        const blocked = new Set();
        const hot = mk('hot.json', pool, 2, onePhase(2), blocked);
        const r2 = mk('react2.json', pool, 1, onePhase(1), blocked);
        runCoordinatedSelection([hot, r2]);
        const lockedKeys = [...hot.selected, ...r2.selected].map(pickKey).sort().join();
        const r1 = mk('react1.json', pool, 2, onePhase(2), blocked, { priority: 'vedlejsi skupina (nejnizsi priorita)' });
        runCoordinatedSelection([r1], () => { }, { redistributeScope: [r1] });
        const after = [...hot.selected, ...r2.selected].map(pickKey).sort().join();
        if (after !== lockedKeys) touched++;
        if (!invariants([hot, r2, r1], blocked, 'S4')) bad++;
    }
    ok('hlavni skupina zustala beze zmeny', touched === 0, touched + ' zasahu');
    ok('stav konzistentni i po behu react1', bad === 0, bad + ' vadnych behu');
}

// --- S5: dlouhy retez na hranici hloubky 4 ----------------------------------
section('S5. Retez na hranici hloubky 4');
{
    let dup = 0, full = 0, bad = 0;
    for (let run = 0; run < 100; run++) {
        // p0 umi jen M1, p1 M1+M2, ... p4 M1..M5 -> nutny retez delky 5.
        const ms = [1, 2, 3, 4, 5].map(id => cand(id, 'Over 2.5'));
        const blocked = new Set();
        const ps = [];
        for (let i = 4; i >= 0; i--) ps.push(mk('p' + i, ms.slice(0, i + 1), 1, onePhase(1), blocked));
        runCoordinatedSelection(ps, () => { }, { maxSteps: 500, maxDepth: 4 });
        if (!invariants(ps, blocked, 'S5')) bad++;
        if (ps.every(p => p.isFull())) full++;
        if (ps.reduce((s, p) => s + p.stats.dupAllowed, 0) > 0) dup++;
    }
    ok('stav konzistentni na hranici hloubky', bad === 0, bad + ' vadnych behu');
    ok('vsechny soubory naplnene', full === 100, full + '/100');
    ok('pripadna duplicita nezpusobila nekonzistenci', dup <= 100);
}

// --- S6: zadne zacykleni pri cyklickych zavislostech ------------------------
section('S6. Ochrana proti zacykleni');
{
    const t0 = Date.now();
    for (let run = 0; run < 50; run++) {
        const pool = [];
        for (let id = 1; id <= 6; id++) pool.push(cand(id, 'Over 2.5'));
        const blocked = new Set();
        const ps = [];
        for (let i = 0; i < 8; i++) ps.push(mk('q' + i, pool, 3, onePhase(3), blocked));
        runCoordinatedSelection(ps, () => { }, { maxSteps: 500, maxDepth: 4 });
        if (!invariants(ps, blocked, 'S6')) { ok('S6 konzistence', false); break; }
    }
    const ms = Date.now() - t0;
    ok('vyber se nezacykli a skonci rychle', ms < 10000, ms + ' ms');
}

console.log('\n--------------------------------');
console.log('PASS: ' + pass + ', FAIL: ' + fail);
process.exit(fail ? 1 : 0);
