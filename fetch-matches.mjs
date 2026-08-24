import { writeFileSync, existsSync, readFileSync } from 'fs';

const API_KEY = process.env.API_FOOTBALL_KEY1;
if (!API_KEY) { console.error('Chybi API_FOOTBALL_KEY1 env promenna.'); process.exit(1); }

const FOOTBALL_API = 'https://v3.football.api-sports.io';
const MIN_ODDS = 2.1;
const MAX_ODDS = 2.5;
const PICK_COUNT = 6;
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

const TIER1 = [
    // Evropske klubove pohary (UEFA) - v API-Football maji country = 'World'
    ['UEFA Champions League','World'],
    ['UEFA Europa League','World'],
    ['UEFA Europa Conference League','World'],
    ['UEFA Conference League','World'],

    ['Premier League','England'],['La Liga','Spain'],['Serie A','Italy'],
    ['Bundesliga','Germany'],['Ligue 1','France'],['Eredivisie','Netherlands'],
    ['Primeira Liga','Portugal'],['Liga Portugal','Portugal'],
    ['Pro League','Belgium'],['Jupiler Pro League','Belgium'],
    ['Scottish Premiership','Scotland'],['Premiership','Scotland'],
    ['Ekstraklasa','Poland'],['Czech Liga','Czech-Republic'],['Fortuna Liga','Czech-Republic'],
    ['Super League 1','Greece'],['Super Liga','Serbia'],
    ['Austrian Football Bundesliga','Austria'],['Austrian Bundesliga','Austria'],
    ['Eliteserien','Norway'],['Allsvenskan','Sweden'],['Superliga','Denmark'],
    ['Veikkausliiga','Finland'],['Super League','Switzerland'],['Liga I','Romania'],
];

const TIER2 = [
    ['Championship','England'],['2. Bundesliga','Germany'],['Serie B','Italy'],
    ['LaLiga2','Spain'],['Ligue 2','France'],['Eerste Divisie','Netherlands'],
    ['Scottish Championship','Scotland'],['TFF First League','Turkey'],
    ['1. Liga','Czech-Republic'],['Fortuna 1. Liga','Slovakia'],['I Liga','Poland'],
    ['National League','England'],['League One','England'],['League Two','England'],
    ['Challenger Pro League','Belgium'],['Liga de Honra','Portugal'],
    ['2. Liga','Austria'],['1. Division','Denmark'],

    // Domaci pohary
    ['FA Cup','England'],['League Cup','England'],
    ['Copa del Rey','Spain'],
    ['Coppa Italia','Italy'],
    ['DFB Pokal','Germany'],
    ['Coupe de France','France'],
    ['KNVB Beker','Netherlands'],
    ['Taça de Portugal','Portugal'],
    ['Croky Cup','Belgium'],
    ['Scottish Cup','Scotland'],
    ['Puchar Polski','Poland'],
    ['Czech Cup','Czech-Republic'],
    ['Slovak Cup','Slovakia'],
    ['Greek Cup','Greece'],
    ['Türkiye Kupası','Turkey'],
    ['OFB Cup','Austria'],
    ['DBU Pokalen','Denmark'],
    ['Svenska Cupen','Sweden'],
    ['NM Cupen','Norway'],
];

const TIER1_SET = new Set(TIER1.map(([n,c]) => n+'|'+c));
const TIER2_SET = new Set(TIER2.map(([n,c]) => n+'|'+c));

const EUROPE_COUNTRIES = new Set([
    'World', // UEFA klubove pohary
    'England','Spain','Germany','Italy','France','Netherlands','Portugal','Belgium',
    'Greece','Turkey','Poland','Czech-Republic','Slovakia','Scotland','Switzerland',
    'Austria','Sweden','Norway','Denmark','Finland','Serbia','Croatia','Ukraine',
    'Romania','Hungary','Bulgaria','Slovenia','Bosnia And Herzegovina',
]);

function leagueTier(n,c){const k=n+'|'+c;if(TIER1_SET.has(k))return 1;if(TIER2_SET.has(k))return 2;if(EUROPE_COUNTRIES.has(c))return 3;return 4;}
function maskKey(k){if(!k)return'(none)';if(k.length<=8)return'***';return k.slice(0,4)+'...'+k.slice(-4)+' (len='+k.length+')';}
function sleep(ms){return new Promise(r=>setTimeout(r,ms));}
function shuffle(arr){for(let i=arr.length-1;i>0;i--){const j=Math.floor(Math.random()*(i+1));[arr[i],arr[j]]=[arr[j],arr[i]];}return arr;}
function fmtDate(d){return d.toISOString().split('T')[0];}

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
    for(const f of fixtures){fixtureMap.set(f.fixture.id,f);const key=f.league.id+'_'+f.league.season;if(!leagueMap.has(key))leagueMap.set(key,{id:f.league.id,season:f.league.season,name:f.league.name,country:f.league.country,dates:new Set()});leagueMap.get(key).dates.add(fmtDate(new Date(f.fixture.date)));}
    console.log('   '+leagueMap.size+' leagues\n');
    const candidateMap=new Map();
    for(const[,lg]of leagueMap){for(const d of lg.dates){const oddsData=await getLeagueOdds(lg.id,lg.season,d);for(const entry of oddsData){const fix=fixtureMap.get(entry.fixture?.id);if(!fix)continue;const mKey=fix.fixture.id;for(const bm of entry.bookmakers||[]){for(const bet of bm.bets||[]){for(const v of bet.values||[]){if(v.value!=='Over 2.5')continue;const odd=parseFloat(v.odd);if(isNaN(odd)||odd<MIN_ODDS||odd>MAX_ODDS)continue;if(!candidateMap.has(mKey))candidateMap.set(mKey,{fixtureId:mKey,league:lg.name,country:lg.country,match:fix.teams.home.name+' - '+fix.teams.away.name,kickoff:fix.fixture.date,tip:'Over 2.5',tier:leagueTier(lg.name,lg.country),allOdds:[]});candidateMap.get(mKey).allOdds.push(odd);}}}};await sleep(450);}}
    let pool=[...candidateMap.values()].map(m=>({...m,odds:(m.allOdds.reduce((a,b)=>a+b,0)/m.allOdds.length).toFixed(2)}));
    console.log('Candidates: '+pool.length+' (Over 2.5, odds '+MIN_ODDS+'-'+MAX_ODDS+')');

    // 2) Odfiltruj zapasy, ktere uz vybral fotbal.json / live2.json
    if(dedupSet.size>0){
        const before=pool.length;
        const removed=[];
        pool=pool.filter(m=>{if(isDuplicate(m,dedupSet)){removed.push(m);return false;}return true;});
        console.log('Dedup: odstraneno '+(before-pool.length)+' duplicit (vs fotbal.json/live2.json)');
        for(const m of removed)console.log('   - DUP: '+m.match+' | '+m.league);
    }

    const tier1=shuffle(pool.filter(m=>m.tier===1)),tier2=shuffle(pool.filter(m=>m.tier===2)),tier3=shuffle(pool.filter(m=>m.tier===3)),tier4=shuffle(pool.filter(m=>m.tier===4));
    console.log('Tier 1: '+tier1.length+', Tier 2: '+tier2.length+', Tier 3: '+tier3.length+', Tier 4: '+tier4.length+'\n');
    // Vsechny tiery dohromady a nahodne zamichane -> z nich vybirame 6 (max 1 zapas na ligu)
    const shuffledPool=shuffle([...pool]);
    const selected=[],usedLeagues=new Set();
    for(const m of shuffledPool){if(selected.length>=PICK_COUNT)break;const lk=m.league+'|'+m.country;if(usedLeagues.has(lk))continue;usedLeagues.add(lk);selected.push(m);console.log('   [T'+m.tier+'] '+m.match+' | '+m.league+' ('+m.country+') | Over 2.5 @ '+m.odds);}
    console.log('\nVybrano: '+selected.length+'/'+PICK_COUNT);
    if(selected.length<PICK_COUNT)console.log('WARNING: Mene nez '+PICK_COUNT+' zapasu.');
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

main().catch(err=>{console.error('Chyba:',err);process.exit(1);});
