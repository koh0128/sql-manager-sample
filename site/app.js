/* データフローマップ。データは lineage.json（tools/compare.py の出力）を読み込む。
   lineage.json の形式は tools/compare.py の build() を参照。 */
const L=await fetch('lineage.json',{cache:'no-store'})
  .then(r=>{if(!r.ok)throw new Error(`HTTP ${r.status}`);return r.json();})
  .catch(e=>{document.getElementById('detail').innerHTML=`<div class="err">lineage.json を読み込めませんでした（${e.message}）。<br>make site を実行してから build/site を開いてください。</div>`;throw e;});

const TYPES={table:'テーブル',view:'ビュー',dynamic_table:'ダイナミックテーブル',stream:'ストリーム',task:'タスク',procedure:'プロシージャ',function:'関数',stage:'ステージ',file_format:'ファイル形式'};
const STATUS={ok:'正常',failed:'失敗',delayed:'更新遅延',stale:'30日以上未更新',unknown:'未取得'};
const KIND=L.kind_labels||{};
const isProc=t=>t==='task'||t==='procedure';
const C=(t,c)=>t+'.'+c;
const HAS_STATE=!!L.synced_at;
const NOW=HAS_STATE?new Date(L.synced_at):new Date();

const NODES=L.nodes.map(n=>({...n,tags:n.tags||[],cols:n.cols||[],anomalies:n.anomalies||[]}));
const EDGES=L.edges;          // [上流, 下流, 経由する処理, 根拠(s=SQL解析 d=依存 a=実行履歴), 最終実行]
const CALLS=L.calls;          // [呼び出し元タスク, 呼び出し先]
const COL_EDGES=L.col_edges;  // [上流カラム, 下流カラム, 変換式]
const BY={};NODES.forEach(n=>{BY[n.id]=n;});
// リポジトリにも Snowflake 状態にも無い参照先（外部共有DBなど）は仮ノードにする
EDGES.forEach(e=>[e[0],e[1]].forEach(id=>{if(BY[id])return;const p=id.split('.');
  const n={id,db:p[0],schema:p[1],name:p[2],type:'table',owner:'',tags:[],desc:'',status:'unknown',cols:[],sql:'',inRepo:false,anomalies:[],external:true};
  NODES.push(n);BY[id]=n;}));

/* DB は上流にあるものから順に並べ、色トークンを割り当てる */
const depth={};
(function(){const up={};EDGES.forEach(([s,t])=>(up[t]=up[t]||[]).push(s));
  const d=(id,g=new Set())=>{if(depth[id]!=null)return depth[id];if(g.has(id))return 0;g.add(id);
    return depth[id]=Math.max(0,...(up[id]||[]).map(u=>d(u,g)+1));};NODES.forEach(n=>d(n.id));})();
const dbOrder=[...new Set(NODES.map(n=>n.db))].sort((a,b)=>{const m=x=>Math.min(...NODES.filter(n=>n.db===x&&!isProc(n.type)).map(n=>depth[n.id]));return m(a)-m(b)||a.localeCompare(b);});
const DBS={};dbOrder.forEach((db,i)=>{DBS[db]={tok:`--db${i%6+1}`,note:(L.databases?.[db]?.description||'')};});

/* 利用状況：下流オブジェクト数（直接／推移的な全体）と 30日参照回数 */
const DOWN={};EDGES.forEach(([s,t])=>(DOWN[s]=DOWN[s]||new Set()).add(t));
NODES.forEach(n=>{
  const seen=new Set(),st=[n.id];
  while(st.length)(DOWN[st.pop()]||[]).forEach(y=>{if(!seen.has(y)){seen.add(y);st.push(y);}});
  n.down=(DOWN[n.id]||new Set()).size;n.downAll=seen.size;n.dt=n.freshAt?new Date(n.freshAt):null;
});
const fmtN=v=>v==null?'—':v>=10000?Math.round(v/1000)+'k':v>=1000?(v/1000).toFixed(1).replace(/\.0$/,'')+'k':String(v);
const usageLabel=n=>`↓${n.down}（全${n.downAll}）· 参照 ${fmtN(n.reads)}${n.reads==null?'':'/30日'}`;

/* 更新時期 */
const rel=d=>{if(!d)return '—';const m=Math.max(0,Math.round((NOW-d)/60000));return m<60?`${m}分前`:m<1440?`${Math.round(m/60)}時間前`:`${Math.round(m/1440)}日前`;};
const fmtT=s=>s?new Date(s).toLocaleString('ja-JP',{timeZone:'Asia/Tokyo',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'}):'—';
const FR={fr1:'1時間以内',fr2:'24時間以内',fr3:'7日以内',fr4:'7日より前',fr0:'未取得'};
const RD={rd0:'0回',rd1:'100回未満',rd2:'1,000回未満',rd3:'1,000回以上',rdx:'未取得'};
const frBucket=d=>{if(!d)return 'fr0';const h=(NOW-d)/3600000;return h<=1?'fr1':h<=24?'fr2':h<=168?'fr3':'fr4';};
const rdBucket=v=>v==null?'rdx':v===0?'rd0':v<100?'rd1':v<1000?'rd2':'rd3';
const timeLabel=n=>!HAS_STATE?(n.cad?`更新頻度 ${n.cad}`:'更新 —'):n.deployed===false?'未デプロイ':
  n.type==='view'?`データ ${rel(n.dt)}（上流基準）`:`更新 ${rel(n.dt)}${n.cad?' · '+n.cad:''}`;
const tableOf=c=>c.slice(0,c.lastIndexOf('.'));
const colOf=c=>c.slice(c.lastIndexOf('.')+1);
const esc=s=>String(s??'').replace(/[&<>"]/g,ch=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[ch]));
const reduced=matchMedia('(prefers-reduced-motion: reduce)').matches;

const SCHEMAS={};NODES.forEach(n=>{(SCHEMAS[n.db]=SCHEMAS[n.db]||{})[n.schema]=(SCHEMAS[n.db][n.schema]||0)+1;});
const state={color:HAS_STATE?'fresh':'db',schemas:new Set(NODES.map(n=>n.db+'.'+n.schema)),types:new Set(Object.keys(TYPES)),statuses:new Set(Object.keys(STATUS)),
  tag:'',owner:'',showProc:true,onlyAnom:false,hops:6,sel:null,col:null,query:''};
document.getElementById('f-color').value=state.color;

/* ヘッダーの同期情報 */
document.getElementById('sync').innerHTML=HAS_STATE
  ?`リポジトリ <span class="mono">${esc(L.commit||'—')}</span> の SQL ＋ Snowflake の状態 ／ 最終同期 <span class="mono">${esc(fmtT(L.synced_at))}</span>`
  :`リポジトリ <span class="mono">${esc(L.commit||'—')}</span> の SQL から生成 ／ <b>Snowflake の状態は未取得</b>（実行状況・異常は表示されません）`;
document.getElementById('sample').hidden=L.account!=='SAMPLE';

/* ---------- フィルタUI ---------- */
function chk(container,key,val,label,sw){
  const id='f-'+key+'-'+val;
  const l=document.createElement('label');l.className='chk';
  l.innerHTML=`<input type="checkbox" id="${id}" checked>${sw?`<span class="sw" style="background:var(${sw})"></span>`:''}${esc(label)}`;
  l.querySelector('input').addEventListener('change',e=>{e.target.checked?state[key].add(val):state[key].delete(val);refresh(true);});
  container.appendChild(l);
}
/* DB → スキーマの階層フィルタ。DBのチェックで配下スキーマを一括切替、一部選択時は中間表示 */
const dbTree=document.getElementById('f-db');
Object.entries(DBS).forEach(([db,v])=>{
  const sch=Object.keys(SCHEMAS[db]||{}).sort(),total=sch.reduce((a,s)=>a+SCHEMAS[db][s],0);
  const row=document.createElement('div');row.className='dbrow';
  row.innerHTML=`<label class="chk"><input type="checkbox" id="f-db-${db}" checked><span class="sw" style="background:var(${v.tok})"></span>${esc(db)}<span class="n">${total}</span></label>
    <div class="schemas">${sch.map(s=>`<label class="chk"><input type="checkbox" id="f-sch-${db}-${s}" data-k="${db}.${s}" checked>${esc(s)}<span class="n">${SCHEMAS[db][s]}</span></label>`).join('')}</div>`;
  const dbBox=row.querySelector(`#f-db-${db}`),boxes=[...row.querySelectorAll('[data-k]')];
  const sync=()=>{const on=boxes.filter(b=>b.checked).length;dbBox.checked=on===boxes.length;dbBox.indeterminate=on>0&&on<boxes.length;};
  dbBox.addEventListener('change',()=>{boxes.forEach(b=>{b.checked=dbBox.checked;dbBox.checked?state.schemas.add(b.dataset.k):state.schemas.delete(b.dataset.k);});sync();refresh(true);});
  boxes.forEach(b=>b.addEventListener('change',()=>{b.checked?state.schemas.add(b.dataset.k):state.schemas.delete(b.dataset.k);sync();refresh(true);}));
  row._sync=sync;dbTree.appendChild(row);
});
function setAllSchemas(on){dbTree.querySelectorAll('[data-k]').forEach(b=>{b.checked=on;on?state.schemas.add(b.dataset.k):state.schemas.delete(b.dataset.k);});
  dbTree.querySelectorAll('.dbrow').forEach(r=>r._sync());refresh(true);}
document.getElementById('b-sch-all').addEventListener('click',()=>setAllSchemas(true));
document.getElementById('b-sch-none').addEventListener('click',()=>setAllSchemas(false));
Object.entries(TYPES).filter(([k])=>NODES.some(n=>n.type===k)).forEach(([k,v])=>chk(document.getElementById('f-type'),'types',k,v));
Object.entries(STATUS).forEach(([k,v])=>chk(document.getElementById('f-status'),'statuses',k,v));
function fillSelect(id,values,key){
  const s=document.getElementById(id);
  s.innerHTML='<option value="">すべて</option>'+values.map(v=>`<option>${esc(v)}</option>`).join('');
  s.addEventListener('change',()=>{state[key]=s.value;refresh(true);});
}
fillSelect('f-tag',[...new Set(NODES.flatMap(n=>n.tags))].sort(),'tag');
fillSelect('f-owner',[...new Set(NODES.map(n=>n.owner))].sort(),'owner');
document.getElementById('f-proc').addEventListener('change',e=>{state.showProc=e.target.checked;build();refresh(true);});
document.getElementById('f-anom').addEventListener('change',e=>{state.onlyAnom=e.target.checked;refresh(true);});
document.getElementById('f-anom').disabled=!HAS_STATE;
const hops=document.getElementById('f-hops'),hopsVal=document.getElementById('f-hops-val');
hops.addEventListener('input',()=>{state.hops=+hops.value;hopsVal.textContent=state.hops>=6?'すべて':`${state.hops} ホップ以内`;refresh(true);});
document.getElementById('b-fit').addEventListener('click',()=>cy.animate({fit:{eles:cy.elements(':visible'),padding:30}},{duration:reduced?0:300}));
document.getElementById('b-clear').addEventListener('click',()=>select(null));
if(innerWidth<=900)document.getElementById('filters').open=false;

/* ---------- 集計 ---------- */
const ANOMS=NODES.flatMap(n=>n.anomalies);
function renderStats(){
  const data=NODES.filter(n=>!isProc(n.type)),proc=NODES.filter(n=>isProc(n.type));
  const cnt=s=>NODES.filter(n=>n.status===s).length;
  const kinds=Object.keys(KIND).filter(k=>k!=='task_failed'&&ANOMS.some(a=>a.kind===k));
  let h=`<span class="stat">オブジェクト <b>${data.length}</b></span><span class="stat">処理 <b>${proc.length}</b></span>`+
   `<span class="stat">依存 <b>${EDGES.length}</b></span><span class="stat">カラム依存 <b>${COL_EDGES.length}</b></span>`;
  if(HAS_STATE)h+=['failed','delayed','stale'].map(s=>`<button class="stat s-${s}" data-only="${s}">${STATUS[s]} <b>${cnt(s)}</b></button>`).join('')+
    kinds.map(k=>`<button class="stat s-anom" data-kind="${k}">${esc(KIND[k])} <b>${new Set(ANOMS.filter(a=>a.kind===k).map(a=>a.object)).size}</b></button>`).join('');
  document.getElementById('stats').innerHTML=h;
  document.querySelectorAll('[data-only]').forEach(b=>b.addEventListener('click',()=>{
    const id=NODES.find(n=>n.status===b.dataset.only&&!isProc(n.type))?.id||NODES.find(n=>n.status===b.dataset.only)?.id;if(id)select(id);}));
  document.querySelectorAll('[data-kind]').forEach(b=>b.addEventListener('click',()=>{
    const a=ANOMS.find(x=>x.kind===b.dataset.kind);if(a&&BY[a.object])select(a.object);}));
}

/* ---------- グラフ ---------- */
const cv=v=>getComputedStyle(document.documentElement).getPropertyValue(v).trim();
function graphStyle(){
  const c={ink:cv('--ink'),muted:cv('--muted'),line:cv('--line'),surface:cv('--surface'),s2:cv('--surface-2'),bg:cv('--bg'),
    accent:cv('--accent'),fail:cv('--fail'),warn:cv('--warn'),stale:cv('--stale'),edge:cv('--edge'),db1:cv('--db1'),db2:cv('--db2'),db3:cv('--db3')};
  return [
   {selector:'node',style:{label:'data(label)','font-family':'"JetBrains Mono", ui-monospace, monospace','font-size':10.5,color:c.ink,
     'text-valign':'center','text-halign':'center','text-wrap':'wrap','text-max-width':200,width:212,height:72,shape:'round-rectangle',
     'background-color':c.surface,'border-width':1.5,'border-color':c.line,'line-height':1.35}},
   ...(state.color==='db'?Object.entries(DBS).map(([k,v])=>({selector:`node[db="${k}"]`,style:{'background-color':cv(v.tok)}})):
    (state.color==='fresh'?Object.keys(FR):Object.keys(RD)).map(k=>({selector:'node.data.'+k,style:{'background-color':cv('--'+k)}}))),
   {selector:'node.view',style:{'border-style':'double','border-width':4}},
   {selector:'node.dynamic_table',style:{shape:'cut-rectangle'}},
   {selector:'node.stream',style:{shape:'barrel'}},
   {selector:'node.proc',style:{width:26,height:26,'text-valign':'bottom','text-margin-y':4,'font-size':9.5,color:c.muted,
     'background-color':c.surface,'border-color':c.muted,'text-max-width':150}},
   {selector:'node.task',style:{shape:'round-diamond'}},
   {selector:'node.procedure',style:{shape:'round-hexagon'}},
   {selector:'node.failed',style:{'border-color':c.fail,'border-width':3,'border-style':'solid'}},
   {selector:'node.delayed',style:{'border-color':c.warn,'border-width':3}},
   {selector:'node.stale',style:{'border-style':'dashed','border-color':c.stale,color:c.muted,'background-opacity':.5}},
   {selector:'edge',style:{'curve-style':'bezier',width:1.4,'line-color':c.edge,'target-arrow-color':c.edge,'target-arrow-shape':'triangle','arrow-scale':.8}},
   {selector:'edge.unconfirmed',style:{'line-style':'dashed','line-dash-pattern':[5,4]}},
   {selector:'edge.calls',style:{'line-style':'dotted','target-arrow-shape':'tee'}},
   {selector:'edge.failedpath',style:{'line-color':c.fail,'target-arrow-color':c.fail}},
   {selector:'edge.offcode',style:{'line-color':c.warn,'target-arrow-color':c.warn,'line-style':'dashed','line-dash-pattern':[2,3],width:2}},
   {selector:'node.anom',style:{'font-weight':500}},
   {selector:'node.match',style:{'underlay-color':c.warn,'underlay-opacity':.28,'underlay-padding':6,'underlay-shape':'round-rectangle'}},
   {selector:'.faded',style:{opacity:.13}},
   {selector:'edge.hl, edge.colhl',style:{'line-color':c.accent,'target-arrow-color':c.accent,width:2.4,label:'data(label)','font-size':9,
     'font-family':'"IBM Plex Sans JP", sans-serif',color:c.muted,'text-background-color':c.bg,'text-background-opacity':1,'text-background-padding':2}},
   {selector:'edge.colhl.failedpath, edge.hl.failedpath',style:{'line-color':c.fail,'target-arrow-color':c.fail}},
   {selector:'node.colhl',style:{'underlay-color':c.accent,'underlay-opacity':.16,'underlay-padding':5,'underlay-shape':'round-rectangle'}},
   {selector:'node.sel',style:{'underlay-color':c.accent,'underlay-opacity':.32,'underlay-padding':7,'underlay-shape':'round-rectangle'}},
   {selector:'.hidden, .hopout',style:{display:'none'}},
  ];
}
const cy=cytoscape({container:document.getElementById('cy'),style:graphStyle(),minZoom:.15,maxZoom:2.5,wheelSensitivity:.25,boxSelectionEnabled:false});
let edgeMap={};
function build(){
  const els=[];edgeMap={};const seen=new Set();
  NODES.forEach(n=>{
    if(isProc(n.type)&&!state.showProc)return;
    const mark=n.anomalies.some(a=>a.kind!=='task_failed')?'⚠ ':'';
    const flag=n.external?' · 外部':!n.inRepo?' · 未登録':n.deployed===false?' · 未デプロイ':'';
    const label=isProc(n.type)?`${mark}${n.name}\n${n.cad||'—'} · ${HAS_STATE?rel(n.dt):'未取得'}`:`${mark}${n.name}\n${n.schema}${flag}\n${usageLabel(n)}\n${timeLabel(n)}`;
    const cls=[n.type,n.status,isProc(n.type)?'proc':'data'];if(!isProc(n.type))cls.push(frBucket(n.dt),rdBucket(n.reads));
    if(mark)cls.push('anom');
    els.push({group:'nodes',data:{id:n.id,label,db:n.db},classes:cls.join(' ')});
  });
  const add=(s,t,cls,label,key)=>{const id=s+'>'+t;if(!seen.has(id)){seen.add(id);els.push({group:'edges',data:{id,source:s,target:t,label:label||''},classes:cls});}
    (edgeMap[key]=edgeMap[key]||[]).push(id);};
  EDGES.forEach(([s,t,via,src])=>{
    const key=s+'>'+t,vn=via&&BY[via],fp=vn&&vn.status==='failed'?' failedpath':'';
    const conf=(src.includes('a')?'confirmed':'unconfirmed')+(src.includes('s')?'':' offcode');
    if(vn&&state.showProc){add(s,via,conf,'',key);add(via,t,conf+fp,'',key);}
    else add(s,t,conf+fp,vn?vn.name:(via||''),key);
  });
  if(state.showProc)CALLS.forEach(([a,b])=>add(a,b,'calls'+(BY[a].status==='failed'?' failedpath':''),'呼び出し','calls'));
  cy.elements().remove();cy.add(els);
}
cy.on('tap','node',e=>select(e.target.id()));
cy.on('tap',e=>{if(e.target===cy)select(null);});
cy.on('mouseover','node',()=>document.getElementById('cy').style.cursor='pointer');
cy.on('mouseout','node',()=>document.getElementById('cy').style.cursor='');

function visibleBase(n){
  return state.schemas.has(n.db+'.'+n.schema)&&state.types.has(n.type)&&state.statuses.has(n.status)&&
   (!state.tag||n.tags.includes(state.tag))&&(!state.owner||n.owner===state.owner)&&
   (!state.onlyAnom||n.anomalies.length>0||n.status==='failed'||n.status==='delayed');
}
function within(start,max,dir){
  const dist=new Map([[start,0]]),q=[start];
  while(q.length){const id=q.shift(),d=dist.get(id),el=cy.getElementById(id);
    (dir==='down'?el.outgoers('node'):el.incomers('node')).forEach(m=>{
      if(m.hasClass('hidden'))return;const nd=d+(m.hasClass('proc')?0:1);if(nd>max)return;
      if(!dist.has(m.id())||dist.get(m.id())>nd){dist.set(m.id(),nd);q.push(m.id());}});}
  return dist;
}
function refresh(relayout){
  cy.batch(()=>{
    cy.nodes().forEach(el=>el.toggleClass('hidden',!visibleBase(BY[el.id()])));
    cy.nodes().removeClass('hopout');
    const sel=state.sel&&cy.getElementById(state.sel);
    if(sel&&sel.nonempty()&&state.hops<6){
      const keep=new Set([...within(state.sel,state.hops,'up').keys(),...within(state.sel,state.hops,'down').keys()]);
      cy.nodes().forEach(el=>{if(!keep.has(el.id()))el.addClass('hopout');});
    }
  });
  highlight();
  if(relayout)layout();
}
function layout(){
  const vis=cy.nodes().not('.hidden, .hopout');
  vis.union(vis.edgesWith(vis)).layout({name:'dagre',rankDir:'LR',nodeSep:14,rankSep:70,edgeSep:6,animate:!reduced,animationDuration:320,fit:true,padding:30,
    stop:()=>focusSel()}).run();
}
function focusSel(){
  if(!state.sel)return;const n=cy.getElementById(state.sel);if(n.empty()||!n.visible())return;
  const eles=state.col?cy.elements('.colhl, .sel'):n.union(n.predecessors()).union(n.successors());
  cy.animate({fit:{eles:eles.filter(':visible'),padding:50}},{duration:reduced?0:350});
}

/* ---------- カラム追跡 ---------- */
function walk(c,dir,depth,out,seen){
  COL_EDGES.filter(e=>dir==='up'?e[1]===c:e[0]===c).forEach(e=>{
    const k=e[0]+'>'+e[1];if(seen.has(k))return;seen.add(k);
    out.push({from:e[0],to:e[1],tf:e[2],depth});walk(dir==='up'?e[0]:e[1],dir,depth+1,out,seen);});
  return out;
}
function trace(c){return{up:walk(c,'up',0,[],new Set()),down:walk(c,'down',0,[],new Set())};}

function highlight(){
  cy.batch(()=>{
    cy.elements().removeClass('faded hl sel colhl match');
    const sel=state.sel&&cy.getElementById(state.sel);
    if(sel&&sel.nonempty()){
      cy.elements().addClass('faded');sel.removeClass('faded').addClass('sel');
      if(state.col){
        const t=trace(C(state.sel,state.col));
        [...t.up,...t.down].forEach(x=>{
          [tableOf(x.from),tableOf(x.to)].forEach(id=>cy.getElementById(id).removeClass('faded').addClass('colhl'));
          (edgeMap[tableOf(x.from)+'>'+tableOf(x.to)]||[]).forEach(eid=>{const e=cy.getElementById(eid);
            e.removeClass('faded').addClass('colhl');e.source().removeClass('faded');e.target().removeClass('faded');});
        });
      }else{
        sel.predecessors().union(sel.successors()).removeClass('faded').addClass('hl');
      }
    }
    if(state.query){
      const q=state.query.toLowerCase();
      const m=cy.nodes().filter(el=>matchNode(BY[el.id()],q));
      m.addClass('match');
      if(!state.sel){cy.elements().addClass('faded');m.removeClass('faded');}
    }
  });
}
const matchNode=(n,q)=>n.id.toLowerCase().includes(q)||n.cols.some(c=>c[0].toLowerCase().includes(q));

/* ---------- 選択 ---------- */
function select(id,col){
  state.sel=id||null;state.col=col||null;  hops.disabled=!state.sel;
  try{history.replaceState(null,'',state.sel?'#'+state.sel:location.pathname+location.search);}catch(e){}
  refresh(state.hops<6);
  if(!(state.hops<6))focusSel();
  renderDetail();
  renderColGraph();
}

/* ---------- カラム単位のグラフ ----------
   選んだカラムの上流・下流に関わるテーブルを箱（複合ノード）にし、中に関係するカラムだけを並べる。
   テーブルの列位置は「選んだテーブル=0、上流は-1,-2…、下流は+1,+2…」で決め、座標は自前で配置する */
let cy2=null;
function colStyle(){
  const c={ink:cv('--ink'),muted:cv('--muted'),line:cv('--line'),surface:cv('--surface'),s2:cv('--surface-2'),bg:cv('--bg'),
    accent:cv('--accent'),fail:cv('--fail'),warn:cv('--warn'),stale:cv('--stale')};
  return [
   {selector:'node',style:{label:'data(label)','font-family':'"JetBrains Mono", ui-monospace, monospace','font-size':10.5,color:c.ink,
     'text-valign':'center','text-halign':'center',shape:'round-rectangle',width:200,height:22,'background-color':c.surface,'border-width':1,'border-color':c.line}},
   {selector:':parent',style:{'background-color':c.s2,'border-width':1.5,'border-color':c.line,padding:10,shape:'round-rectangle',
     'text-valign':'top','text-halign':'center','text-margin-y':-5,'font-size':11,'font-weight':500,color:c.ink}},
   {selector:':parent.failed',style:{'border-color':c.fail,'border-width':2.5}},
   {selector:':parent.delayed',style:{'border-color':c.warn,'border-width':2.5}},
   {selector:':parent.stale',style:{'border-style':'dashed','border-color':c.stale}},
   {selector:'node.root',style:{'background-color':c.accent,color:c.surface,'border-color':c.accent,'font-weight':500}},
   {selector:'edge',style:{'curve-style':'bezier',width:1.6,'line-color':c.accent,'target-arrow-color':c.accent,'target-arrow-shape':'triangle','arrow-scale':.8,
     label:'data(label)','font-size':9,'font-family':'"JetBrains Mono", monospace',color:c.muted,
     'text-background-color':c.bg,'text-background-opacity':1,'text-background-padding':2}},
  ];
}
function renderColGraph(){
  const wrap=document.getElementById('colwrap');
  if(!state.sel||!state.col){wrap.hidden=true;return;}
  wrap.hidden=false;
  if(!cy2){
    cy2=cytoscape({container:document.getElementById('cy2'),style:colStyle(),minZoom:.2,maxZoom:2.5,wheelSensitivity:.25,boxSelectionEnabled:false,autoungrabify:true});
    cy2.on('tap','node',e=>{const id=e.target.id();
      if(id.startsWith('C:')){const c=id.slice(2);select(tableOf(c),colOf(c));}else select(id.slice(2));});
    cy2.on('mouseover','node',()=>document.getElementById('cy2').style.cursor='pointer');
    cy2.on('mouseout','node',()=>document.getElementById('cy2').style.cursor='');
  }
  cy2.resize();
  const root=C(state.sel,state.col),t=trace(root),es=[...t.up,...t.down];
  document.getElementById('colroot').textContent=root;
  const cols=new Set([root]);es.forEach(e=>{cols.add(e.from);cols.add(e.to);});
  const rank=new Map([[state.sel,0]]);
  for(let changed=true,g=0;changed&&g<50;g++){changed=false;
    t.up.forEach(e=>{const a=tableOf(e.from),b=tableOf(e.to);if(rank.has(b)){const r=rank.get(b)-1;if(!rank.has(a)||rank.get(a)>r){rank.set(a,r);changed=true;}}});
    t.down.forEach(e=>{const a=tableOf(e.from),b=tableOf(e.to);if(rank.has(a)){const r=rank.get(a)+1;if(!rank.has(b)||rank.get(b)<r){rank.set(b,r);changed=true;}}});}
  const byRank={};rank.forEach((r,tid)=>(byRank[r]=byRank[r]||[]).push(tid));
  const X=290,ROW=27,GAP=58,els=[];
  Object.entries(byRank).forEach(([r,tids])=>{
    tids.sort();
    const blocks=tids.map(tid=>{const n=BY[tid];return{tid,n,cs:n.cols.map(c=>c[0]).filter(c=>cols.has(C(tid,c)))};});
    const total=blocks.reduce((a,b)=>a+b.cs.length*ROW+GAP,0)-GAP;
    let y=-total/2;
    blocks.forEach(({tid,n,cs})=>{
      els.push({group:'nodes',data:{id:'T:'+tid,label:`${n.name}  ${n.db}.${n.schema}`},classes:n.status});
      cs.forEach((c,i)=>{const typ=(n.cols.find(x=>x[0]===c)||[])[1]||'';
        els.push({group:'nodes',data:{id:'C:'+C(tid,c),parent:'T:'+tid,label:`${c}  ${typ}`},position:{x:+r*X,y:y+i*ROW},classes:C(tid,c)===root?'root':''});});
      y+=cs.length*ROW+GAP;
    });
  });
  const short=s=>s&&s.length>30?s.slice(0,29)+'…':(s||'');
  const ids=new Set(els.map(x=>x.data.id));
  es.filter(e=>ids.has('C:'+e.from)&&ids.has('C:'+e.to)).forEach(e=>els.push({group:'edges',data:{id:'CE:'+e.from+'>'+e.to,source:'C:'+e.from,target:'C:'+e.to,label:short(e.tf)}}));
  cy2.elements().remove();cy2.add(els);
  cy2.layout({name:'preset',fit:true,padding:40}).run();
}
document.getElementById('b-colback').addEventListener('click',()=>select(state.sel,null));

/* ---------- 詳細パネル ---------- */
const fmtSql=s=>esc(s).replace(/\b(CREATE|OR|REPLACE|TABLE|VIEW|DYNAMIC|TASK|STREAM|PROCEDURE|SELECT|FROM|JOIN|LEFT|USING|WHERE|GROUP BY|AS|ON|MERGE|INTO|WHEN|MATCHED|NOT|THEN|UPDATE|SET|INSERT|VALUES|CALL|SCHEDULE|WAREHOUSE|TARGET_LAG|RETURNS|LANGUAGE|BEGIN|END|RETURN|CASE|ELSE|APPEND_ONLY|AND)\b/g,'<span class="kw">$1</span>');
function defaultSql(n){
  if(n.sql)return n.sql;
  const kw=n.type==='view'?'VIEW':'TABLE';
  if(n.type==='view'){const ups=EDGES.filter(e=>e[1]===n.id).map(e=>e[0]);
    return `CREATE OR REPLACE VIEW ${n.id} AS\nSELECT\n  ${n.cols.map(c=>c[0]).join(',\n  ')}\nFROM ${ups.join('\nJOIN ')} ...;`;}
  return `CREATE OR REPLACE ${kw} ${n.id} (\n${n.cols.map(c=>`  ${c[0].padEnd(14)} ${c[1]}${c[2]?` COMMENT '${c[2]}'`:''}`).join(',\n')}\n);`;
}
function lnk(id){const n=BY[id];return n?`<button class="lnk" data-go="${esc(id)}"><span class="st ${n.status}"></span>${esc(id)}</button>`:`<span class="badge">${esc(id)}</span>`;}
function renderDetail(){
  const el=document.getElementById('detail');
  if(!state.sel){el.innerHTML=overview();bind(el);return;}
  const n=BY[state.sel];
  const ups=[...new Set(EDGES.filter(e=>e[1]===n.id).map(e=>e[0]).concat(EDGES.filter(e=>e[2]===n.id).map(e=>e[0])).concat(CALLS.filter(c=>c[1]===n.id).map(c=>c[0])))];
  const downs=[...new Set(EDGES.filter(e=>e[0]===n.id).map(e=>e[1]).concat(EDGES.filter(e=>e[2]===n.id).map(e=>e[1])).concat(CALLS.filter(c=>c[0]===n.id).map(c=>c[1])))];
  const writers=[...new Set(EDGES.filter(e=>e[1]===n.id&&e[2]).map(e=>e[2]))];
  const proc=isProc(n.type);
  const pills=[`<span class="badge">${TYPES[n.type]||n.type}</span>`,`<span class="pill p-${n.status}">${STATUS[n.status]}</span>`,
    ...[...new Set(n.anomalies.filter(a=>a.kind!=='task_failed').map(a=>a.label))].map(l=>`<span class="pill p-norepo">${esc(l)}</span>`)];
  let h=`<div class="dhead"><div class="badges">${pills.join('')}</div>
    <div class="dname">${esc(n.name)}</div><div class="dpath">${esc(n.db)}.${esc(n.schema)}</div></div>`;
  if(n.error)h+=`<div class="err">最終実行 ${esc(fmtT(n.lastAltered))} に失敗<br>${esc(n.error).replace(/\n/g,'<br>')}</div>`;
  if(n.status==='delayed')h+=`<div class="warnbox">上流の処理が失敗したため、データが ${esc(fmtT(n.freshAt))} 時点のままです。</div>`;
  if(n.external)h+=`<div class="warnbox">リポジトリにも Snowflake の取得対象にも無いオブジェクトです（外部の共有DBなど）。</div>`;
  h+=n.anomalies.filter(a=>a.kind!=='task_failed').map(anomBox).join('');
  h+=`<p class="desc${n.desc?'':' empty'}">${n.desc?esc(n.desc):'説明が未記入です（yml の description に追記してください）'}</p>`;
  const file=n.path?(L.repo_url?`<a href="${esc(L.repo_url)}/blob/${esc(L.branch||'main')}/${esc(n.path)}" target="_blank" rel="noopener">${esc(n.path)}</a>`:esc(n.path)):'—（リポジトリに無し）';
  h+=`<dl><dt>オーナー</dt><dd>${esc(n.owner||'—')}</dd><dt>タグ</dt><dd>${n.tags.map(t=>`<span class="badge">${esc(t)}</span>`).join(' ')||'—'}</dd>`+
     (proc?`<dt>スケジュール</dt><dd>${esc(n.cad||'—')}</dd><dt>最終実行</dt><dd class="mono">${esc(fmtT(n.lastAltered))}</dd>`
          :`<dt>更新方法</dt><dd>${esc(n.refresh||'—')}</dd>`+
           (n.type==='view'?`<dt>最終参照</dt><dd class="mono">${esc(fmtT(n.lastRead))}</dd>`:`<dt>最終更新</dt><dd class="mono">${esc(fmtT(n.lastAltered))}</dd>`)+
           `<dt>更新頻度</dt><dd>${esc(n.cad||(n.type==='view'?'—（ビュー）':'—'))}</dd>${n.sla?`<dt>SLA</dt><dd>${esc(n.sla)}</dd>`:''}`+
           `<dt>データの鮮度</dt><dd>${HAS_STATE?rel(n.dt):'未取得'}${n.type==='view'&&n.dt?'（最も古い上流を基準）':''}</dd>`+
           (n.rows!=null?`<dt>行数</dt><dd class="mono">${n.rows.toLocaleString()}</dd>`:'')+
           `<dt>後続での利用</dt><dd>直接 <b class="mono">${n.down}</b> ／ 下流全体 <b class="mono">${n.downAll}</b> オブジェクト</dd>`+
           `<dt>参照回数</dt><dd>${n.reads==null?'未取得':`<b class="mono">${n.reads.toLocaleString()}</b> 回 / 直近30日`}</dd>`)+
     `<dt>ファイル</dt><dd class="mono">${file}</dd></dl>`;
  if(writers.length)h+=`<div><h3>この${TYPES[n.type]}を更新する処理</h3><div class="links">${writers.map(w=>BY[w]?lnk(w):`<span class="badge">${esc(w)}</span>`).join('')}</div></div>`;
  h+=`<div><h3>上流（${ups.length}）</h3><div class="links">${ups.map(lnk).join('')||'<span class="hint">なし（起点）</span>'}</div></div>`;
  h+=`<div><h3>下流（${downs.length}）</h3><div class="links">${downs.map(lnk).join('')||'<span class="hint">なし（終点）</span>'}</div></div>`;
  if(n.cols.length){
    h+=`<div><h3>カラム（行をクリックで由来を追跡）</h3><div class="tblwrap"><table><thead><tr><th>カラム</th><th>型</th><th>説明</th><th></th></tr></thead><tbody>`+
      n.cols.map(([c,t,d])=>{const tr=trace(C(n.id,c));
        return `<tr class="col${state.col===c?' on':''}" data-col="${esc(c)}" tabindex="0"><td class="cn">${esc(c)}</td><td class="ct">${esc(t)}</td><td class="cd${d?'':' empty'}">${d?esc(d):'未記入'}</td><td class="cnt" title="上流 / 下流のカラム数">↑${tr.up.length} ↓${tr.down.length}</td></tr>`;}).join('')+
      `</tbody></table></div></div>`;
  }
  if(state.col)h+=traceBox(n.id,state.col);
  h+=`<div><h3>定義</h3><pre class="sql">${fmtSql(defaultSql(n))}</pre></div>`;
  el.innerHTML=h;bind(el);
  if(state.col)el.querySelector('.trace')?.scrollIntoView({block:'nearest',behavior:reduced?'auto':'smooth'});
}
function fmtDiff(d){
  return esc(d).split('\n').map(l=>l.startsWith('+')&&!l.startsWith('+++')?`<span class="da">${l}</span>`:
    l.startsWith('-')&&!l.startsWith('---')?`<span class="dr">${l}</span>`:l).join('\n');
}
function anomBox(a){
  let b=`<div class="anombox lv-${esc(a.level)}"><div class="at"><span class="pill p-norepo">${esc(a.label)}</span>${esc(a.title)}</div>`;
  if(a.kind==='drift'&&a.diff)b+=`<details open><summary>差分（GitHub → Snowflake）</summary><pre class="sql diff">${fmtDiff(a.diff)}</pre></details>
    <span class="hint">リポジトリ側に取り込むなら Snowflake の定義で PR を作成、戻すなら main を再デプロイしてください。</span>`;
  if(a.kind==='manual_ddl')b+=`<dl><dt>実行者</dt><dd class="mono">${esc(a.user)}（${esc(a.role)}）</dd><dt>日時</dt><dd class="mono">${esc(fmtT(a.at))}</dd><dt>種別</dt><dd class="mono">${esc(a.query_type)}</dd></dl>`+
    (a.sql?`<details><summary>実行された SQL</summary><pre class="sql">${fmtSql(a.sql)}</pre></details>`:'');
  if(a.kind==='unregistered')b+=`<span class="hint">下の定義を <span class="mono">${esc(a.suggest_path)}</span> として PR を作成してください。</span>`;
  if(a.kind==='hidden_flow')b+=`<span class="hint">上流: ${lnk(a.source)} 最終 ${esc(fmtT(a.at))}</span>`;
  if(a.kind==='undeployed')b+=`<span class="hint">main にマージ済みなら deploy の実行状況を確認してください。</span>`;
  return b+'</div>';
}
function traceBox(tid,col){
  const t=trace(C(tid,col));
  const item=(x,dir)=>{const c=dir==='up'?x.from:x.to;
    return `<div class="ti" style="margin-left:${x.depth*12}px"><button data-go="${esc(tableOf(c))}" data-gocol="${esc(colOf(c))}">${dir==='up'?'←':'→'} ${esc(c)}</button>${x.tf?`<span class="tf">${esc(x.tf)}</span>`:''}</div>`;};
  return `<div class="trace"><div class="tt">${esc(tid)}.${esc(col)}</div>
    <div><h3>由来（上流 ${t.up.length}）</h3><div class="tl">${t.up.map(x=>item(x,'up')).join('')||'<span class="hint">このカラムが起点です</span>'}</div></div>
    <div><h3>派生（下流 ${t.down.length}）</h3><div class="tl">${t.down.map(x=>item(x,'down')).join('')||'<span class="hint">このカラムを使う先はありません</span>'}</div></div>
    <div class="btnrow"><button class="btn" data-clearcol>カラム追跡を解除</button></div></div>`;
}
function usageBlocks(){
  const data=NODES.filter(n=>!isProc(n.type));
  const row=(n,v)=>`${lnk(n.id)}<span class="v">${v}</span>`;
  const byDown=[...data].sort((a,b)=>b.downAll-a.downAll||b.down-a.down).slice(0,5);
  const byReads=data.filter(n=>n.reads!=null).sort((a,b)=>b.reads-a.reads).slice(0,5);
  const unused=data.filter(n=>n.down===0&&n.reads===0);
  return `<div class="issue"><h3>後続で多く使われている（下流全体）</h3><span class="hint">変更したときの影響が大きいもの</span><div class="rank">${byDown.map(n=>row(n,`直接 ${n.down} / 全 ${n.downAll}`)).join('')}</div></div>`+
   (byReads.length?`<div class="issue"><h3>よく参照されている（30日）</h3><div class="rank">${byReads.map(n=>row(n,n.reads.toLocaleString()+' 回')).join('')}</div></div>`:'')+
   (unused.length?`<div class="issue"><h3>後続なし・参照なし（${unused.length}）</h3><span class="hint">どこからも使われていない。削除候補</span><div class="links">${unused.map(n=>lnk(n.id)).join('')}</div></div>`:'');
}
function overview(){
  const issues=s=>NODES.filter(n=>n.status===s);
  const byKind=k=>[...new Set(ANOMS.filter(a=>a.kind===k).map(a=>a.object))].map(id=>BY[id]).filter(Boolean);
  const nodesc=NODES.filter(n=>!isProc(n.type)&&!n.desc);
  const allCols=NODES.flatMap(n=>n.cols),filled=allCols.filter(c=>c[2]).length,pct=Math.round(filled/allCols.length*100);
  const block=(title,list,note)=>list.length?`<div class="issue"><h3>${title}（${list.length}）</h3>${note?`<span class="hint">${note}</span>`:''}<div class="links">${list.map(n=>lnk(n.id)).join('')}</div></div>`:'';
  return `<div class="dhead"><div class="dname" style="font-family:var(--f-display)">全体の状況</div><div class="hint">ノードをクリックすると、そのテーブルの上流・下流とカラムの由来を表示します。ノード下段の数字は「下流の数（全体）・30日の参照回数」です。</div></div>`+
   (HAS_STATE?'':`<div class="warnbox">Snowflake の状態を取得していないため、実行状況・更新時刻・参照回数・異常は表示されません。<br><span class="mono">make site STATE=…</span> または GitHub Actions の site ワークフローで取得します。</div>`)+
   block('失敗している処理',issues('failed'),'TASK_HISTORY の最終実行が失敗')+
   block('更新が遅れているもの',issues('delayed'),'上流の失敗で最新化されていない')+
   block('手動変更（GitHub と Snowflake の定義が違う）',byKind('drift'),'GitHub を通さずに変更された可能性')+
   block('手動デプロイ',byKind('manual_ddl'),'許可リスト外のユーザーが DDL を実行')+
   block('GitHub に SQL がないもの',byKind('unregistered'),'Snowflake 上でだけ作られている')+
   block('未デプロイ',byKind('undeployed'),'リポジトリにあるが Snowflake に無い')+
   block('コード外の流れがあるもの',byKind('hidden_flow'),'実行履歴にあるがリポジトリの SQL に無い読み書き')+
   block('タスク停止中',byKind('suspended'))+
   block('30日以上更新・参照なし',issues('stale'),'削除やアーカイブの候補')+
   usageBlocks()+
   block('説明が未記入のオブジェクト',nodesc)+
   `<div class="issue"><h3>カラム説明の記入率</h3><div class="meter"><i style="width:${pct}%"></i></div><span class="hint mono">${filled} / ${allCols.length} カラム（${pct}%）</span></div>`;
}
function bind(el){
  el.querySelectorAll('[data-go]').forEach(b=>b.addEventListener('click',()=>select(b.dataset.go,b.dataset.gocol)));
  el.querySelectorAll('tr.col').forEach(tr=>{const go=()=>select(state.sel,state.col===tr.dataset.col?null:tr.dataset.col);
    tr.addEventListener('click',go);tr.addEventListener('keydown',e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();go();}});});
  el.querySelector('[data-clearcol]')?.addEventListener('click',()=>select(state.sel,null));
}

/* ---------- 検索 ---------- */
const q=document.getElementById('q'),matches=document.getElementById('matches');
q.addEventListener('input',()=>{
  state.query=q.value.trim();highlight();
  if(!state.query){matches.hidden=true;return;}
  const ql=state.query.toLowerCase();
  const list=NODES.filter(n=>matchNode(n,ql)).slice(0,8);
  matches.innerHTML=list.map(n=>{const c=n.cols.find(c=>c[0].toLowerCase().includes(ql)&&!n.id.toLowerCase().includes(ql));
    return `<button data-id="${esc(n.id)}" ${c?`data-col="${esc(c[0])}"`:''}>${esc(n.id)}${c?' · '+esc(c[0]):''}</button>`;}).join('')||'<button disabled>該当なし</button>';
  matches.hidden=false;
  matches.querySelectorAll('[data-id]').forEach(b=>b.addEventListener("click",()=>pickMatch(b)));
});
q.addEventListener('keydown',e=>{if(e.key==='Enter'){const b=matches.querySelector('[data-id]');if(b)pickMatch(b);}if(e.key==='Escape'){q.value='';q.dispatchEvent(new Event('input'));}});
function pickMatch(b){q.value='';state.query='';matches.hidden=true;select(b.dataset.id,b.dataset.col);}

/* ---------- テーマ追従 ---------- */
const restyle=()=>{cy.style(graphStyle());if(cy2)cy2.style(colStyle());renderColorLegend();};
function renderColorLegend(){
  const el=document.getElementById('legend-color');
  const items=state.color==='db'?Object.keys(DBS).map(k=>[DBS[k].tok,k]):Object.entries(state.color==='fresh'?FR:RD).map(([k,v])=>['--'+k,v]);
  el.innerHTML=`<span>色＝${state.color==='db'?'DB':state.color==='fresh'?'最終更新':'参照回数/30日'}：</span>`+items.map(([t,l])=>`<i style="background:var(${t})"></i><span>${esc(l)}</span>`).join('');
}
document.getElementById('f-color').addEventListener('change',e=>{state.color=e.target.value;restyle();});
matchMedia('(prefers-color-scheme: dark)').addEventListener('change',restyle);
new MutationObserver(restyle).observe(document.documentElement,{attributes:true,attributeFilter:['data-theme']});
document.fonts&&document.fonts.ready.then(restyle);

/* ---------- 起動 ---------- */
renderStats();renderColorLegend();build();
const initial=decodeURIComponent(location.hash.slice(1));
state.sel=BY[initial]?initial:null;hops.disabled=!state.sel;
refresh(true);renderDetail();
addEventListener('resize',()=>{cy.resize();if(cy2)cy2.resize();});
