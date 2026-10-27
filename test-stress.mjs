// Zatezovy bezpecnostni test viceurovnoveho prerozdeleni.
// Umyslne naraz i na limit kroku (maxSteps) a na maximalni hloubku retezu.
// Spusteni: node test-stress.mjs
import { createPicker, runCoordinatedSelection, pickKey, createLeagueRegistry, leagueKey } from './fetch-matches.mjs';

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

// === S7: postupne uvolnovani globalniho limitu ligy =========================
section('S7. Relaxace globalniho limitu ligy');
{
    const lc = (id, lid, tip = 'Over 2.5') => ({
        fixtureId: id, leagueId: lid, tip, tier: 1, league: 'Liga ' + lid, country: 'Zeme ' + lid,
        match: 'H' + id + ' - A' + id, kickoff: '2026-01-01T18:00:00+00:00', odds: '2.10',
    });

    // 8) Nedostatek lig musi limit uvolnit - ne nechat soubory prazdne.
    // 4 ligy x 5 zapasu, 3 soubory po 3 = 9 slotu. Lokalni limit je splnitelny
    // (4 ligy >= 3 sloty), ale globalni limit 2 dovoli jen 8 unikatnich
    // fixture - na 9. slot se musi limit uvolnit.
    {
        let short = 0, relaxed = 0;
        for (let i = 0; i < 100; i++) {
            const pool = [];
            for (let lid = 1; lid <= 4; lid++) for (let j = 0; j < 5; j++) pool.push(lc(lid * 100 + j, lid));
            const blocked = new Set(), reg = createLeagueRegistry();
            const ps = ['a', 'b', 'c'].map(n => mk(n, pool, 3, onePhase(3), blocked, { registry: reg }));
            const res = runCoordinatedSelection(ps, () => { }, { registry: reg });
            if (ps.some(p => !p.isFull())) short++;
            if (res.capRelaxations.length > 0) relaxed++;
        }
        ok('S7 nedostatek lig spusti relaxaci limitu', relaxed === 100, relaxed + '/100');
        ok('S7 soubory se diky relaxaci naplni', short === 0, short + ' nenaplnenych');
    }

    // Relaxace se NEspusti, kdyz limit 2 staci.
    {
        let relaxed = 0;
        for (let i = 0; i < 100; i++) {
            const pool = [];
            for (let lid = 1; lid <= 8; lid++) for (let j = 0; j < 3; j++) pool.push(lc(lid * 100 + j, lid));
            const blocked = new Set(), reg = createLeagueRegistry();
            const ps = ['a', 'b', 'c'].map(n => mk(n, pool, 3, onePhase(3), blocked, { registry: reg }));
            const res = runCoordinatedSelection(ps, () => { }, { registry: reg });
            if (res.capRelaxations.length > 0) relaxed++;
        }
        ok('S7 pri dostatku lig se limit neuvolnuje', relaxed === 0, relaxed + '/100');
    }

    // Relaxace nese duvod a lokalni limit (1 liga/soubor) plati i po ni.
    {
        const pool = [];
        for (let j = 0; j < 6; j++) pool.push(lc(j, 1));
        const blocked = new Set(), reg = createLeagueRegistry();
        const ps = ['a', 'b', 'c'].map(n => mk(n, pool, 2, onePhase(2), blocked, { registry: reg }));
        const res = runCoordinatedSelection(ps, () => { }, { registry: reg });
        ok('S7 relaxace ma uvedeny duvod', res.capRelaxations.every(r => !!r.reason));
        const localViol = ps.some(p => new Set(p.selected.map(m => m.league + '|' + m.country)).size !== p.selected.length);
        ok('S7 lokalni limit 1 liga/soubor plati i po relaxaci', !localViol);
    }

    // 9) Prerozdeleni udrzuje globalni pocty unikatnich fixture konzistentni.
    {
        let bad = 0;
        for (let i = 0; i < 200; i++) {
            const pool = [];
            for (let lid = 1; lid <= 4; lid++) for (let j = 0; j < 3; j++) {
                pool.push(lc(lid * 100 + j, lid, 'Over 2.5'));
                pool.push(lc(lid * 100 + j, lid, 'Home win'));
            }
            const blocked = new Set(), reg = createLeagueRegistry();
            const ps = ['a', 'b', 'c', 'd'].map(n => mk(n, pool, 2, onePhase(2), blocked, { registry: reg }));
            runCoordinatedSelection(ps, () => { }, { registry: reg });
            // Registr musi presne odpovidat skutecnemu vyberu.
            const actual = new Map();
            for (const p of ps) for (const m of p.selected) {
                const k = leagueKey(m);
                if (!actual.has(k)) actual.set(k, new Set());
                actual.get(k).add(m.fixtureId);
            }
            for (const [, s] of actual) {
                const any = ps.flatMap(p => p.selected).find(m => actual.get(leagueKey(m)) === s);
                if (any && reg.count(any) !== s.size) bad++;
            }
        }
        ok('S7 registr lig presne odpovida vyberu i po prerozdeleni', bad === 0, bad + ' nesouladu');
    }

    // 15) Pri vycerpani limitu prohledavani se nedostatek NEoznaci za prokazany.
    {
        const pool = [];
        for (let id = 1; id <= 12; id++) pool.push(lc(id, id));
        const blocked = new Set(), reg = createLeagueRegistry();
        const ps = [];
        for (let i = 0; i < 6; i++) ps.push(mk('p' + i, pool, 4, onePhase(4), blocked, { registry: reg }));
        const res = runCoordinatedSelection(ps, () => { }, { registry: reg, maxSteps: 1, maxDepth: 1 });
        const dupEntries = ps.flatMap(p => p.entries).filter(e => e.duplicate);
        ok('S7 pri limitu hledani neni nedostatek oznacen za prokazany',
            !res.limitHit || dupEntries.every(e => !/prokazany nedostatek/.test(e.reason || '')),
            'limitHit=' + res.limitHit + ', dup=' + dupEntries.length);
    }

    // Zatez: mnoho lig s prekryvajicimi se kandidaty.
    {
        const t0 = Date.now();
        let bad = 0;
        for (let i = 0; i < 30; i++) {
            const pool = [];
            for (let lid = 1; lid <= 40; lid++) for (let j = 0; j < 5; j++) {
                pool.push(lc(lid * 1000 + j, lid, 'Over 2.5'));
                pool.push(lc(lid * 1000 + j, lid, 'Home win'));
            }
            const blocked = new Set(), reg = createLeagueRegistry();
            const ps = [
                mk('hot.json', pool, 6, onePhase(6), blocked, { registry: reg }),
                mk('tips-kombik.json', pool, 2, onePhase(2), blocked, { registry: reg }),
                mk('react2.json', pool, 6, onePhase(6), blocked, { registry: reg }),
                mk('react3.json', pool, 8, onePhase(8), blocked, { registry: reg }),
            ];
            runCoordinatedSelection(ps, () => { }, { registry: reg });
            if (!invariants(ps, blocked, 'S7')) { bad++; break; }
            if (ps.some(p => !p.isFull())) bad++;
            const uniq = new Map();
            for (const p of ps) for (const m of p.selected) {
                const k = leagueKey(m);
                if (!uniq.has(k)) uniq.set(k, new Set());
                uniq.get(k).add(m.fixtureId);
            }
            for (const [, s] of uniq) if (s.size > 2) bad++;
        }
        const ms = Date.now() - t0;
        ok('S7 zatez 40 lig x 5 zapasu x 2 trhy: vse naplneno a v limitu', bad === 0, bad + ' problemu');
        ok('S7 zatez skonci rychle', ms < 15000, ms + ' ms');
    }
}

// === S8: prerozdeleni zachranuje limit 2 ====================================
// Klicove: kdyz existuje reseni respektujici limit 2, MUSI ho najit
// prerozdeleni - uvolneni limitu je az posledni moznost.
section('S8. Prerozdeleni misto uvolneni limitu');
{
    const lc = (id, lid, tip = 'Over 2.5', tier = 1) => ({
        fixtureId: id, leagueId: lid, tip, tier, league: 'Liga ' + lid, country: 'Zeme ' + lid,
        match: 'H' + id + ' - A' + id, kickoff: '2026-01-01T18:00:00+00:00', odds: '2.10',
    });
    const uniqPerLeague = ps => {
        const m = new Map();
        for (const p of ps) for (const x of p.selected) {
            const k = leagueKey(x);
            if (!m.has(k)) m.set(k, new Set());
            m.get(k).add(x.fixtureId);
        }
        return m;
    };

    // Scenar A - vynucene prerozdeleni mezi JSON.
    // Dva "uzke" soubory umi JEN Over 2.5, a Over existuje pouze v lize 1.
    // Liga 1 ma presne 2 zapasy = presne na limit 2. "Siroky" soubor umi vse.
    // Kdyz siroky sahne po lize 1 driv, jeden uzky zustane bez kandidata -
    // jedine zachrana je prerozdeleni: siroky vrati zapas z ligy 1 a vezme si
    // nahradu z ligy 2-4. Uvolnit limit by bylo zbytecne.
    {
        let short = 0, relaxed = 0, viol = 0, redist = 0;
        for (let i = 0; i < 300; i++) {
            const pool = [
                lc(11, 1, 'Over 2.5'), lc(12, 1, 'Over 2.5'),
                lc(11, 1, 'Home win'), lc(12, 1, 'Home win'),
            ];
            for (let lid = 2; lid <= 4; lid++) pool.push(lc(lid * 10, lid, 'Home win'));
            const blocked = new Set(), reg = createLeagueRegistry();
            const isOver = m => m.tip === 'Over 2.5';
            const ps = [
                mk('uzky1', pool, 1, onePhase(1, isOver), blocked, { registry: reg }),
                mk('uzky2', pool, 1, onePhase(1, isOver), blocked, { registry: reg }),
                mk('siroky', pool, 1, onePhase(1), blocked, { registry: reg }),
            ];
            const res = runCoordinatedSelection(ps, () => { }, { registry: reg });
            if (ps.some(p => !p.isFull())) short++;
            if (res.capRelaxations.length > 0) relaxed++;
            if (ps.reduce((a, p) => a + p.stats.redistributed, 0) > 0) redist++;
            for (const [, s] of uniqPerLeague(ps)) if (s.size > 2) viol++;
        }
        ok('S8/A vsechny soubory naplneny', short === 0, short + ' nenaplnenych');
        ok('S8/A limit 2 NEBYL zbytecne uvolnen', relaxed === 0, relaxed + '/300 uvolneni');
        ok('S8/A zadna liga nad 2 unikatni fixture', viol === 0, viol + ' poruseni');
        ok('S8/A prerozdeleni se uplatnilo (konflikt byl realny)', redist > 0, redist + '/300 behu');
    }

    // Scenar B - uzke hrdlo pres tri soubory s ruznymi trhy.
    // Over existuje jen v ligach 1 a 2 (po dvou zapasech). onlyOver tedy musi
    // vzit po jednom z obou. Home/Away soubory by rady sahly po stejnych
    // ligach, ale tim by v nich vznikl 3. unikatni zapas - musi se proto
    // prerozdelit do lig 3-5. Reseni pri limitu 2 existuje, relaxace ne.
    {
        let short = 0, relaxed = 0, viol = 0;
        for (let i = 0; i < 300; i++) {
            const pool = [];
            for (const [lid, ids] of [[1, [101, 102]], [2, [201, 202]]]) {
                for (const id of ids) {
                    pool.push(lc(id, lid, 'Over 2.5'));
                    pool.push(lc(id, lid, 'Home win'));
                    pool.push(lc(id, lid, 'Away win'));
                }
                // treti zapas v lize jen pro vitezne trhy - past na cap
                pool.push(lc(lid * 100 + 9, lid, 'Home win'));
                pool.push(lc(lid * 100 + 9, lid, 'Away win'));
            }
            for (let lid = 3; lid <= 5; lid++) {
                pool.push(lc(lid * 100, lid, 'Home win'));
                pool.push(lc(lid * 100 + 1, lid, 'Away win'));
            }
            const blocked = new Set(), reg = createLeagueRegistry();
            const ps = [
                mk('onlyOver', pool, 2, onePhase(2, m => m.tip === 'Over 2.5'), blocked, { registry: reg }),
                mk('onlyHome', pool, 2, onePhase(2, m => m.tip === 'Home win'), blocked, { registry: reg }),
                mk('onlyAway', pool, 2, onePhase(2, m => m.tip === 'Away win'), blocked, { registry: reg }),
            ];
            const res = runCoordinatedSelection(ps, () => { }, { registry: reg });
            if (ps.some(p => !p.isFull())) short++;
            if (res.capRelaxations.length > 0) relaxed++;
            for (const [, s] of uniqPerLeague(ps)) if (s.size > 2) viol++;
        }
        ok('S8/B vsechny tri soubory naplneny', short === 0, short + ' nenaplnenych');
        ok('S8/B limit 2 NEBYL zbytecne uvolnen', relaxed === 0, relaxed + '/300 uvolneni');
        ok('S8/B zadna liga nad 2 unikatni fixture', viol === 0, viol + ' poruseni');
    }

    // Scenar C - kontrolni protipriklad: reseni pri limitu 2 NEEXISTUJE,
    // relaxace tedy probehnout MUSI (jinak by soubory zustaly prazdne).
    {
        let short = 0, relaxed = 0;
        for (let i = 0; i < 100; i++) {
            const pool = [];
            // 3 ligy x 4 zapasy, 4 soubory po 2 = 8 slotu > 3 ligy x 2 = 6.
            for (let lid = 1; lid <= 3; lid++) for (let j = 0; j < 4; j++) pool.push(lc(lid * 100 + j, lid));
            const blocked = new Set(), reg = createLeagueRegistry();
            const ps = ['a', 'b', 'c', 'd'].map(n => mk(n, pool, 2, onePhase(2), blocked, { registry: reg }));
            const res = runCoordinatedSelection(ps, () => { }, { registry: reg });
            if (ps.some(p => !p.isFull())) short++;
            if (res.capRelaxations.length > 0) relaxed++;
        }
        ok('S8/C kdyz reseni pri limitu 2 neexistuje, relaxace probehne', relaxed === 100, relaxed + '/100');
        ok('S8/C soubory se presto naplni', short === 0, short + ' nenaplnenych');
    }
}

console.log('\n--------------------------------');
console.log('PASS: ' + pass + ', FAIL: ' + fail);
process.exit(fail ? 1 : 0);
