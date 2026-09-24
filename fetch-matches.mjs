import { writeFileSync, existsSync, readFileSync } from 'fs';

const API_KEY = process.env.API_FOOTBALL_KEY1;

const FOOTBALL_API = 'https://v3.football.api-sports.io';
const MIN_ODDS = 2.1;
const MAX_ODDS = 2.5;
const PICK_COUNT = 6;
// Samostatny denni vyber do tips.json - nizsi kurzovy pas, primarne tier 1+2,
// pri nedostatku se dobira z tieru 3 a 4. Max jeden zapas z ligy.
const TIPS_MIN_ODDS = 1.75;
const TIPS_MAX_ODDS = 1.9;
const TIPS_PICK_COUNT = 2;
// Max pocet vybranych zapasu z jedne zeme - brani zaplaveni tiketu jednou destinaci.
const MAX_PER_COUNTRY = 2;
// Vahy pro vazeny nahodny vyber TIERU (ne jednotlivych zapasu) - vyssi tier ma
// vyrazne vetsi sanci, ale nizsi nejsou vyloucene.
// T1 = top evropske ligy + UEFA pohary, T2 = druhe ligy/domaci pohary, T3 = ostatni Evropa, T4 = zbytek sveta.
// Sance pri dostupnych vsech tierech: T1 ~66 %, T2 ~23 %, T3 ~9 %, T4 ~1,6 %.
const TIER_WEIGHTS = { 1: 40, 2: 14, 3: 5, 4: 1 };
// Tvrdy strop na pocet vybranych zapasu z daneho tieru. Resi to, ze T4 ma vzdy
// nejvic kandidatu - i kdyby jich bylo 200, projde max 1. Stropy se uvolni az
// v druhe fazi, kdyby se jinak nepodarilo naplnit PICK_COUNT.
const MAX_PER_TIER = { 1: 6, 2: 4, 3: 2, 4: 1 };
// Z tieru se nikdy nevybere vic nez tento podil jeho kandidatu (min. 1 zapas).
// Bez toho by pri 4 kandidatech v T1 a stropu 6 byl vyber deterministicky -
// vzali by se proste vsichni ctyri. Takhle se ze 4 kandidatu vyberou max 2.
const MAX_TIER_SHARE = 0.5;
const EXCLUDED_COUNTRIES = new Set(['Russia', 'Belarus']);
const TZ = 'Europe/Prague';
const USER_AGENT = 'kombik-bot/1.0 (+github-actions)';
// Zdroje (URL), jejichz zapasy NESMI byt vybrany (jine boty vybiraji podobne) -> dedup
const DEDUP_URLS = [
    'https://raw.githubusercontent.com/cubin-star/cubin-star.github.io/main/fotbal.json',
    'https://raw.githubusercontent.com/cubin-star/cubin-star.github.io/main/live2.json',
];

let reqCount = 0;
let fixtureFetchErrors = 0;
let fixtureFetchAttempts = 0;

// === Klasifikace soutezi ====================================================
// Misto krehkeho porovnavani presnych nazvu 'Nazev|Zeme' se soutez rozklada na
// (skupina zeme) x (uroven souteze), coz je odolne vuci diakritice, sponzorskym
// nazvum a drobnym odchylkam v API.

// Normalizace: odstrani diakritiku, prevede na mala pismena, interpunkci na mezery
// a oddeli cislice od pismen ('LaLiga2' -> 'laliga 2', '2. Bundesliga' -> '2 bundesliga').
function norm(s){
    return String(s||'')
        .replace(/ı/g,'i').replace(/İ/g,'i').replace(/ø/g,'o').replace(/Ø/g,'o')
        .replace(/đ/g,'d').replace(/Đ/g,'d').replace(/ß/g,'ss')
        .replace(/ł/g,'l').replace(/Ł/g,'l').replace(/æ/g,'ae').replace(/Æ/g,'ae')
        .normalize('NFD').replace(/[\u0300-\u036f]/g,'')
        .toLowerCase()
        .replace(/[^a-z0-9]+/g,' ')
        .replace(/([a-z])(\d)/g,'$1 $2')
        .replace(/\s+/g,' ')
        .trim();
}

// Souteze, ktere nikdy nechceme (mladez, zeny, rezervy, pratelaky, futsal...).
const EXCLUDE_RE = [
    /\bu ?\d{2}\b/, /\byouth\b/, /\bjunior/, /\bjuvenil/, /\bprimavera\b/,
    /\breserve/, /\bacademy\b/, /\bdevelopment\b/,
    /\bamateur/, /\bamatoer/, /\bveterans\b/,
    // Rezervni / farmarske / mladeznicke souteze schovane pod nazvem hlavni ligy
    /\bpremier league 2\b/, /\bpremier league cup\b/, /\bpremier league international cup\b/, /\bnext pro\b/, /\belite league\b/,
    // Zenske souteze - 'wk league' (J. Korea), 'femenil' (Mexiko), 'kvinde' (Dansko)...
    /\bwk league\b/, /\bwomen\b/, /\bfemen/, /\bfeminin/, /\bfrauen/,
    /\bdamallsvenskan\b/, /\belitettan\b/, /\bkvinde/, /\btoppserien\b/, /\bnwsl\b/, /\bkansallinen liiga\b/,
    /\bwsl\b/, /\busl super league\b/, /\bfa wsl\b/,
    /\bfriendl/, /\bfutsal\b/, /\bbeach\b/, /\besoccer\b/, /\bindoor\b/,
];

// Rozpoznani poharu bez nutnosti znat nazev (fallback, kdyz chybi league.type z API).
const CUP_RE = /\bcup\b|\bcupen\b|\bpokal\b|\bpokalen\b|\bcopa\b|\bcoupe\b|\bcoppa\b|\bbeker\b|\btaca\b|\bkupa\b|\bkupasi\b|\bkubok\b|\bpuchar\b|\bpohar\b|\bkypello\b|\btrophy\b|\bsupercup\b|\bsuper cup\b/;
// Pohary nizsich/mladeznickych urovni - nepatri mezi hlavni domaci pohary (T2).
const LOW_CUP_RE = /\bfa trophy\b|\bfa vase\b|\bleague trophy\b|\befl trophy\b|\bpremier league cup\b|\bnational league cup\b|\bchallenge cup\b|\bcopa federacion\b|\bregional/;

// Zeme, kde nazev neprozradi uroven ligy (Championship, 1. Division...) -> rucni mapa.
// Klic = normalizovana zeme, hodnota = [uroven, [normalizovane podretezce nazvu]].
const LEVEL_OVERRIDES = {
    'england':   [[1,['premier league']],[2,['championship']],[3,['league one']],[4,['league two']],[5,['national league','non league','isthmian','northern premier','southern league']]],
    'scotland':  [[1,['premiership']],[2,['championship']],[3,['league one']],[4,['league two']],[5,['highland league','lowland league']]],
    'netherlands':[[2,['eerste divisie']],[3,['tweede divisie']]],
    'belgium':   [[2,['challenger pro league','first division b']]],
    'portugal':  [[2,['liga de honra','segunda liga','liga portugal 2']],[3,['campeonato de portugal']]],
    'spain':     [[3,['primera federacion','primera division rfef']],[4,['segunda federacion','segunda division rfef']],[5,['tercera']],[2,['segunda division','laliga 2','la liga 2']]],
    'italy':     [[3,['serie c']],[4,['serie d']]],
    'germany':   [[4,['regionalliga']],[5,['oberliga']]],
    'france':    [[3,['national 1','championnat national']],[4,['national 2']],[5,['national 3']]],
    'turkey':    [[1,['super lig']],[2,['1 lig','first league']],[3,['2 lig']],[4,['3 lig']]],
    'denmark':   [[1,['superliga','superligaen']],[2,['1 division','1 divisionen']],[3,['2 division']]],
    'sweden':    [[1,['allsvenskan']],[2,['superettan']],[3,['ettan']]],
    'norway':    [[1,['eliteserien']],[2,['obos ligaen','1 divisjon']],[3,['2 divisjon']]],
    'czech republic':[[1,['czech liga','fortuna liga','first league']],[2,['fnl','narodni liga','national football league']]],
    'slovakia':  [[1,['super liga','nike liga','fortuna liga','fortuna 1 liga']],[2,['2 liga']]],
    'poland':    [[1,['ekstraklasa']],[2,['i liga','1 liga']],[3,['ii liga','2 liga']]],
    'austria':   [[1,['bundesliga']],[2,['2 liga']],[3,['regionalliga']]],
    'switzerland':[[1,['super league']],[2,['challenge league']],[3,['promotion league']],[4,['1 liga classic','1 liga']]],
    'greece':    [[1,['super league 1']],[2,['super league 2']]],
    'ireland':   [[1,['premier division']],[2,['first division']]],
    'republic of ireland':[[1,['premier division']],[2,['first division']]],
    'usa':       [[2,['usl championship','nwsl']],[3,['usl league one','mls next pro']],[4,['usl league two','nisa']]],
    'brazil':    [[2,['serie b']],[3,['serie c']],[4,['serie d']]],
    'japan':     [[2,['j 2 league']],[3,['j 3 league']]],
    'south korea':[[2,['k league 2']]],
    'croatia':   [[2,['prva nl','first nl','druga']]],
    'israel':    [[2,['liga leumit']],[3,['liga alef']],[4,['liga bet','liga gimel']]],
    'ukraine':   [[2,['persha liga','first league']],[3,['druha liga']]],
    'estonia':   [[1,['meistriliiga']],[2,['esiliiga a','esiliiga']],[3,['esiliiga b']]],
    'lithuania': [[1,['a lyga']],[2,['1 lyga','pirma lyga']]],
    'latvia':    [[1,['virsliga']],[2,['1 liga','first league']]],
    'argentina': [[2,['primera nacional']],[3,['primera b metropolitana']],[4,['primera c','primera d','torneo federal']]],
    'paraguay':  [[2,['division intermedia']]],
    'costa rica':[[2,['liga de ascenso','segunda division']]],
    'colombia':  [[2,['primera b','torneo']]],
    'chile':     [[2,['primera b']]],
    'bolivia':   [[2,['nacional b']]],
    'saudi arabia':[[2,['division 1','first division']]],
    'wales':     [[1,['cymru premier']],[2,['faw championship','cymru north','cymru south']]],
    'iran':      [[1,['persian gulf pro league']],[2,['azadegan league']]],
    'india':     [[1,['indian super league']],[2,['i league']],[3,['calcutta','premier division','state league']]],
    'northern ireland':[[1,['premiership']],[2,['championship']]],
    'serbia':    [[1,['super liga','superliga']],[2,['prva liga']],[3,['srpska liga']]],
    'slovenia':  [[1,['prva liga','1 snl']],[2,['2 snl']],[3,['3 snl']]],
    'azerbaijan':[[1,['premyer liqa']],[2,['birinci dasta','first division']]],
    'albania':   [[1,['kategoria superiore','superiore']],[2,['1st division','kategoria e pare','first division']]],
    'bosnia':    [[1,['premijer liga']],[2,['1st league','first league','prva liga']]],
    'bosnia and herzegovina':[[1,['premijer liga']],[2,['1st league','first league','prva liga']]],
    'uzbekistan':[[1,['super league','superliga']],[2,['pro league']]],
    'kazakhstan':[[1,['premier league']],[2,['1 division','first division']]],
    'mexico':    [[1,['liga mx']],[2,['liga de expansion','expansion mx','ascenso mx']],[3,['liga premier','primera premier','serie a']],[4,['serie b','liga tdp']]],
    'china':     [[1,['super league']],[2,['league one']],[3,['league two']]],
    'moldova':   [[1,['super liga','divizia nationala','national division']],[2,['liga 1']]],
    'south africa':[[1,['premier soccer league','premiership','psl']],[2,['1st division','first division','motsepe']]],
    'united arab emirates':[[1,['pro league','uae league','adnoc']],[2,['division 1','first division']]],
    'finland':   [[1,['veikkausliiga']],[2,['ykkosliiga','ykkonen']],[3,['kakkonen']]],
    'malta':     [[1,['premier league']],[2,['challenge league','first division']]],
    'faroe islands':[[1,['betrideildin','meistaradeildin','premier league']],[2,['1 deild']],[3,['2 deild']]],
    'australia': [[1,['a league']],[2,['npl','state league','premier league']]],
    'armenia':   [[1,['premier league']],[2,['first league']]],
    'macedonia': [[1,['first league','prva liga']],[2,['second league','vtora liga']]],
    'north macedonia':[[1,['first league','prva liga']],[2,['second league','vtora liga']]],
    'ghana':     [[1,['premier league']],[2,['division one league','division one']]],
    'kosovo':    [[1,['superliga','super league']],[2,['liga e pare','first league']]],
};

// Genericka detekce urovne z cisla/slova v nazvu. Poradi od nejnizsi urovne.
const GENERIC_LEVELS = [
    [5,[/\b(5|v)\b/,/quinta/,/oberliga/]],
    [4,[/\b(4|iv)\b/,/cuarta/,/quarta/,/quatrieme/,/regionalliga/,/\bd\b/]],
    [3,[/\b(3|iii)\b/,/tercera/,/terza/,/troisieme/,/dritte/,/third/,/treca/,/\bc\b/]],
    [2,[/\b(2|ii)\b/,/segunda/,/seconda/,/second/,/zweite/,/deuxieme/,/druga/,/druha/,/masodik/,/\bb\b/]],
];

// Shoda podretezce na hranicich slov. Prosty includes() nestaci - 'III Liga'
// obsahuje 'I Liga' a 'Liga III' obsahuje 'Liga II', takze 3. ligy padaly do T2.
const wordReCache = new Map();
function hasWord(n,p){
    let re=wordReCache.get(p);
    if(!re){re=new RegExp('\\b'+p.replace(/[.*+?^${}()|[\]\\]/g,'\\$&')+'\\b');wordReCache.set(p,re);}
    return re.test(n);
}

// Vrati uroven ligy (1 = nejvyssi soutez zeme). Default 1 - nizsi souteze maji
// prakticky vzdy cislo nebo poradove slovo v nazvu.
function leagueLevel(n,c){
    const ov=LEVEL_OVERRIDES[c];
    if(ov){for(const [lvl,pats] of ov){for(const p of pats){if(hasWord(n,p))return lvl;}}}
    for(const [lvl,res] of GENERIC_LEVELS){for(const re of res){if(re.test(n))return lvl;}}
    return 1;
}

// Explicitni seznam soutezi pro tier 1 a tier 2.
// Klic = normalizovana zeme (viz norm()), hodnota = normalizovane podretezce nazvu.
// U kazde ligy jsou uvedeny i alternativni nazvy, ktere API-Football pouziva
// nebo pouzivalo (sponzorske nazvy se meni kazdou sezonu).
// POZOR: T2 se vyhodnocuje PRED T1, aby 'Liga Portugal 2' nespadla pod 'Liga Portugal'.
const TIER1_LEAGUES = {
    'england':      ['premier league'],
    'czech republic':['chance liga','czech liga','fortuna liga','1 liga','first league'],
    'france':       ['ligue 1'],
    'italy':        ['serie a'],
    'germany':      ['bundesliga'],
    'spain':        ['la liga','laliga'],
    'belgium':      ['jupiler pro league','first division a'],
    'denmark':      ['superliga','superligaen'],
    'finland':      ['veikkausliiga'],
    'croatia':      ['hnl','prva liga'],
    'ireland':      ['premier division'],
    'republic of ireland':['premier division'],
    'israel':       ['ligat ha al','ligat haal','premier league'],
    'japan':        ['j 1 league','j1 league'],
    'south korea':  ['k league 1'],
    'netherlands':  ['eredivisie'],
    'norway':       ['eliteserien'],
    'poland':       ['ekstraklasa'],
    'portugal':     ['liga portugal','primeira liga'],
    'austria':      ['bundesliga'],
    'romania':      ['superliga','liga i'],
    'greece':       ['super league 1'],
    'scotland':     ['premiership'],
    'slovakia':     ['nike liga','super liga','fortuna liga'],
    'slovenia':     ['prva liga','1 snl'],
    'serbia':       ['super liga','superliga'],
    'sweden':       ['allsvenskan'],
    'switzerland':  ['super league'],
    'turkey':       ['super lig','superliga'],
    'ukraine':      ['premier league'],
    'usa':          ['major league soccer','mls'],
};

const TIER2_LEAGUES = {
    'england':      ['championship','league one'],
    'brazil':       ['serie a'],
    'denmark':      ['1 division','1 divisionen'],
    'france':       ['ligue 2'],
    'indonesia':    ['liga 1','super league'],
    'italy':        ['serie b'],
    'cyprus':       ['1 division','1 divizion','first division'],
    'lithuania':    ['a lyga','toplyga'],
    'latvia':       ['virsliga','virsliga'],
    'hungary':      ['nb i','nb 1','otp bank liga'],
    'malta':        ['premier league'],
    'germany':      ['2 bundesliga'],
    'netherlands':  ['eerste divisie'],
    'norway':       ['obos ligaen','obos','1 divisjon'],
    'paraguay':     ['division profesional','copa de primera','primera division'],
    'peru':         ['liga 1','primera division'],
    'poland':       ['i liga','1 liga'],
    'portugal':     ['liga portugal 2','segunda liga','liga de honra'],
    'austria':      ['2 liga'],
    'romania':      ['liga ii','liga 2'],
    'scotland':     ['championship'],
    'spain':        ['segunda division','laliga 2','la liga 2'],
    'sweden':       ['superettan'],
    'switzerland':  ['promotion league'],
    'thailand':     ['thai league 1'],
    'turkey':       ['1 lig','first league'],
};

// Reprezentacni souteze (country = 'World'). Evropske a svetove -> T1,
// ostatni svetadily -> T2. Duvod: pri reprezentacni prestavce nehraji klubove
// souteze z T1/T2 a bez tohoto by zbyly jen zapasy z T3/T4.
const NAT_T1_RE = /\buefa nations league\b|\beuro championship\b|\beuropean championship\b|\bworld cup\b|\bfinalissima\b/;
const NAT_T2_RE = /\bafrica cup of nations\b|\bafcon\b|\basian cup\b|\bcopa america\b|\bgold cup\b|\bconcacaf nations league\b|\bconcacaf championship\b|\bcaf\b|\bafc asian\b|\bafc championship\b|\boceania nations cup\b|\bofc nations cup\b/;

function matchesList(list,c,n){
    const pats=list[c];
    if(!pats)return false;
    for(const p of pats){if(hasWord(n,p))return true;}
    return false;
}

function leagueTier(name,country,type){
    const n=norm(name),c=norm(country);
    if(EXCLUDE_RE.some(re=>re.test(n)))return 0;
    // Pohary nizsich urovni odchytit drive, nez se nazev chytne na seznam T1/T2
    // ('Premier League Cup' obsahuje 'premier league').
    if(LOW_CUP_RE.test(n))return 4;
    // Spanelske RFEF souteze jsou az 3.-5. uroven, ale nazev obsahuje
    // 'segunda division' / 'primera division' ze seznamu T1/T2 -> odchytit driv.
    if(c==='spain'&&/\brfef\b|\bfederacion\b/.test(n))return 4;
    if(c==='world'){
        if(/uefa champions league|uefa europa league|uefa (europa )?conference league/.test(n))return 1;
        // Evropske reprezentacni souteze + svetove kvalifikace -> T1. Bez toho by
        // pri reprezentacni prestavce byly T1 i T2 prazdne a bral by se jen T4.
        if(NAT_T1_RE.test(n))return 1;
        // Reprezentacni souteze ostatnich svetadilu -> T2.
        if(NAT_T2_RE.test(n))return 2;
        if(/uefa super cup/.test(n))return 2;
        return 4;
    }
    // T2 se testuje jako prvni - jeho nazvy jsou casto nadmnozinou tech z T1
    // ('liga portugal 2' obsahuje 'liga portugal', '2 bundesliga' obsahuje 'bundesliga').
    if(matchesList(TIER2_LEAGUES,c,n))return 2;
    if(matchesList(TIER1_LEAGUES,c,n))return 1;
    const isCup=(type&&norm(type)==='cup')||CUP_RE.test(n);
    if(isCup){
        // Hlavni domaci pohary zemi, ktere maji ligu v T1 -> T3, ostatni T4.
        return TIER1_LEAGUES[c]?3:4;
    }
    // Zbytek: nejvyssi soutez zeme -> T3, jakakoli nizsi soutez -> T4.
    return leagueLevel(n,c)===1?3:4;
}

export { leagueTier, norm, leagueLevel };
function maskKey(k){if(!k)return'(none)';if(k.length<=8)return'***';return k.slice(0,4)+'...'+k.slice(-4)+' (len='+k.length+')';}
function sleep(ms){return new Promise(r=>setTimeout(r,ms));}
function shuffle(arr){for(let i=arr.length-1;i>0;i--){const j=Math.floor(Math.random()*(i+1));[arr[i],arr[j]]=[arr[j],arr[i]];}return arr;}
function fmtDate(d){return d.toISOString().split('T')[0];}
// Stredni (medianovy) kurz - pro sudy pocet prumer dvou prostrednich hodnot
function median(arr){if(!arr||arr.length===0)return NaN;const s=[...arr].sort((a,b)=>a-b);const mid=Math.floor(s.length/2);return s.length%2!==0?s[mid]:(s[mid-1]+s[mid])/2;}
// --- Deduplikace proti existujicim json souborum jinych botu -----------------
function normTeam(s){return String(s||'').toLowerCase().replace(/\s+/g,' ').trim();}
function normDate(s){return String(s||'').slice(0,10);}
function matchKeysFromEntry(m){
    const keys=[];
    const id=m?.fixtureId??m?.fixture?.id??m?.id;
    if(id!==undefined&&id!==null&&id!=='')keys.push('id:'+id);
    // 'match' bywa "Home - Away"
    let home=m?.home??m?.teams?.home?.name;
    let away=m?.away??m?.teams?.away?.name;
    if((!home||!away)&&typeof m?.match==='string'&&m.match.includes(' - ')){
        const [h,a]=m.match.split(' - ');home=home||h;away=away||a;
    }
    const date=normDate(m?.kickoff??m?.fixture?.date??m?.date);
    if(home&&away&&date)keys.push('na:'+normTeam(home)+'|'+normTeam(away)+'|'+date);
    if(home&&away)keys.push('teams:'+normTeam(home)+'|'+normTeam(away));
    return keys;
}
function loadDedupKeysFromFiles(paths){
    const set=new Set();
    for(const p of paths){
        if(!existsSync(p)){console.log('Dedup: '+p+' neexistuje, preskakuji.');continue;}
        try{
            const raw=readFileSync(p,'utf-8');
            const data=JSON.parse(raw);
            const arr=Array.isArray(data)?data:(data.matches??data.items??data.picks??[]);
            let n=0;
            for(const m of arr){for(const k of matchKeysFromEntry(m)){set.add(k);}n++;}
            console.log('Dedup: '+p+' -> '+n+' zaznamu, '+set.size+' klicu celkem');
        }catch(e){console.warn('Dedup: nelze precist '+p+': '+e.message);}
    }
    return set;
}
function isDuplicate(candidate,dedupSet){
    for(const k of matchKeysFromEntry(candidate)){if(dedupSet.has(k))return true;}
    return false;
}
async function loadDedupKeysFromUrls(urls){
    const set=new Set();
    for(const u of urls){
        try{
            const res=await fetch(u,{headers:{'Accept':'application/json','User-Agent':USER_AGENT,'Cache-Control':'no-cache'}});
            if(!res.ok){console.warn('Dedup: '+u+' HTTP '+res.status+', preskakuji.');continue;}
            const data=await res.json();
            const arr=Array.isArray(data)?data:(data.matches??data.items??data.picks??[]);
            let n=0;
            for(const m of arr){for(const k of matchKeysFromEntry(m)){set.add(k);}n++;}
            console.log('Dedup: '+u+' -> '+n+' zaznamu, '+set.size+' klicu celkem');
        }catch(e){console.warn('Dedup: nelze stahnout '+u+': '+e.message);}
    }
    return set;
}
// ---------------------------------------------------------------------------

async function apiFetch(path){
    const url=FOOTBALL_API+path;
    for(let attempt=1;attempt<=3;attempt++){
        try{
            const res=await fetch(url,{headers:{'x-apisports-key':API_KEY,'Accept':'application/json','User-Agent':USER_AGENT}});
            reqCount++;
            if(res.status===429){console.warn('  [429] waiting '+(5*attempt)+'s...');await sleep(5000*attempt);continue;}
            if(res.status===403){let b='';try{b=(await res.text()).slice(0,400);}catch{}console.warn('  [403] '+path.split('?')[0]+' key='+maskKey(API_KEY)+' body='+b);if(attempt<3){await sleep(3000*attempt);continue;}return{response:[],paging:{total:0},__error:403};}
            if(!res.ok){let b='';try{b=(await res.text()).slice(0,200);}catch{}console.warn('  HTTP '+res.status+': '+path.split('?')[0]+' body='+b);return{response:[],paging:{total:0},__error:res.status};}
            const data=await res.json();
            if(data.errors&&Object.keys(data.errors).length>0){const e=JSON.stringify(data.errors);if(e.includes('rateLimit')||e.includes('Too many')){console.warn('  [RATE] '+e+' waiting '+(5*attempt)+'s...');await sleep(5000*attempt);continue;}console.warn('  ',e);return{response:[],paging:{total:0},__error:'api-errors'};}
            return data;
        }catch(e){console.warn('  Fetch error:',e.message);return{response:[],paging:{total:0},__error:'exception'};}
    }
    console.warn('  [FAIL] Max retries: '+path.split('?')[0]);
    return{response:[],paging:{total:0},__error:'max-retries'};
}

async function getFixtures(date){fixtureFetchAttempts++;const data=await apiFetch('/fixtures?date='+date+'&timezone='+TZ+'&status=NS');if(data.__error)fixtureFetchErrors++;return data.response||[];}

// Metadata vsech soutezi (hlavne league.type = 'League' / 'Cup'), ktere /fixtures nevraci.
async function getLeagueTypes(){
    const data=await apiFetch('/leagues');
    const map=new Map();
    for(const it of data.response||[]){if(it?.league?.id!=null)map.set(it.league.id,it.league.type);}
    console.log('Metadata soutezi: '+map.size+' lig nacteno');
    return map;
}

async function getLeagueOdds(leagueId,season,date){
    let all=[],page=1,totalPages=1;
    do{const data=await apiFetch('/odds?league='+leagueId+'&season='+season+'&date='+date+'&bet=5&page='+page);all.push(...(data.response||[]));totalPages=data.paging?.total||0;page++;if(page<=totalPages)await sleep(450);}while(page<=totalPages);
    return all;
}

function generatePairings(indices){const results=[];function recurse(rem,cur){if(rem.length===0){results.push([...cur]);return;}if(rem.length===1){results.push([...cur,[rem[0]]]);return;}const first=rem[0],rest=rem.slice(1);for(let i=0;i<rest.length;i++){cur.push([first,rest[i]]);recurse(rest.filter((_,j)=>j!==i),cur);cur.pop();}}recurse(indices,[]);return results;}

function balanceGroups(picks){
    const indices=picks.map((_,i)=>i);const allP=generatePairings(indices);
    let bestP=null,bestD=Infinity;
    for(const p of allP){const gO=p.map(pair=>pair.reduce((a,idx)=>a*parseFloat(picks[idx].odds),1));const d=Math.max(...gO)-Math.min(...gO);if(d<bestD){bestD=d;bestP=p;}}
    if(!bestP)return picks.map((p,i)=>({...p,group:Math.floor(i/2)+1}));
    const result=[];for(let g=0;g<bestP.length;g++){for(const idx of bestP[g])result.push({...picks[idx],group:g+1});}return result;
}

async function main(){
    console.log('Kombik Bot - fetch-matches\n');

    // 1) Nacti existujici vybery jinych botu (fotbal.json, live2.json z GitHubu) -> dedup set
    const dedupSet=await loadDedupKeysFromUrls(DEDUP_URLS);
    console.log('Dedup klicu celkem: '+dedupSet.size+'\n');

    const leagueTypes=await getLeagueTypes();
    await sleep(450);

    const now=new Date(),max24h=new Date(now.getTime()+24*60*60*1000);
    console.log('Window: '+now.toUTCString()+' -> '+max24h.toUTCString()+' (24h)\n');
    const dates=new Set();
    for(let d=new Date(now);d<=max24h;d=new Date(d.getTime()+24*60*60*1000))dates.add(fmtDate(d));
    let fixtures=[];
    for(const d of dates){console.log('Fixtures '+d+'...');fixtures.push(...await getFixtures(d));await sleep(450);}
    console.log('   '+fixtures.length+' scheduled matches\n');
    if(fixtureFetchAttempts>0&&fixtureFetchErrors===fixtureFetchAttempts){console.error('FATAL: All /fixtures failed. key='+maskKey(API_KEY));process.exit(2);}
    fixtures=fixtures.filter(f=>{const t=new Date(f.fixture.date);return t>=now&&t<=max24h&&!EXCLUDED_COUNTRIES.has(f.league.country);});
    console.log('   '+fixtures.length+' in 24h window (excl. RU/BY)');
    const fixtureMap=new Map(),leagueMap=new Map();
    for(const f of fixtures){fixtureMap.set(f.fixture.id,f);const key=f.league.id+'_'+f.league.season;if(!leagueMap.has(key))leagueMap.set(key,{id:f.league.id,season:f.league.season,name:f.league.name,country:f.league.country,type:leagueTypes.get(f.league.id),tier:leagueTier(f.league.name,f.league.country,leagueTypes.get(f.league.id)),dates:new Set()});leagueMap.get(key).dates.add(fmtDate(new Date(f.fixture.date)));}
    console.log('   '+leagueMap.size+' leagues');
    // Diagnostika: prehled zarazeni soutezi do tieru (pro ladeni klasifikace)
    for(let t=0;t<=4;t++){
        const names=[...leagueMap.values()].filter(l=>l.tier===t).map(l=>l.name+' ('+l.country+')');
        if(names.length)console.log('   T'+t+(t===0?' [VYRAZENO]':'')+': '+names.join(', '));
    }
    // Vyrad mladez/zeny/rezervy/pratelaky uplne - nema smysl na ne platit /odds dotazy.
    for(const [k,lg] of leagueMap){if(lg.tier===0)leagueMap.delete(k);}
    console.log('   '+leagueMap.size+' leagues po vyrazeni\n');
    const candidateMap=new Map();
    const tipsCandidateMap=new Map();
    function addCandidate(map,mKey,lg,fix,odd){
        if(!map.has(mKey))map.set(mKey,{fixtureId:mKey,league:lg.name,country:lg.country,match:fix.teams.home.name+' - '+fix.teams.away.name,kickoff:fix.fixture.date,tip:'Over 2.5',tier:lg.tier,allOdds:[]});
        map.get(mKey).allOdds.push(odd);
    }
    for(const[,lg]of leagueMap){for(const d of lg.dates){const oddsData=await getLeagueOdds(lg.id,lg.season,d);for(const entry of oddsData){const fix=fixtureMap.get(entry.fixture?.id);if(!fix)continue;const mKey=fix.fixture.id;for(const bm of entry.bookmakers||[]){for(const bet of bm.bets||[]){for(const v of bet.values||[]){if(v.value!=='Over 2.5')continue;const odd=parseFloat(v.odd);if(isNaN(odd))continue;if(odd>=MIN_ODDS&&odd<=MAX_ODDS)addCandidate(candidateMap,mKey,lg,fix,odd);if(odd>=TIPS_MIN_ODDS&&odd<=TIPS_MAX_ODDS)addCandidate(tipsCandidateMap,mKey,lg,fix,odd);}}}};await sleep(450);}}
    let pool=[...candidateMap.values()].map(m=>({...m,odds:median(m.allOdds).toFixed(2)}));
    let tipsPool=[...tipsCandidateMap.values()].map(m=>({...m,odds:median(m.allOdds).toFixed(2)})).filter(m=>parseFloat(m.odds)>=TIPS_MIN_ODDS&&parseFloat(m.odds)<=TIPS_MAX_ODDS);
    console.log('Tips candidates: '+tipsPool.length+' (Over 2.5, odds '+TIPS_MIN_ODDS+'-'+TIPS_MAX_ODDS+')');
    console.log('Candidates: '+pool.length+' (Over 2.5, odds '+MIN_ODDS+'-'+MAX_ODDS+')');

    // 2) Odfiltruj zapasy, ktere uz vybral fotbal.json / live2.json
    if(dedupSet.size>0){
        const before=pool.length;
        const removed=[];
        pool=pool.filter(m=>{if(isDuplicate(m,dedupSet)){removed.push(m);return false;}return true;});
        console.log('Dedup: odstraneno '+(before-pool.length)+' duplicit (vs fotbal.json/live2.json)');
        for(const m of removed)console.log('   - DUP: '+m.match+' | '+m.league);
        const tipsBefore=tipsPool.length;
        tipsPool=tipsPool.filter(m=>!isDuplicate(m,dedupSet));
        console.log('Dedup (tips): odstraneno '+(tipsBefore-tipsPool.length)+' duplicit');
    }

    const tier1=shuffle(pool.filter(m=>m.tier===1)),tier2=shuffle(pool.filter(m=>m.tier===2)),tier3=shuffle(pool.filter(m=>m.tier===3)),tier4=shuffle(pool.filter(m=>m.tier===4));
    console.log('Tier 1: '+tier1.length+', Tier 2: '+tier2.length+', Tier 3: '+tier3.length+', Tier 4: '+tier4.length+'\n');
    // Vazeny nahodny vyber: nejdriv se podle vah vylosuje TIER, teprve pak zapas z nej.
    // Diky tomu pocet zapasu v tieru neovlivnuje jeho sanci (drive 200x T4 prevazilo 5x T1).
    // Faze 1 drzi stropy: pevny MAX_PER_TIER a zaroven podil MAX_TIER_SHARE z poctu
    // kandidatu tieru, aby vyber zustal nahodny i u malych tieru.
    // Faze 2 stropy uvolni, jen kdyby se jinak nepodarilo naplnit PICK_COUNT.
    const buckets={1:[...tier1],2:[...tier2],3:[...tier3],4:[...tier4]};
    const tierCaps={};
    for(const t of [1,2,3,4]){
        const n=buckets[t].length;
        tierCaps[t]=n===0?0:Math.max(1,Math.min(MAX_PER_TIER[t]??PICK_COUNT,Math.floor(n*MAX_TIER_SHARE)));
    }
    console.log('Stropy tieru (faze 1): T1='+tierCaps[1]+', T2='+tierCaps[2]+', T3='+tierCaps[3]+', T4='+tierCaps[4]);
    const selected=[],usedLeagues=new Set(),countryCount=new Map(),tierCount={1:0,2:0,3:0,4:0};

    function tryPick(useTierCaps){
        while(selected.length<PICK_COUNT){
            const avail=[1,2,3,4].filter(t=>buckets[t].length>0&&(!useTierCaps||tierCount[t]<tierCaps[t]));
            if(avail.length===0)return;
            const totalW=avail.reduce((a,t)=>a+(TIER_WEIGHTS[t]||1),0);
            let r=Math.random()*totalW,tier=avail[avail.length-1];
            for(const t of avail){r-=(TIER_WEIGHTS[t]||1);if(r<=0){tier=t;break;}}
            const m=buckets[tier].pop();
            const lk=m.league+'|'+m.country;
            if(usedLeagues.has(lk))continue;
            if((countryCount.get(m.country)||0)>=MAX_PER_COUNTRY)continue;
            usedLeagues.add(lk);
            countryCount.set(m.country,(countryCount.get(m.country)||0)+1);
            tierCount[tier]++;
            selected.push(m);
            console.log('   [T'+m.tier+'] '+m.match+' | '+m.league+' ('+m.country+') | Over 2.5 @ '+m.odds);
        }
    }

    tryPick(true);
    if(selected.length<PICK_COUNT){
        console.log('   (nedostatek zapasu pri stropech tieru - uvolnuji stropy)');
        tryPick(false);
    }
    console.log('   Rozlozeni tieru: T1='+tierCount[1]+', T2='+tierCount[2]+', T3='+tierCount[3]+', T4='+tierCount[4]);
    console.log('\nVybrano: '+selected.length+'/'+PICK_COUNT);
    if(selected.length<PICK_COUNT)console.log('WARNING: Mene nez '+PICK_COUNT+' zapasu.');
    // === tips.json: 2 nahodne zapasy 1.75-1.9, primarne T1+T2, jinak T3/T4 ====
    const selectedIds=new Set(selected.map(m=>m.fixtureId));
    const tipsSelected=[],tipsUsedLeagues=new Set();
    function tipsPickFrom(tiers){
        const bucket=shuffle(tipsPool.filter(m=>tiers.includes(m.tier)&&!selectedIds.has(m.fixtureId)));
        for(const m of bucket){
            if(tipsSelected.length>=TIPS_PICK_COUNT)return;
            const lk=m.league+'|'+m.country;
            if(tipsUsedLeagues.has(lk))continue;
            tipsUsedLeagues.add(lk);
            selectedIds.add(m.fixtureId);
            tipsSelected.push(m);
        }
    }
    tipsPickFrom([1,2]);
    if(tipsSelected.length<TIPS_PICK_COUNT)tipsPickFrom([3,4]);
    const tips=tipsSelected.map(m=>({league:m.league,match:m.match,kickoff:m.kickoff,tip:m.tip,odds:m.odds}));
    writeFileSync('tips.json',JSON.stringify(tips,null,2),'utf-8');
    console.log('tips.json: '+tips.length+'/'+TIPS_PICK_COUNT+' zapasu ('+TIPS_MIN_ODDS+'-'+TIPS_MAX_ODDS+')');
    for(const m of tipsSelected)console.log('   [T'+m.tier+'] '+m.match+' | '+m.league+' ('+m.country+') | Over 2.5 @ '+m.odds);
    if(tips.length<TIPS_PICK_COUNT)console.log('WARNING: tips.json ma mene nez '+TIPS_PICK_COUNT+' zapasu.');

    const live1=[...tier1,...tier2].map(m=>({league:m.league,match:m.match,kickoff:m.kickoff,tip:m.tip,odds:m.odds}));
    writeFileSync('live1.json',JSON.stringify(live1,null,2),'utf-8');
    console.log('live1.json: '+live1.length+' matches (tier 1+2)');
    if(selected.length===0){writeFileSync('hot.json',JSON.stringify([],null,2),'utf-8');writeFileSync('best.json',JSON.stringify([],null,2),'utf-8');console.log('Zadne zapasy. ('+reqCount+' API req)');process.exit(0);}
    const best=[...selected].sort((a,b)=>parseFloat(b.odds)-parseFloat(a.odds)).slice(0,3).map(m=>({league:m.league,match:m.match,kickoff:m.kickoff,tip:m.tip,odds:m.odds}));
    writeFileSync('best.json',JSON.stringify(best,null,2),'utf-8');
    const grouped=balanceGroups(selected);
    const output=grouped.map(m=>({league:m.league,match:m.match,kickoff:m.kickoff,tip:m.tip,odds:m.odds,group:m.group}));
    writeFileSync('hot.json',JSON.stringify(output,null,2),'utf-8');
    console.log('\n'+output.length+' matches -> hot.json ('+reqCount+' API req)\n');
    const gc=Math.ceil(output.length/2);
    for(let g=1;g<=gc;g++){const gm=output.filter(m=>m.group===g);const go=gm.reduce((a,m)=>a*parseFloat(m.odds),1);console.log('  Gr.'+g+' ('+go.toFixed(2)+'):');gm.forEach(m=>console.log('     ['+m.league+'] '+m.match+' | '+m.tip+' @ '+m.odds+' | '+m.kickoff));}
}

// Spustit jen pri primem volani skriptu - pri importu (testy klasifikace) ne.
if(process.argv[1]&&process.argv[1].endsWith('fetch-matches.mjs')){
    if(!API_KEY){console.error('Chybi API_FOOTBALL_KEY1 env promenna.');process.exit(1);}
    main().catch(err=>{console.error('Chyba:',err);process.exit(1);});
}
