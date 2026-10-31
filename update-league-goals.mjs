// Bezpecna aktualizace league-goals.json (golovost soutezi pro fetch-matches.mjs).
//
// Rezimy:
//   --mode=weekly   inkrementalne: /fixtures?date=YYYY-MM-DD pro kazdy novy den
//                   (+ OVERLAP_DAYS dnu prekryvu kvuli opozdenym vysledkum)
//   --mode=monthly  plna korekce aktivnich lig: /leagues?current=true +
//                   /fixtures?league=X&season=Y&status=FT pro kazdou ligu
//                   (stejne vypocty jako puvodni measure-goals.mjs)
//
// Vystup:
//   (bez prepinace)   dry-run - nic nezapisuje, jen vypise souhrn
//   --out-dir=DIR     zapise NAHLED do DIR (produkcni soubory nemeni)
//   --write           atomicky prepise league-goals.json + league-goals-meta.json
//
// Bezpecnost:
//   - pri jakekoli chybe API nebo neuspesne validaci se NIC nezapise
//   - lastProcessedDate se posune az po uspesne validaci -> opakovani po
//     vypadku je bezpecne, prekryv dnu + fixture ID zabrani dvojimu zapocteni
//   - liga se nikdy nesmaze jen proto, ze nema nova data
//   - sezony se pocitaji oddelene; nova sezona se publikuje az od 30 zapasu
//
// Format league-goals.json zustava PRESNE stejny (pole se 12 klici), takze
// fetch-matches.mjs se nemusi menit. Pocitadla a historie jsou ve
// league-goals-meta.json.

import { readFileSync, writeFileSync, existsSync, renameSync, mkdirSync, unlinkSync } from 'fs';
import { join } from 'path';
import { leagueTier } from './fetch-matches.mjs';

export const CONFIG = {
    API: 'https://v3.football.api-sports.io',
    USER_AGENT: 'kombik-bot/1.0 (+github-actions)',
    GOALS_FILE: 'league-goals.json',
    META_FILE: 'league-goals-meta.json',
    // Kolik uz zpracovanych dnu se znovu stahne kvuli opozdene dohranym zapasum.
    OVERLAP_DAYS: 3,
    // Vetsi mezera = nekdo dlouho nespoustel aktualizaci, bezpecnejsi je plna korekce.
    MAX_GAP_DAYS: 21,
    // Nova sezona se publikuje az od tohoto poctu dohranych zapasu.
    NEW_SEASON_MIN: 30,
    // Jak dlouho drzet ID zapocitanych zapasu (musi byt > OVERLAP_DAYS).
    SEEN_RETENTION_DAYS: 35,
    // Liga bez zapasu dele nez tolik dni = neaktivni (jen metadata).
    INACTIVE_AFTER_DAYS: 60,
    // Tvrdy strop requestu na jeden beh.
    MAX_REQUESTS: { weekly: 40, monthly: 1100 },
    // Kdyz API hlasi mene zbyvajicich dennich requestu, beh se zastavi
    // (rezerva pro denni produkcni boty).
    MIN_REMAINING: 1500,
    // Vystup nesmi mit mene nez 95 % lig predchozi verze.
    MIN_KEEP_RATIO: 0.95,
    // Maximalni prumerny posun over25Rate (body) u lig s velkym vzorkem.
    MAX_MEAN_SHIFT: { weekly: 5, monthly: 10 },
    // Mesicni korekce: pri vice nez 5 % chybnych lig se nic nezapise.
    MONTHLY_MAX_ERROR_RATIO: 0.05,
    // Mesicni korekce: kdyz API vrati vyrazne mene zapasu nez mame, ponechame stare.
    MONTHLY_MIN_KEEP: 0.9,
    MONTHLY_SLEEP_MS: 450,
    // Nad touto hodnotou nejde o standardni fotbal (Kings League ~11 golu/zapas).
    MAX_AVG_GOALS: 7,
    MAX_GOALS_PER_TEAM: 30,
    HISTORY_LIMIT: 60,
};

const SKIP_COUNTRIES = new Set(['Russia', 'Belarus', 'Ukraine']);
// Nestandardni formaty - jina pravidla, jine skore, nepatri do golove statistiky.
const NONSTANDARD = /\bkings?\s+(league|world\s+cup|cup|nations)\b|\bqueens\s+league\b|\bballer\s+league\b|\bfutsal\b|\bbeach\b|\be-?sports?\b|\besoccer\b|\b[5-7]\s*-?\s*a\s*-?\s*side\b|\bsevens\b/i;

export const OUTPUT_KEYS = ['id', 'name', 'country', 'type', 'tier', 'season', 'played', 'avgGoals', 'over15Rate', 'over25Rate', 'over35Rate', 'bttsRate'];

export class ApiLimitError extends Error { }

// ---------------------------------------------------------------- pomocne
export const isNonStandard = name => NONSTANDARD.test(String(name || ''));
export function isEligible(name, country, type) {
    if (!name || !country) return false;
    if (SKIP_COUNTRIES.has(country)) return false;
    if (isNonStandard(name)) return false;
    return leagueTier(name, country, type) !== 0;
}
export function addDays(d, n) {
    const x = new Date(d + 'T00:00:00Z'); x.setUTCDate(x.getUTCDate() + n);
    return x.toISOString().slice(0, 10);
}
export const diffDays = (a, b) => Math.round((Date.parse(a + 'T00:00:00Z') - Date.parse(b + 'T00:00:00Z')) / 864e5);
const clone = o => JSON.parse(JSON.stringify(o));
const sleep = ms => new Promise(r => setTimeout(r, ms));

// ---------------------------------------------------------------- pocitadla
export const emptyCounters = () => ({ played: 0, goals: 0, o15: 0, o25: 0, o35: 0, btts: 0 });

// Platny dohrany zapas -> {h,a}, jinak null. Stejne jako measure-goals.mjs bere
// jen status FT (odlozene, zrusene, prerusene a nedohrane se nepocitaji).
export function finishedScore(f) {
    if (f?.fixture?.status?.short !== 'FT') return null;
    const h = f?.goals?.home, a = f?.goals?.away, M = CONFIG.MAX_GOALS_PER_TEAM;
    if (!Number.isInteger(h) || !Number.isInteger(a) || h < 0 || a < 0 || h > M || a > M) return null;
    return { h, a };
}
export function addScore(c, { h, a }) {
    const t = h + a;
    c.played++; c.goals += t;
    if (t >= 2) c.o15++;
    if (t >= 3) c.o25++;
    if (t >= 4) c.o35++;
    if (h > 0 && a > 0) c.btts++;
}
export function countersFrom(fixtures) {
    const c = emptyCounters(); let invalid = 0;
    for (const f of fixtures || []) {
        const s = finishedScore(f);
        if (!s) { if (f?.fixture?.status?.short === 'FT') invalid++; continue; }
        addScore(c, s);
    }
    return { c, invalid };
}
// Vnitrni konzistence: 0 <= o35 <= o25 <= o15 <= played, btts <= played a
// goly odpovidaji prahum (zapas s t>=4 prispeje aspon 2+1+1 do souctu).
export function validCounters(c) {
    if (!c) return false;
    for (const k of ['played', 'goals', 'o15', 'o25', 'o35', 'btts']) if (!Number.isInteger(c[k]) || c[k] < 0) return false;
    return c.o35 <= c.o25 && c.o25 <= c.o15 && c.o15 <= c.played && c.btts <= c.played
        && c.goals >= 2 * c.o15 + c.o25 + c.o35;
}
export function toRecord(L, season, c) {
    const pct = v => +(v / c.played * 100).toFixed(1);
    return {
        id: L.id, name: L.name, country: L.country, type: L.type, tier: L.tier, season: Number(season),
        played: c.played, avgGoals: +(c.goals / c.played).toFixed(2),
        over15Rate: pct(c.o15), over25Rate: pct(c.o25), over35Rate: pct(c.o35), bttsRate: pct(c.btts),
    };
}

// ---------------------------------------------------------------- meta
export function emptyMeta() {
    return { version: 1, createdFrom: null, lastSuccessfulUpdate: null, lastMode: null, lastProcessedDate: null, leagues: {}, seen: {}, history: [] };
}
// Inicializace z existujiciho league-goals.json. Pocitadla se zrekonstruuji
// z procent (+/-1 zapas zaokrouhlenim) a oznaci approx:true. Prvni mesicni
// korekce je nahradi presnymi hodnotami.
export function bootstrapMeta(prev, log = () => { }) {
    const meta = emptyMeta(); meta.createdFrom = CONFIG.GOALS_FILE;
    let skipped = 0;
    for (const r of prev || []) {
        if (!Number.isInteger(r?.id) || !Number.isInteger(r?.played) || r.played <= 0) { skipped++; continue; }
        if (isNonStandard(r.name)) { log('   bootstrap: vyrazena nestandardni soutez ' + r.name + ' (' + r.country + ')'); skipped++; continue; }
        const p = r.played, q = v => Math.round(v * p / 100);
        const c = { played: p, o15: q(r.over15Rate), o25: q(r.over25Rate), o35: q(r.over35Rate), btts: q(r.bttsRate) };
        c.o25 = Math.min(c.o25, c.o15); c.o35 = Math.min(c.o35, c.o25);
        c.goals = Math.max(Math.round(r.avgGoals * p), 2 * c.o15 + c.o25 + c.o35);
        if (!validCounters(c)) { skipped++; continue; }
        meta.leagues[r.id] = {
            id: r.id, name: r.name, country: r.country, type: r.type, tier: r.tier,
            currentSeason: r.season, seasonEnd: null, lastFixtureDate: null, status: 'unknown',
            seasons: { [r.season]: { ...c, approx: true } },
        };
    }
    log('Bootstrap meta z ' + CONFIG.GOALS_FILE + ': ' + Object.keys(meta.leagues).length + ' lig, vynechano ' + skipped);
    return meta;
}

// Publikuje se nejnovejsi sezona s aspon NEW_SEASON_MIN zapasy. Kdyz zadna
// takovou nema, sezona s nejvice zapasy - tim stara sezona zustava platna,
// dokud nova nenaroste.
export function publishedSeason(L) {
    const ys = Object.keys(L?.seasons || {}).map(Number).sort((a, b) => b - a);
    for (const y of ys) if (L.seasons[y].played >= CONFIG.NEW_SEASON_MIN) return y;
    let best = null;
    for (const y of ys) if (L.seasons[y].played > 0 && (best == null || L.seasons[y].played > L.seasons[best].played)) best = y;
    return best;
}

export function updateStatuses(meta, today) {
    const year = Number(today.slice(0, 4));
    for (const L of Object.values(meta.leagues)) {
        const ps = publishedSeason(L);
        if (L.seasonEnd && L.seasonEnd >= today) L.status = 'active';
        else if (L.lastFixtureDate && diffDays(today, L.lastFixtureDate) <= CONFIG.INACTIVE_AFTER_DAYS) L.status = 'active';
        else if (ps != null && ps < year - 1) L.status = 'historical';
        else L.status = 'inactive';
    }
}
function pruneSeen(meta, today) {
    const limit = addDays(today, -CONFIG.SEEN_RETENTION_DAYS);
    for (const [id, d] of Object.entries(meta.seen)) if (!d || d < limit) delete meta.seen[id];
}

// ---------------------------------------------------------------- API
export function createApi({ key, fetchImpl = globalThis.fetch, maxRequests, minRemaining = CONFIG.MIN_REMAINING, sleepImpl = sleep }) {
    let count = 0;
    async function get(path) {
        for (let attempt = 1; attempt <= 3; attempt++) {
            if (count >= maxRequests) throw new ApiLimitError('Dosazen strop ' + maxRequests + ' requestu pro tento beh');
            count++;
            let res;
            try {
                res = await fetchImpl(CONFIG.API + path, { headers: { 'x-apisports-key': key, 'Accept': 'application/json', 'User-Agent': CONFIG.USER_AGENT } });
            } catch (e) {
                if (attempt < 3) { await sleepImpl(3000 * attempt); continue; }
                throw new Error('Sitova chyba ' + path + ': ' + e.message);
            }
            const rem = Number(res.headers?.get?.('x-ratelimit-requests-remaining'));
            if (Number.isFinite(rem) && res.headers.get('x-ratelimit-requests-remaining') !== null && rem < minRemaining) {
                throw new ApiLimitError('API hlasi jen ' + rem + ' zbyvajicich dennich requestu (rezerva ' + minRemaining + ')');
            }
            if (res.status === 429) { if (attempt < 3) { await sleepImpl(5000 * attempt); continue; } throw new ApiLimitError('HTTP 429 opakovane'); }
            if (!res.ok) throw new Error('HTTP ' + res.status + ' ' + path);
            let data;
            try { data = await res.json(); } catch { throw new Error('Neplatny JSON ' + path); }
            const errs = data?.errors;
            if (errs && (Array.isArray(errs) ? errs.length : Object.keys(errs).length)) {
                const e = JSON.stringify(errs);
                if (/ratelimit|too many/i.test(e) && attempt < 3) { await sleepImpl(5000 * attempt); continue; }
                if (/request|limit|plan|token|key|subscription/i.test(e)) throw new ApiLimitError('API: ' + e);
                throw new Error('API chyba ' + path + ': ' + e);
            }
            if (!Array.isArray(data?.response)) throw new Error('Odpoved bez pole response ' + path);
            return data;
        }
        throw new Error('Request selhal ' + path);
    }
    return { get, get count() { return count; } };
}

// ---------------------------------------------------------------- weekly
export async function runWeekly({ api, meta, today, log = () => { } }) {
    if (!meta.lastProcessedDate) throw new Error('Meta nema lastProcessedDate - nejdriv spust --mode=monthly (inicializace)');
    const yesterday = addDays(today, -1);
    const gap = diffDays(yesterday, meta.lastProcessedDate);
    if (gap > CONFIG.MAX_GAP_DAYS) throw new Error('Mezera ' + gap + ' dni > ' + CONFIG.MAX_GAP_DAYS + ' - spust mesicni korekci');
    const from = addDays(meta.lastProcessedDate, -(CONFIG.OVERLAP_DAYS - 1));
    const dates = [];
    for (let d = from; d <= yesterday; d = addDays(d, 1)) dates.push(d);

    // Nejdriv VSE stahnout - pri jakekoli chybe se meta vubec nezmeni.
    const all = [];
    for (const d of dates) all.push(...(await api.get('/fixtures?date=' + d)).response);

    const next = clone(meta);
    const st = { mode: 'weekly', dates: dates.length, newFixtures: 0, duplicates: 0, invalid: 0, pending: 0, unknownLeagues: new Set(), updated: new Set(), seasonChanges: [], errors: 0 };
    for (const f of all) {
        const fid = f?.fixture?.id, lid = f?.league?.id, season = f?.league?.season;
        if (!Number.isInteger(fid) || !Number.isInteger(lid)) { st.invalid++; continue; }
        const L = next.leagues[lid];
        if (!L) { st.unknownLeagues.add(lid); continue; }
        const s = finishedScore(f);
        if (!s) { if (f?.fixture?.status?.short === 'FT') st.invalid++; else st.pending++; continue; }
        if (next.seen[fid]) { st.duplicates++; continue; }
        if (!Number.isInteger(season)) { st.invalid++; continue; }
        const before = publishedSeason(L);
        if (!L.seasons[season]) L.seasons[season] = emptyCounters();
        addScore(L.seasons[season], s);
        const date = String(f?.fixture?.date || '').slice(0, 10) || yesterday;
        next.seen[fid] = date;
        if (!L.lastFixtureDate || date > L.lastFixtureDate) L.lastFixtureDate = date;
        if (season > (L.currentSeason ?? -Infinity)) L.currentSeason = season;
        const after = publishedSeason(L);
        if (before !== after) st.seasonChanges.push(L.name + ' (' + L.country + '): ' + before + ' -> ' + after);
        st.updated.add(lid); st.newFixtures++;
    }
    next.lastProcessedDate = yesterday;
    pruneSeen(next, today);
    return { meta: next, stats: st };
}

// ---------------------------------------------------------------- monthly
export async function runMonthly({ api, meta, today, log = () => { }, sleepImpl = sleep }) {
    const next = clone(meta);
    const lr = await api.get('/leagues?current=true');
    const cands = [];
    for (const it of lr.response) {
        const lg = it?.league, ctry = it?.country?.name;
        if (!Number.isInteger(lg?.id) || !ctry) continue;
        if (!isEligible(lg.name, ctry, lg.type)) continue;
        const cur = (it.seasons || []).find(s => s.current);
        if (!Number.isInteger(cur?.year)) continue;
        cands.push({ id: lg.id, name: lg.name, country: ctry, type: lg.type, tier: leagueTier(lg.name, ctry, lg.type), season: cur.year, end: cur.end || null });
    }
    const known = Object.keys(next.leagues).length;
    if (known > 100 && cands.length < known * 0.5) throw new Error('Podezrele malo lig z /leagues (' + cands.length + ' vs ' + known + ' znamych) - nic nemenim');

    const st = { mode: 'monthly', candidates: cands.length, newFixtures: 0, invalid: 0, kept: [], errorsList: [], updated: new Set(), seasonChanges: [], errors: 0, unknownLeagues: new Set() };
    const recent = addDays(today, -CONFIG.SEEN_RETENTION_DAYS);
    let i = 0;
    for (const c of cands) {
        i++;
        if (i % 50 === 0) log('   ...' + i + '/' + cands.length + ' (' + api.count + ' API req)');
        let data;
        try { data = await api.get('/fixtures?league=' + c.id + '&season=' + c.season + '&status=FT'); }
        catch (e) {
            if (e instanceof ApiLimitError) throw e;
            st.errorsList.push(c.name + ' (' + c.country + '): ' + e.message); st.errors++; continue;
        }
        const { c: cnt, invalid } = countersFrom(data.response);
        st.invalid += invalid;
        let L = next.leagues[c.id];
        if (!L) L = next.leagues[c.id] = { id: c.id, seasons: {}, lastFixtureDate: null, status: 'unknown' };
        Object.assign(L, { name: c.name, country: c.country, type: c.type, tier: c.tier, seasonEnd: c.end });
        const before = publishedSeason(L);
        const old = L.seasons[c.season];
        if (old && !old.approx && cnt.played < old.played * CONFIG.MONTHLY_MIN_KEEP) {
            st.kept.push(c.name + ' (' + c.country + '): API ' + cnt.played + ' < ulozeno ' + old.played);
        } else if (cnt.played > 0 || !old) {
            if (cnt.played > 0 || !Object.keys(L.seasons).length) L.seasons[c.season] = cnt;
            st.newFixtures += Math.max(0, cnt.played - (old?.played || 0));
            st.updated.add(c.id);
        }
        // Prazdna nova sezona se neuklada (nemazala by nic, ale zbytecne by
        // posunula currentSeason na sezonu bez dat).
        if (!L.seasons[c.season]?.played) delete L.seasons[c.season];
        else if (c.season > (L.currentSeason ?? -Infinity)) L.currentSeason = c.season;
        for (const f of data.response) {
            const fid = f?.fixture?.id, d = String(f?.fixture?.date || '').slice(0, 10);
            if (Number.isInteger(fid) && d >= recent) next.seen[fid] = d;
            if (d && finishedScore(f) && (!L.lastFixtureDate || d > L.lastFixtureDate)) L.lastFixtureDate = d;
        }
        const after = publishedSeason(L);
        if (before !== after) st.seasonChanges.push(c.name + ' (' + c.country + '): ' + before + ' -> ' + after);
        if (sleepImpl && CONFIG.MONTHLY_SLEEP_MS) await sleepImpl(CONFIG.MONTHLY_SLEEP_MS);
    }
    if (cands.length && st.errors / cands.length > CONFIG.MONTHLY_MAX_ERROR_RATIO) {
        throw new Error('Mesicni korekce: ' + st.errors + '/' + cands.length + ' lig selhalo (> ' + (CONFIG.MONTHLY_MAX_ERROR_RATIO * 100) + ' %) - nic nemenim');
    }
    next.lastProcessedDate = addDays(today, -1);
    pruneSeen(next, today);
    return { meta: next, stats: st };
}

// ---------------------------------------------------------------- vystup
export function buildOutput(meta) {
    const out = [], anomalies = [];
    for (const L of Object.values(meta.leagues)) {
        if (isNonStandard(L.name)) { anomalies.push({ id: L.id, reason: 'nestandardni soutez ' + L.name }); continue; }
        const ps = publishedSeason(L);
        if (ps == null) continue;
        const c = L.seasons[ps];
        if (!validCounters(c) || c.played === 0) { anomalies.push({ id: L.id, reason: 'nevalidni pocitadla ' + L.name }); continue; }
        if (c.goals / c.played > CONFIG.MAX_AVG_GOALS) { anomalies.push({ id: L.id, reason: L.name + ': ' + (c.goals / c.played).toFixed(2) + ' golu/zapas' }); continue; }
        out.push(toRecord(L, ps, c));
    }
    out.sort((a, b) => b.over25Rate - a.over25Rate || a.id - b.id);
    return { out, anomalies };
}

export function validateOutput(out, prev, mode, anomalies = []) {
    const errs = [];
    if (!Array.isArray(out) || out.length === 0) return ['vystup je prazdny'];
    const ids = new Set();
    for (const r of out) {
        const tag = (r?.name || '?') + ' #' + r?.id;
        if (!r || OUTPUT_KEYS.some(k => !(k in r)) || Object.keys(r).length !== OUTPUT_KEYS.length) { errs.push('spatna struktura ' + tag); continue; }
        if (!Number.isInteger(r.id)) errs.push('id ' + tag);
        if (ids.has(r.id)) errs.push('duplicitni id ' + tag); ids.add(r.id);
        if (typeof r.name !== 'string' || typeof r.country !== 'string') errs.push('nazev ' + tag);
        if (!Number.isInteger(r.season)) errs.push('sezona ' + tag);
        if (!Number.isInteger(r.played) || r.played <= 0) errs.push('played ' + tag);
        if (!(r.avgGoals >= 0 && r.avgGoals <= CONFIG.MAX_AVG_GOALS)) errs.push('avgGoals ' + tag);
        for (const k of ['over15Rate', 'over25Rate', 'over35Rate', 'bttsRate']) if (!(Number.isFinite(r[k]) && r[k] >= 0 && r[k] <= 100)) errs.push(k + ' ' + tag);
        if (!(r.over15Rate >= r.over25Rate && r.over25Rate >= r.over35Rate)) errs.push('over15>=over25>=over35 ' + tag);
    }
    if (Array.isArray(prev) && prev.length) {
        const base = prev.filter(r => !isNonStandard(r.name));
        if (out.length < base.length * CONFIG.MIN_KEEP_RATIO) errs.push('vystup ma ' + out.length + ' lig, puvodne ' + base.length + ' (min ' + (CONFIG.MIN_KEEP_RATIO * 100) + ' %)');
        const excl = new Set(anomalies.map(a => a.id));
        const missing = base.filter(r => !ids.has(r.id) && !excl.has(r.id));
        if (missing.length) errs.push('zmizelo ' + missing.length + ' lig (napr. ' + missing.slice(0, 3).map(r => r.name).join(', ') + ')');
        const pm = new Map(base.map(r => [r.id, r]));
        const d = out.filter(r => pm.get(r.id)?.played >= 60 && r.played >= 60).map(r => Math.abs(r.over25Rate - pm.get(r.id).over25Rate));
        const mean = d.length ? d.reduce((a, b) => a + b, 0) / d.length : 0;
        if (mean > CONFIG.MAX_MEAN_SHIFT[mode]) errs.push('prumerny posun over25Rate ' + mean.toFixed(2) + ' b. > ' + CONFIG.MAX_MEAN_SHIFT[mode]);
    }
    return errs;
}

// Cista orchestrace bez souboru - pouzivaji ji testy i CLI.
export async function runUpdate({ mode, api, meta, prev, today, log = () => { }, sleepImpl }) {
    try {
        const r = mode === 'monthly'
            ? await runMonthly({ api, meta, today, log, sleepImpl })
            : await runWeekly({ api, meta, today, log });
        updateStatuses(r.meta, today);
        const { out, anomalies } = buildOutput(r.meta);
        const errors = validateOutput(out, prev, mode, anomalies);
        if (errors.length) return { ok: false, errors, stats: r.stats, requests: api.count, anomalies };
        r.meta.lastSuccessfulUpdate = new Date().toISOString();
        r.meta.lastMode = mode;
        r.meta.history = [...(r.meta.history || []), {
            at: r.meta.lastSuccessfulUpdate, mode, requests: api.count, updatedLeagues: r.stats.updated.size,
            newFixtures: r.stats.newFixtures, errors: r.stats.errors, seasonChanges: r.stats.seasonChanges.length, output: out.length,
        }].slice(-CONFIG.HISTORY_LIMIT);
        return { ok: true, errors: [], out, meta: r.meta, stats: r.stats, requests: api.count, anomalies };
    } catch (e) {
        return { ok: false, errors: [e.message], requests: api.count, limit: e instanceof ApiLimitError };
    }
}

// Atomicky zapis: tmp soubor -> overeni parsovanim -> rename.
export function atomicWriteJson(path, obj) {
    const tmp = path + '.tmp-' + process.pid;
    try {
        writeFileSync(tmp, JSON.stringify(obj, null, 2), 'utf-8');
        JSON.parse(readFileSync(tmp, 'utf-8'));
        renameSync(tmp, path);
    } finally {
        if (existsSync(tmp)) unlinkSync(tmp);
    }
}

// ---------------------------------------------------------------- CLI
async function main() {
    const arg = n => process.argv.find(a => a.startsWith('--' + n + '='))?.split('=')[1];
    const mode = arg('mode') || 'weekly';
    const write = process.argv.includes('--write');
    const outDir = arg('out-dir');
    const today = arg('today') || new Date().toISOString().slice(0, 10);
    if (!['weekly', 'monthly'].includes(mode)) { console.error('Neznamy --mode=' + mode); process.exit(2); }
    const key = process.env.API_FOOTBALL_KEY1;
    if (!key) { console.error('Chybi API_FOOTBALL_KEY1'); process.exit(2); }

    let prev;
    try { prev = JSON.parse(readFileSync(CONFIG.GOALS_FILE, 'utf-8')); if (!Array.isArray(prev)) throw new Error('neni pole'); }
    catch (e) { console.error('Nelze nacist ' + CONFIG.GOALS_FILE + ': ' + e.message + ' - koncim'); process.exit(1); }
    let meta;
    if (existsSync(CONFIG.META_FILE)) {
        try { meta = JSON.parse(readFileSync(CONFIG.META_FILE, 'utf-8')); }
        catch (e) { console.error('Poskozeny ' + CONFIG.META_FILE + ': ' + e.message + ' - koncim'); process.exit(1); }
    } else meta = bootstrapMeta(prev, console.log);

    console.log('Aktualizace golovosti: mode=' + mode + ', dnes=' + today + ', vystup=' + (write ? 'PRODUKCE' : outDir ? 'nahled ' + outDir : 'dry-run'));
    const api = createApi({ key, maxRequests: CONFIG.MAX_REQUESTS[mode] });
    const r = await runUpdate({ mode, api, meta, prev, today, log: console.log });
    const s = r.stats;
    console.log('\n=== Souhrn ===');
    console.log('API requestu:         ' + r.requests);
    if (s) {
        console.log('aktualizovanych lig:  ' + s.updated.size);
        console.log('novych zapasu:        ' + s.newFixtures);
        console.log('duplicit preskoceno:  ' + (s.duplicates ?? 0));
        console.log('nedohranych (pozdeji):' + (s.pending ?? 0));
        console.log('nevalidnich vysledku: ' + s.invalid);
        console.log('chyb lig:             ' + s.errors);
        console.log('neznamych lig:        ' + s.unknownLeagues.size);
        console.log('zmen sezony:          ' + s.seasonChanges.length);
        for (const x of s.seasonChanges) console.log('   ~ ' + x);
        for (const x of s.kept || []) console.log('   = ponechano: ' + x);
        for (const x of s.errorsList || []) console.log('   ! ' + x);
    }
    for (const a of r.anomalies || []) console.log('   x vyrazeno: ' + a.reason);
    if (!r.ok) {
        console.error('\nAKTUALIZACE ZAMITNUTA - nic se nezapsalo:');
        for (const e of r.errors) console.error('   - ' + e);
        process.exit(1);
    }
    console.log('vystup:               ' + r.out.length + ' lig (puvodne ' + prev.length + ')');
    if (write) {
        atomicWriteJson(CONFIG.META_FILE, r.meta);
        atomicWriteJson(CONFIG.GOALS_FILE, r.out);
        console.log('\nZapsano: ' + CONFIG.GOALS_FILE + ', ' + CONFIG.META_FILE);
    } else if (outDir) {
        mkdirSync(outDir, { recursive: true });
        atomicWriteJson(join(outDir, CONFIG.META_FILE), r.meta);
        atomicWriteJson(join(outDir, CONFIG.GOALS_FILE), r.out);
        console.log('\nNahled zapsan do ' + outDir + ' (produkcni soubory beze zmeny)');
    } else console.log('\nDry-run: nic nezapsano.');
}

if (process.argv[1] && process.argv[1].endsWith('update-league-goals.mjs')) {
    main().catch(e => { console.error('Chyba:', e); process.exit(1); });
}
