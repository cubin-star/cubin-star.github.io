// === FVE ENGINE -> react2.json (ACCA) / react3.json (QUAD 4) ===============
// Prioritni zdroj pro ACCA a QUAD 4. Modul je ciste funkcni (bez vedlejsich
// efektu pri importu), aby sel testovat bez site a bez API klicu.
//
// Rozhodovani podle poctu VALIDNICH UNIKATNICH zapasu v dnesnim engine.json:
//   0-19  -> ACCA puvodni vyber, QUAD 4 puvodni vyber
//   20-24 -> ACCA ENGINE,        QUAD 4 puvodni vyber
//   25+   -> ACCA ENGINE,        QUAD 4 ENGINE
// Engine se pouzije jen pro dnesni datum (Europe/Prague), status READY
// a coverage Complete. Kurzy ani tipy se nemeni, jen se zapisi ve formatu,
// ktery uz znaji backendy ACCA/QUAD (TipEvaluator: "Over 2.5", "Home win"...).

export const ENGINE_URL = 'https://raw.githubusercontent.com/cubin-star/cubin-star.github.io/main/engine.json';
export const ENGINE_API_URL = 'https://api.github.com/repos/cubin-star/cubin-star.github.io/contents/engine.json?ref=main';
export const ENGINE_TZ = 'Europe/Prague';
export const ACCA_MIN_ENGINE = 20;
export const QUAD_MIN_ENGINE = 25;
export const ACCA_COUNT = 6;
export const QUAD_COUNT = 8;
export const MATCHES_PER_GROUP = 2;
// Cekani na zverejneni engine.json: nejpozdeji do 09:30 Europe/Prague.
export const ENGINE_WAIT_UNTIL = { hour: 9, minute: 30 };
export const ENGINE_POLL_MS = 60 * 1000; 
const FINAL_STATUSES = new Set(['READY', 'NO_SELECTION']);
// Finalni = dal se uz nezmeni: READY s kompletnim pokrytim, nebo NO_SELECTION.
export function isFinalEngine(doc, today) {
    if (!doc || doc.date !== today) return false;
    return doc.status === 'NO_SELECTION' || (doc.status === 'READY' && doc.coverage === 'Complete');
}

// --- Cas v Europe/Prague (DST resi Intl, zadny pevny offset) ----------------
function pragueParts(date) {
    const f = new Intl.DateTimeFormat('en-CA', { timeZone: ENGINE_TZ, year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23' });
    const p = Object.fromEntries(f.formatToParts(date).filter(x => x.type !== 'literal').map(x => [x.type, x.value]));
    return { year: +p.year, month: +p.month, day: +p.day, hour: +p.hour, minute: +p.minute, second: +p.second };
}
export function pragueDate(date = new Date()) {
    const p = pragueParts(date);
    return p.year + '-' + String(p.month).padStart(2, '0') + '-' + String(p.day).padStart(2, '0');
}
// Offset Europe/Prague vuci UTC v minutach (60 = CET, 120 = CEST).
export function pragueOffsetMinutes(date = new Date()) {
    const p = pragueParts(date);
    const asUtc = Date.UTC(p.year, p.month - 1, p.day, p.hour, p.minute, p.second);
    return Math.round((asUtc - Math.floor(date.getTime() / 1000) * 1000) / 60000);
}
// Prevede mistni cas Europe/Prague (YYYY-MM-DD hh:mm) na UTC Date.
export function pragueLocalToUtc(dateStr, hour, minute) {
    const [y, m, d] = dateStr.split('-').map(Number);
    const naive = Date.UTC(y, m - 1, d, hour, minute);
    let guess = new Date(naive - pragueOffsetMinutes(new Date(naive)) * 60000);
    guess = new Date(naive - pragueOffsetMinutes(guess) * 60000);
    return guess;
}

// --- Planovac GitHub Actions ------------------------------------------------
// GitHub cron bezi jen v UTC. Pro kazdy cilovy cas jsou proto dva sloty
// (letni a zimni) a spusti se jen ten, jehoz offset odpovida aktualnimu
// offsetu Europe/Prague. Rozhoduje se podle vyrazu cronu (github.event.schedule),
// ne podle aktualniho casu, takze ani zpozdeny start GitHubu nevadi.
export const SCHEDULE_SLOTS = {
    '20 7 * * *': { offset: 120, kind: 'main' },   // 09:20 CEST
    '20 8 * * *': { offset: 60, kind: 'main' },    // 09:20 CET
    '50 9 * * *': { offset: 120, kind: 'backup' }, // 11:50 CEST
    '50 10 * * *': { offset: 60, kind: 'backup' }, // 11:50 CET
};
export function scheduleDecision(event, schedule, now = new Date()) {
    if (event !== 'schedule') return { run: true, kind: 'manual', reason: 'event=' + event };
    const slot = SCHEDULE_SLOTS[String(schedule || '').trim()];
    if (!slot) return { run: false, kind: 'unknown', reason: 'neznamy cron "' + schedule + '"' };
    // Den spusteni = den, kdy cron nastal. Zpozdeni GitHubu (i hodiny) offset
    // nezmeni, DST se prepina ve 01:00 UTC - pred vsemi sloty.
    const off = pragueOffsetMinutes(now);
    if (off !== slot.offset) return { run: false, kind: slot.kind, reason: 'slot pro offset +' + slot.offset + ' min, Praha ma ted +' + off + ' min' };
    return { run: true, kind: slot.kind, reason: 'slot odpovida offsetu +' + off + ' min' };
}

// --- Deterministicka nahoda (denni seed) -----------------------------------
export function hashSeed(str) {
    let h = 0x811c9dc5;
    for (let i = 0; i < str.length; i++) { h ^= str.charCodeAt(i); h = Math.imul(h, 0x01000193) >>> 0; }
    return h >>> 0;
}
export function seededRng(seed) {
    let a = (seed >>> 0) || 1;
    return function () {
        a = (a + 0x6D2B79F5) >>> 0;
        let t = Math.imul(a ^ (a >>> 15), 1 | a);
        t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
        return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
}
function seededShuffle(arr, rng) {
    const a = [...arr];
    for (let i = a.length - 1; i > 0; i--) { const j = Math.floor(rng() * (i + 1)); [a[i], a[j]] = [a[j], a[i]]; }
    return a;
}

// --- Prevod vyberu enginu na zapis tipu, ktery znaji ACCA/QUAD backendy ---
// Jde jen o jiny zapis TEHOZ trhu a strany (1X2:HOME -> "Home win",
// OU2.5:UNDER -> "Under 2.5"). Nic jineho se neprevadi; neznamy vyber = neplatny tip.
export function selectionToTip(selection) {
    const s = String(selection || '').trim().toUpperCase();
    const m1 = /^1X2:(HOME|AWAY|DRAW)$/.exec(s);
    if (m1) return { HOME: 'Home win', AWAY: 'Away win', DRAW: 'Draw' }[m1[1]];
    const m2 = /^OU(\d+(?:\.\d+)?):(OVER|UNDER)$/.exec(s);
    if (m2) return (m2[2] === 'OVER' ? 'Over ' : 'Under ') + m2[1];
    return null;
}
function marketOfSelection(selection) { return String(selection || '').split(':')[0].trim().toUpperCase(); }
function parseOdds(v) {
    if (typeof v === 'number') return v;
    if (typeof v === 'string' && /^\s*\d+(\.\d+)?\s*$/.test(v)) return Number(v);
    return NaN;
}
function oddsText(v) { return typeof v === 'string' ? v.trim() : String(v); }
function nonEmpty(v) { return typeof v === 'string' && v.trim() !== ''; }

// Validace dokumentu a tipu. Vraci normalizovane unikatni tipy serazene podle
// fixtureId (nezavisle na poradi ve zdroji => stabilni vyber).
export function evaluateEngine(doc, { now = new Date(), today = pragueDate(now), resolveLeague = defaultLeague } = {}) {
    const res = { usable: false, final: false, reason: '', status: null, date: null, picks: [], rejected: [] };
    if (!doc || typeof doc !== 'object' || Array.isArray(doc)) { res.reason = 'neplatny format'; return res; }
    res.status = doc.status ?? null; res.date = doc.date ?? null;
    if (doc.date !== today) { res.reason = 'datum ' + doc.date + ' neni dnesni ' + today; return res; }
    res.final = FINAL_STATUSES.has(doc.status);
    if (doc.status !== 'READY') { res.reason = 'status ' + doc.status; return res; }
    if (doc.coverage !== 'Complete') { res.reason = 'coverage ' + doc.coverage; return res; }
    if (!Array.isArray(doc.picks)) { res.reason = 'picks neni pole'; return res; }
    const byFixture = new Map();
    for (const p of doc.picks) {
        const why = rejectReason(p, now, resolveLeague);
        if (why) { res.rejected.push({ fixtureId: p?.fixtureId, reason: why }); continue; }
        const pick = {
            fixtureId: Number(p.fixtureId),
            league: resolveLeague(p).trim(),
            match: p.home.trim() + ' - ' + p.away.trim(),
            kickoff: p.kickoffUtc,
            tip: selectionToTip(p.selection),
            odds: oddsText(p.odds),
            selection: p.selection,
            market: p.market ?? marketOfSelection(p.selection),
            source: p,
        };
        if (!byFixture.has(pick.fixtureId)) byFixture.set(pick.fixtureId, []);
        byFixture.get(pick.fixtureId).push(pick);
    }
    // Vic tipu na stejny zapas: deterministicky prvni podle vyberu, pak kurzu.
    for (const [id, list] of byFixture) {
        list.sort((a, b) => a.selection.localeCompare(b.selection) || Number(b.odds) - Number(a.odds));
        if (list.length > 1) res.rejected.push(...list.slice(1).map(x => ({ fixtureId: id, reason: 'duplicitni fixtureId' })));
        res.picks.push(list[0]);
    }
    res.picks.sort((a, b) => a.fixtureId - b.fixtureId);
    res.usable = true;
    res.reason = 'READY, ' + res.picks.length + ' validnich unikatnich zapasu';
    return res;
}
function defaultLeague(p) { return nonEmpty(p?.league) ? p.league : (nonEmpty(p?.leagueName) ? p.leagueName : ''); }
function rejectReason(p, now, resolveLeague) {
    if (!p || typeof p !== 'object') return 'neplatny zaznam';
    if (!Number.isSafeInteger(Number(p.fixtureId)) || Number(p.fixtureId) <= 0) return 'neplatne fixtureId';
    if (!nonEmpty(p.home) || !nonEmpty(p.away)) return 'chybi tymy';
    const ko = Date.parse(p.kickoffUtc);
    if (!nonEmpty(p.kickoffUtc) || !Number.isFinite(ko)) return 'neplatny cas zacatku';
    if (ko <= now.getTime()) return 'zapas uz zacal';
    const odds = parseOdds(p.odds);
    if (!Number.isFinite(odds) || odds <= 1) return 'neplatny kurz';
    if (!nonEmpty(p.selection) || !selectionToTip(p.selection)) return 'nepodporovany vyber ' + p.selection;
    if (nonEmpty(p.market) && p.market.trim().toUpperCase() !== marketOfSelection(p.selection)) return 'market nesouhlasi s vyberem';
    if (!nonEmpty(resolveLeague(p))) return 'nelze urcit nazev ligy';
    return null;
}

// --- Kurzove vyvazeni dvojic ------------------------------------------------
// Uplne prohledani vsech parovani (6 -> 15, 8 -> 105 moznosti). Minimalizuje
// rozdil nejvyssiho a nejnizsiho soucinu dvojice, pri shode soucet odchylek
// logaritmu od prumeru. Kurzy ani tipy se nemeni.
function allPairings(idx) {
    if (idx.length === 0) return [[]];
    const [first, ...rest] = idx, out = [];
    for (let i = 0; i < rest.length; i++) {
        const others = rest.filter((_, j) => j !== i);
        for (const tail of allPairings(others)) out.push([[first, rest[i]], ...tail]);
    }
    return out;
}
export function balanceIntoGroups(picks) {
    if (picks.length % MATCHES_PER_GROUP !== 0) throw new Error('lichy pocet zapasu pro skupiny');
    const sorted = [...picks].sort((a, b) => a.fixtureId - b.fixtureId);
    const odds = sorted.map(p => Number(p.odds));
    let best = null, bestSpread = Infinity, bestDev = Infinity;
    for (const pairing of allPairings(sorted.map((_, i) => i))) {
        const prods = pairing.map(([a, b]) => odds[a] * odds[b]);
        const spread = Math.max(...prods) - Math.min(...prods);
        const logs = prods.map(Math.log), mean = logs.reduce((s, x) => s + x, 0) / logs.length;
        const dev = logs.reduce((s, x) => s + (x - mean) ** 2, 0);
        if (spread < bestSpread - 1e-12 || (Math.abs(spread - bestSpread) <= 1e-12 && dev < bestDev - 1e-12)) { best = pairing; bestSpread = spread; bestDev = dev; }
    }
    const groups = best.map(pair => pair.map(i => sorted[i])).sort((g1, g2) => (Number(g1[0].odds) * Number(g1[1].odds)) - (Number(g2[0].odds) * Number(g2[1].odds)) || g1[0].fixtureId - g2[0].fixtureId);
    return groups.flatMap((g, gi) => g.map(p => ({ ...p, group: gi + 1 })));
}

// --- Rozhodnuti a vyber -----------------------------------------------------
export function planEngineSelection(evaluation, date) {
    const picks = evaluation?.usable ? evaluation.picks : [];
    const n = picks.length;
    const plan = { count: n, accaFromEngine: n >= ACCA_MIN_ENGINE, quadFromEngine: n >= QUAD_MIN_ENGINE, acca: [], quad: [] };
    if (!plan.accaFromEngine) return plan;
    const base = [...picks].sort((a, b) => a.fixtureId - b.fixtureId);
    const acca = seededShuffle(base, seededRng(hashSeed(date + '|acca'))).slice(0, ACCA_COUNT);
    plan.acca = balanceIntoGroups(acca);
    if (plan.quadFromEngine) {
        const used = new Set(acca.map(p => p.fixtureId));
        const rest = base.filter(p => !used.has(p.fixtureId));
        plan.quad = balanceIntoGroups(seededShuffle(rest, seededRng(hashSeed(date + '|quad'))).slice(0, QUAD_COUNT));
    }
    return plan;
}

// Polozka presne ve formatu react2/react3 (stejna pole, odds jako retezec).
export function toFeedItem(p) {
    return { fixtureId: p.fixtureId, league: p.league, match: p.match, kickoff: p.kickoff, tip: p.tip, odds: p.odds, group: p.group };
}

// Pojistka pred zapisem - nesmi se publikovat neuplny nebo poskozeny tiket.
export function assertFeed(items, count, name) {
    const errors = [];
    if (!Array.isArray(items) || items.length !== count) errors.push('ocekavano ' + count + ' zapasu, je ' + (items?.length ?? 0));
    const ids = new Set();
    const groups = new Map();
    for (const [i, m] of (items || []).entries()) {
        if (!(m.fixtureId > 0)) errors.push('#' + (i + 1) + ' chybi fixtureId');
        if (ids.has(m.fixtureId)) errors.push('#' + (i + 1) + ' duplicitni fixtureId ' + m.fixtureId);
        ids.add(m.fixtureId);
        for (const k of ['league', 'match', 'kickoff', 'tip']) if (!nonEmpty(m[k])) errors.push('#' + (i + 1) + ' chybi ' + k);
        if (typeof m.odds !== 'string' || !(Number(m.odds) > 1)) errors.push('#' + (i + 1) + ' neplatny kurz');
        groups.set(m.group, (groups.get(m.group) || 0) + 1);
    }
    const expectedGroups = count / MATCHES_PER_GROUP;
    for (let g = 1; g <= expectedGroups; g++) if (groups.get(g) !== MATCHES_PER_GROUP) errors.push('skupina ' + g + ' ma ' + (groups.get(g) || 0) + ' zapasu');
    if (groups.size !== expectedGroups) errors.push('pocet skupin ' + groups.size);
    if (errors.length) throw new Error(name + ' neprosel validaci: ' + errors.join('; '));
}

// --- Stazeni engine.json s cekanim do 09:30 Prahy ---------------------------
async function fetchOnce(fetchImpl, token) {
    const attempts = [];
    // GitHub API vraci aktualni obsah bez CDN cache raw.githubusercontent.com.
    attempts.push({ url: ENGINE_API_URL, headers: { Accept: 'application/vnd.github.raw+json', 'User-Agent': 'kombik-bot', ...(token ? { Authorization: 'Bearer ' + token } : {}) } });
    attempts.push({ url: ENGINE_URL + '?t=' + Date.now(), headers: { Accept: 'application/json', 'User-Agent': 'kombik-bot', 'Cache-Control': 'no-cache' } });
    let lastErr = 'nedostupny';
    for (const a of attempts) {
        try {
            const res = await fetchImpl(a.url, { headers: a.headers });
            if (!res.ok) { lastErr = 'HTTP ' + res.status; continue; }
            return { doc: JSON.parse(await res.text()), error: null };
        } catch (e) { lastErr = e.message; }
    }
    return { doc: null, error: lastErr };
}
export async function loadEngine({ fetchImpl = fetch, now = () => new Date(), sleepFn = ms => new Promise(r => setTimeout(r, ms)), pollMs = ENGINE_POLL_MS, token = process.env.GITHUB_TOKEN, log = () => {} } = {}) {
    const today = pragueDate(now());
    const deadline = pragueLocalToUtc(today, ENGINE_WAIT_UNTIL.hour, ENGINE_WAIT_UNTIL.minute);
    for (let attempt = 1; ; attempt++) {
        const { doc, error } = await fetchOnce(fetchImpl, token);
        const ev = doc ? evaluateEngine(doc, { now: now(), today }) : null;
        if (isFinalEngine(doc, today)) { log('engine.json: dnesni finalni stav ' + doc.status + ' (pokus ' + attempt + ')'); return { doc, error: null, today }; }
        log('engine.json: ' + (error ? 'chyba ' + error : (ev?.reason || 'neni finalni')) + ' (pokus ' + attempt + ')');
        if (now().getTime() + pollMs > deadline.getTime()) { log('engine.json: limit 09:30 Praha vycerpan, pokracuji.'); return { doc, error, today }; }
        await sleepFn(pollMs);
    }
}
