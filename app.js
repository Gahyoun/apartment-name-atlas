import { createMapAdapter } from './map-adapter.js';
import { cleanApartmentName } from './name-cleaning.js';

const $ = (selector, parent = document) => parent.querySelector(selector);
const $$ = (selector, parent = document) => [...parent.querySelectorAll(selector)];
const state = {meta:null, analysis:null, items:[], mapped:[], tableTotal:0, offset:0, pageSize:8, selected:['리버','파크','포레'], chartMode:'cumulative', filters:{}, hypothesis:'river', map:null, mapPromise:null, mapConfig:{provider:'osm'}, mapRenderId:0, requestId:0, gisRequestId:0, regionRequestId:0, featureCache:new Map(), taxonomy:new Map()};
const colors = ['#346fb2','#287d73','#a36c35','#775599','#b04452'];
const labels = {place:'지명',brand:'브랜드',company:'기업·그룹',nature:'자연 표현',premium:'기타 수식어',building_type:'주거 유형',number:'차수·숫자',unclassified:'개별 명칭·미분류',unknown:'개별 명칭·미분류'};
const hypotheses = {river:{token:'리버',feature:'water',label:'수면·하천',title:'‘리버’는 정말 물 가까이에 있을까?'},park:{token:'파크',feature:'park',label:'공원',title:'‘파크’는 정말 공원 가까이에 있을까?'},forest:{token:'포레',feature:'forest',label:'숲',title:'‘포레’는 정말 숲 가까이에 있을까?'},metro:{token:'메트로',feature:'metro',label:'철도역',title:'‘메트로’는 정말 역 가까이에 있을까?'},school:{token:'에듀',feature:'school',label:'학교',title:'‘에듀’는 정말 학교 가까이에 있을까?'}};
const fmt = (value) => Number.isFinite(Number(value)) ? new Intl.NumberFormat('ko-KR').format(Number(value)) : '—';
const escape = (value) => String(value ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const share = (value) => value == null || !Number.isFinite(Number(value)) ? '미측정' : `${(Number(value)*100).toFixed(1)}%`;
const distance = (value) => value == null || !Number.isFinite(Number(value)) ? '미측정' : Number(value)>=1000 ? `${(Number(value)/1000).toFixed(2)} km` : `${fmt(Math.round(Number(value)))} m`;
const safeLink = (value) => {try{const url=new URL(value);return ['http:','https:'].includes(url.protocol)?url.href:null;}catch{return null;}};
const link = (url,label) => safeLink(url)?`<a href="${escape(safeLink(url))}" target="_blank" rel="noopener noreferrer">${escape(label)} ↗</a>`:'';
const nonempty = (object) => Object.fromEntries(Object.entries(object).filter(([,v])=>v!==''&&v!=null));
const query = (values={}) => new URLSearchParams(nonempty(values)).toString();
let runtimeApiPromise;
function resolveRuntimeApi() {
  if (runtimeApiPromise) return runtimeApiPromise;
  runtimeApiPromise = (async () => {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 15000);
    try {
      // Resolve beside this module, including GitHub Pages repository paths.
      const response = await fetch(new URL('./runtime-config.json', import.meta.url), { signal: controller.signal, cache: 'no-cache' });
      if (response.status === 404) return null;
      if (!response.ok) throw new Error('실행 설정을 불러오지 못했습니다. 페이지를 새로고침해 주세요.');
      const config = await response.json();
      if (config.mode === 'api' || config.mode === 'local') return null;
      if (config.mode !== 'static') throw new Error('지원하지 않는 실행 설정입니다.');
      state.runtimeMode = 'static';
      const adapter = await import('./static-api.js');
      if (typeof adapter.staticApi !== 'function') throw new Error('정적 자료 연결 모듈을 불러오지 못했습니다.');
      return adapter.staticApi;
    } finally {
      clearTimeout(timer);
    }
  })();
  return runtimeApiPromise;
}
async function api(path, values={}) {
  const staticApi = await resolveRuntimeApi();
  if (staticApi) return staticApi(path, values);
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 60000);
  try {
    const response = await fetch(`${path}${Object.keys(values).length ? '?' + query(values) : ''}`, { signal: controller.signal });
    if (!response.ok) throw new Error(`요청을 처리하지 못했습니다 (${response.status}).`);
    return await response.json();
  } finally {
    clearTimeout(timer);
  }
}
function toast(message){$('#toast').textContent=message;$('#toast').hidden=false;clearTimeout(state.toastTimer);state.toastTimer=setTimeout(()=>{$('#toast').hidden=true;},4500);}
function setError(message){$('#global-error').textContent=message;$('#global-error').hidden=!message;}
function statusNotice(message){$('#data-notice p').textContent=message;}
function baseFilters(){return nonempty({sido:state.filters.sido,sigungu:state.filters.sigungu,dong:state.filters.dong,year_from:state.filters.year_from,year_to:state.filters.year_to});}
function scopeLabel(){const parts=[state.filters.sido,state.filters.sigungu,state.filters.dong].filter(Boolean);return parts.length?parts.join(' '):'전국 아파트';}
function tokenLabel(token){return token.canonical||token.surface||token.token||'';}
function isFrequencyToken(token){const label=typeof token==='string'?token:tokenLabel(token||{});const normalized=label.normalize('NFKC').replace(/\s/gu,'');const numberDesignator=/[0-9]/u.test(normalized)&&/^[0-9제차단지블록동호층번지()\[\]{},·;:/~\-–—.ㆍ]+$/u.test(normalized);return Boolean(label.trim())&&token?.category!=='number'&&!numberDesignator;}
function cleanSelectedTokens(tokens){return [...new Set((Array.isArray(tokens)?tokens:[]).filter(token=>typeof token==='string'&&isFrequencyToken(token)).map(token=>token.trim()))].slice(0,5);}
function syncSelectedTokens(analysis){state.selected=cleanSelectedTokens(Array.isArray(analysis?.selected_tokens)?analysis.selected_tokens:state.selected);}
const numberTokenFeedback='숫자·차수는 표현 순위와 그래프 비교에서 제외합니다.';
function rejectNumberToken(){$('#token-search').setAttribute('aria-invalid','true');$('#token-selection-feedback').textContent=numberTokenFeedback;toast(numberTokenFeedback);}
function tokensOf(item){return Array.isArray(item.tokens)?item.tokens:[];}
function displayName(item){return (item.name_clean??cleanApartmentName(item.name))||'명칭 미확인';}
function visibleNameTokens(item){return tokensOf(item).map(token=>({...token,surface:(token.surface||tokenLabel(token)).replace(/^[)\]}]+|[([{]+$/gu,'').trim()})).filter(token=>isFrequencyToken(token)&&/[\p{L}\p{N}]/u.test(token.surface));}
function chip(token, withCategory=false){const category=token.category||'unclassified';return `<span class="token-chip ${escape(category)}" title="${escape(labels[category]||category)}">${escape(token.surface||tokenLabel(token))}${withCategory?`<small>${escape(labels[category]||category)}</small>`:''}</span>`;}
function validPoint(item){return Number.isFinite(item.lat)&&Number.isFinite(item.lon)&&item.lat>=32&&item.lat<=40&&item.lon>=123&&item.lon<=133;}
function needsReview(item){return Boolean(item.review_required||item.needs_review||tokensOf(item).some(t=>t.provisional||t.category==='unclassified'||t.category==='unknown'||t.review_required));}
function options(select, rows, allLabel){const prior=select.value;select.innerHTML=`<option value="">${escape(allLabel)}</option>`+(rows||[]).map(item=>`<option value="${escape(item.value)}">${escape(item.label)} (${fmt(item.count)})</option>`).join('');if((rows||[]).some(row=>String(row.value)===prior))select.value=prior;}
async function loadRegions(level){const id=++state.regionRequestId;const select=$(`#${level}`);try{const result=await api('/api/regions',{level,sido:$('#sido').value,sigungu:$('#sigungu').value});if(id!==state.regionRequestId)return;options(select,result.options,level==='sido'?'전국':level==='sigungu'?'전체 시·군·구':'전체 동·읍·면');select.disabled=level!=='sido'&&!result.options?.length;}catch{toast('지역 목록을 불러오지 못했습니다. 잠시 후 다시 선택해 주세요.');}}
function renderMeta(){const dataset=state.meta?.dataset||{};const isReal=dataset.status==='real';$('#dataset-badge').textContent=isReal?'공개 실데이터':'수집 대기';$('#dataset-badge').classList.toggle('ready',isReal);const geoLabel=dataset.coordinate_supplement?.source_url?.includes('seoul')?'서울 ':'';statusNotice(dataset.record_count!=null?`전국 ${fmt(dataset.record_count)}개 현재명 · 사용승인연도 기준 · GIS ${geoLabel}${fmt(dataset.coordinate_count)}개 좌표`:'원자료 수집 대기 · 자료와 분석 방법에서 수록 범위를 확인해 주세요.');const yearFrom=$('#year-from'),yearTo=$('#year-to');if(dataset.year_min){yearFrom.placeholder=String(dataset.year_min);yearFrom.min=String(dataset.year_min);yearTo.min=String(dataset.year_min);}if(dataset.year_max){yearTo.placeholder=String(dataset.year_max);yearFrom.max=String(dataset.year_max);yearTo.max=String(dataset.year_max);}$('#source-description').textContent=`${dataset.name||'공개 원자료'}${dataset.downloaded_at?' · 수집일 '+String(dataset.downloaded_at).slice(0,10):''}. ${dataset.coverage_note||'해당 자료에 수록된 단지를 분석하며, 전국 모든 단지의 전수조사를 의미하지 않습니다.'}`;$('#source-links').innerHTML=link(dataset.source_url,'단지 원자료 확인')+link(dataset.coordinate_supplement?.source_url,'서울 좌표 보강 자료')+link('https://www.openstreetmap.org/copyright','분석용 입지 자료 · OpenStreetMap')+link('https://apis.map.kakao.com/web/guide/','카카오 지도 API')+link('https://oh-my-design.kr/design-systems/krds','참조 디자인 시스템');}
function metric(id, value, unit='개'){$(id).innerHTML=`${value==null?'—':fmt(value)}${value==null?'':`<span class="metric-unit">${escape(unit)}</span>`}`;}
function renderAnalysis(){const data=state.analysis;if(!data)return;syncSelectedTokens(data);$('#scope-title').textContent=scopeLabel();metric('#metric-total',data.sample_count);if(data.brand_count!=null){metric('#metric-brand',data.brand_count);$('#metric-brand-note').textContent=`${share(data.sample_count?data.brand_count/data.sample_count:null)} · 초기 사전 인식 기준`;}else{const card=$('#metric-brand').closest('article');$('span',card).textContent='사용승인연도 확인';metric('#metric-brand',data.valid_year_count);$('#metric-brand-note').textContent='연도별 그래프에 포함된 단지';}if(data.unique_token_count!=null){metric('#metric-tokens',data.unique_token_count);}else{const card=$('#metric-tokens').closest('article');$('span',card).textContent='사용승인연도 확인';metric('#metric-tokens',data.valid_year_count);$('small',card).textContent='연도 미상 단지는 그래프 제외';}metric('#metric-geocoded',data.mapped_count);$('#metric-geo-note').textContent=`선택 표본의 ${share(data.sample_count?data.mapped_count/data.sample_count:null)} · 실제 좌표`;renderRanking();renderChart();$('#place-match-note').textContent=data.place_name_count!=null?`지명 후보 포함 ${fmt(data.place_name_count)}개 · 주소에서 같은 표기를 찾은 단지 ${fmt(data.place_local_match_count)}개`:'';renderWarnings();}
function renderWarnings(){const dataset=state.meta?.dataset||{};const messages=[dataset.coverage_note,dataset.coordinate_coverage_note,...(state.analysis?.warnings||[])].filter(Boolean);if(dataset.audit?.name_disagreement_count!=null)messages.push(`자료원 사이에 명칭 표기가 다른 단지는 ${fmt(dataset.audit.name_disagreement_count)}개입니다. 대표명은 공시가격 → 건축물대장 → 도로명주소 순으로 선택하며, 자료원별 원문은 개별 단지 상세에 보존합니다. 이 차이는 개명 이력이 아닙니다.`);$('#data-warnings').innerHTML=[...new Set(messages)].map(message=>`<p>${escape(message)}</p>`).join('');}

function renderRanking(){const items=(state.rankingTokens||state.analysis?.top_tokens||[]).filter(isFrequencyToken).filter(t=>!state.filters.category||t.category===state.filters.category).slice(0,10);const maximum=Math.max(1,...items.map(t=>Number(t.count)||0));$('#token-ranking').innerHTML=items.length?items.map((item,index)=>`<button class="ranking-item" type="button" data-token="${escape(item.token)}" aria-pressed="${state.selected.includes(item.token)}" style="--bar-width:${Math.max(2,100*item.count/maximum)}%"><span class="ranking-num">${String(index+1).padStart(2,'0')}</span><span class="ranking-token">${escape(item.token)}</span><span class="ranking-value">${fmt(item.count)}</span></button>`).join(''):'<p class="field-help">이 분류에 해당하는 표현이 없습니다. 다른 분류를 선택해 주세요.</p>';$('#token-selection-feedback').textContent=$('#token-search').getAttribute('aria-invalid')==='true'?numberTokenFeedback:state.selected.length?`${state.selected.length}개 표현 비교 중`:'비교할 표현을 선택해 주세요.';}
function renderChart(){const series=state.analysis?.series||[];state.selected=cleanSelectedTokens(state.selected);const selected=state.selected;$('#chart-legend').innerHTML=selected.map((token,i)=>`<button type="button" class="legend-token" data-remove-token="${escape(token)}" aria-label="${escape(token)} 비교에서 제외"><i class="legend-line" style="--line-color:${colors[i]}" aria-hidden="true"></i>${escape(token)}<span aria-hidden="true">×</span></button>`).join('');$$('[data-chart-mode]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.chartMode===state.chartMode)));$('#chart-note').textContent=state.chartMode==='cumulative'?'누적값은 선택 구간의 첫해부터 합산합니다. 사용승인 시기별 현재 이름이며, 명칭 변경 이력을 나타내지 않습니다.':'해당 연도 전체 유효 단지가 분모입니다. 같은 이름에 여러 표현이 함께 쓰이므로 비율의 합은 100%를 넘을 수 있습니다.';if(!series.length||!selected.length){$('#trend-chart').innerHTML=`<div class="empty-state compact"><strong>${!series.length?'표시할 연도별 표본이 없습니다.':'비교할 표현을 선택해 주세요.'}</strong><p>${!series.length?'지역·연도 조건을 넓히거나 사용승인연도가 있는 자료를 추가해 주세요.':'오른쪽 표현 목록에서 최대 5개를 선택할 수 있습니다.'}</p></div>`;$('#chart-table').innerHTML='';return;}
const width=640,height=278,padding={left:49,right:18,top:18,bottom:37},innerW=width-padding.left-padding.right,innerH=height-padding.top-padding.bottom;const years=series.map(s=>Number(s.year));const minYear=Math.min(...years),maxYear=Math.max(...years);const key=state.chartMode==='share'?'token_shares':'token_cumulative';const allValues=series.flatMap(s=>selected.map(t=>Number(s[key]?.[t])||0));const rawMax=Math.max(...allValues,state.chartMode==='share'?1/100:4);const rough=rawMax/4;const magnitude=10**Math.floor(Math.log10(rough));const niceStep=Math.ceil(rough/magnitude)*magnitude;const yMax=niceStep*4;const x=(year)=>padding.left+(maxYear===minYear?.5:(year-minYear)/(maxYear-minYear))*innerW;const y=(v)=>padding.top+innerH-(v/yMax)*innerH;let svg=`<svg viewBox="0 0 ${width} ${height}" role="img" aria-labelledby="svg-title svg-description"><title id="svg-title">${escape(scopeLabel())} 표현별 ${state.chartMode==='share'?'연도별 포함 비율':'누적 단지 수'}</title><desc id="svg-description">${escape(selected.join(', '))}. 사용승인연도 ${minYear}년부터 ${maxYear}년까지. 아래 그래프 수치 표에서 정확한 값을 확인할 수 있습니다.</desc>`;for(let i=0;i<=4;i++){const v=i*niceStep;svg+=`<line x1="${padding.left}" y1="${y(v)}" x2="${width-padding.right}" y2="${y(v)}" stroke="#e6e8ea" stroke-width="1"/><text x="${padding.left-10}" y="${y(v)+4}" text-anchor="end">${state.chartMode==='share'?`${yMax<.1?(v*100).toFixed(1):Math.round(v*100)}%`:fmt(Math.round(v))}</text>`;}const tickYears=maxYear-minYear<=5?years:[minYear,...Array.from({length:Math.ceil((maxYear-minYear)/10)},(_,i)=>Math.ceil(minYear/10)*10+i*10).filter(v=>v>minYear&&v<maxYear-2),maxYear];[...new Set(tickYears)].forEach(year=>{svg+=`<text x="${x(year)}" y="${height-12}" text-anchor="middle">${year}</text>`;});selected.forEach((token,index)=>{const pointGroups=[[]];series.forEach(s=>{if(state.chartMode==='share'&&s[key]?.[token]==null){if(pointGroups.at(-1).length)pointGroups.push([]);return;}pointGroups.at(-1).push(`${x(Number(s.year)).toFixed(1)},${y(Number(s[key]?.[token])||0).toFixed(1)}`);});pointGroups.filter(points=>points.length).forEach(points=>{if(state.chartMode==='cumulative')svg+=`<path d="M${x(minYear)},${y(0)} L${points.join(' L')} L${x(maxYear)},${y(0)} Z" fill="${colors[index]}" opacity="0.035"/>`;svg+=`<polyline points="${points.join(' ')}" fill="none" stroke="${colors[index]}" stroke-width="2.5" stroke-linejoin="round" stroke-linecap="round"/>`;if(points.length===1){const [px,py]=points[0].split(',');svg+=`<circle cx="${px}" cy="${py}" r="3" fill="${colors[index]}"/>`;}});});svg+='</svg>';$('#trend-chart').innerHTML=svg;$('#chart-table').innerHTML=`<table><caption class="sr-only">그래프 수치 ${state.chartMode==='share'?'연도별 비율':'누적 단지 수'}</caption><thead><tr><th scope="col">승인연도</th><th scope="col">해당 연도 표본</th>${selected.map(t=>`<th scope="col">${escape(t)}</th>`).join('')}</tr></thead><tbody>${series.map(s=>`<tr><th scope="row">${s.year}</th><td>${fmt(s.total_count)}</td>${selected.map(t=>`<td>${state.chartMode==='share'?share(s[key]?.[t]):fmt(s[key]?.[t]??0)}</td>`).join('')}</tr>`).join('')}</tbody></table>`;}
async function refreshTokenSeries(){state.selected=cleanSelectedTokens(state.selected);try{const requestId=++state.tokenRequestId;const result=await api('/api/analysis',{...baseFilters(),tokens:state.selected.join(','),category:state.filters.category});if(requestId!==state.tokenRequestId)return;state.analysis=result;renderAnalysis();state.map?.updateSelection(state.selected);}catch{toast('그래프를 갱신하지 못했습니다. 조건 적용하기로 다시 시도해 주세요.');}}
function renderTable(){const {items,tableTotal,offset,pageSize}=state;$('#apartments-body').innerHTML=items.length?items.map(item=>`<tr><td><button class="name-button" data-complex="${escape(item.id)}" type="button">${escape(displayName(item))}</button></td><td class="region-cell">${escape(item.sido||'')} ${escape(item.sigungu||'')}<br>${escape(item.dong||'동 정보 없음')}</td><td>${item.approval_year?escape(item.approval_year)+'년':'미확인'}</td><td><div class="token-chips">${visibleNameTokens(item).slice(0,8).map(t=>chip(t)).join('')||'<span class="muted">분절 결과 없음</span>'}</div></td><td><span class="review-tag ${needsReview(item)?'':'clear'}">${needsReview(item)?'검토 필요':'사전 매칭'}</span></td></tr>`).join(''):'<tr><td colspan="5" class="table-empty">조건에 맞는 단지가 없습니다. 검색어나 지역·연도 범위를 넓혀 주세요.</td></tr>';$('#pagination-info').textContent=items.length?`${fmt(offset+1)}–${fmt(Math.min(offset+items.length,tableTotal))} / ${fmt(tableTotal)}개`:'검색 결과 0개';$('#prev-page').disabled=offset===0;$('#next-page').disabled=offset+pageSize>=tableTotal;$('#download-csv').disabled=!tableTotal;$('#table-description').textContent=state.filters.q?`원문 이름에 ‘${state.filters.q}’가 포함된 ${fmt(tableTotal)}개 단지 · 검색어는 이 목록과 지도에 적용됩니다.`:`${fmt(tableTotal)}개 단지 · 숫자 부가정보를 제거한 이름과 분절 · 이름을 눌러 원문 확인`;}
async function loadTable(){const request=++state.tableRequestId;try{const data=await api('/api/complexes',{...baseFilters(),q:state.filters.q,limit:state.pageSize,offset:state.offset});if(request!==state.tableRequestId)return;state.items=data.items||[];state.tableTotal=data.total||0;renderTable();}catch{toast('단지 목록을 불러오지 못했습니다. 조건을 다시 적용해 주세요.');}}
function showDetail(item,pan=false){$('#detail-label').textContent='표시 이름 · 분절 결과';$('#name-detail-title').textContent=displayName(item);const groups=[...new Set(tokensOf(item).map(t=>t.corporate_group).filter(Boolean))];$('#detail-content').innerHTML=`<div class="token-chips">${visibleNameTokens(item).map(t=>chip(t,true)).join('')||'<p class="muted">사전으로 분류된 부분이 없습니다.</p>'}</div><p class="field-help">동번호·차수·숫자 부가정보를 제거한 표기입니다.</p><dl class="detail-meta"><dt>주소 지역</dt><dd>${escape([item.sido,item.sigungu,item.dong].filter(Boolean).join(' '))||'미확인'}</dd><dt>사용승인</dt><dd>${item.approval_year?escape(item.approval_year)+'년':'미확인'}</dd><dt>법정동 코드</dt><dd>${escape(item.dong_code||'미확인')}</dd><dt>기업 관계</dt><dd>${escape(groups.join(', ')||'연결된 정보 없음')}</dd><dt>좌표</dt><dd>${validPoint(item)?`${item.lat.toFixed(5)}, ${item.lon.toFixed(5)}`:'검증된 좌표 없음'}</dd></dl>${needsReview(item)?'<p class="detail-review">잠정 분류 또는 미분류 표현이 포함되어 있습니다. 원문과 주소를 함께 검토해 주세요.</p>':''}${renderNameVariants(item)}${safeLink(item.source_url)?`<div class="detail-source">${link(item.source_url,'단지 원자료 확인')}</div>`:''}`;if(pan&&state.map&&validPoint(item)){state.map.showItem(item);}}

function renderNameVariants(item){const variants=Object.entries(item.name_variants||{}).filter(([,value])=>value);return `<details class="source-variants"><summary>원문 확인${item.name_disagreement?' · 자료원별 표기 차이 있음':''}</summary><dl><dt>분석에 사용한 원문</dt><dd>${escape(item.name)}</dd></dl><div class="token-chips">${tokensOf(item).map(t=>chip(t,true)).join('')}</div>${variants.length?`<dl>${variants.map(([source,value])=>`<dt>${escape(source.replace('단지명_',''))}${source===item.name_basis?' · 분석 대표명':''}</dt><dd>${escape(value)}</dd>`).join('')}</dl>`:''}<p class="field-help">숫자·차수와 원문 분절을 이곳에 보존합니다. 자료원별 현재 명칭 차이는 개명 이력이 아닙니다.</p></details>`;}

function setMapStatus(status, message) {
  const element = $('#map-provider-status');
  element.textContent = message;
  element.dataset.status = status;
}
async function initializeMap() {
  if (state.map) return state.map;
  if (state.mapPromise) return state.mapPromise;
  state.mapPromise = createMapAdapter($('#map'), state.mapConfig, (item) => showDetail(item), setMapStatus)
    .then((adapter) => {
      state.map = adapter;
      if (window.ResizeObserver) {
        state.mapResizeObserver = new ResizeObserver(() => adapter.resize());
        state.mapResizeObserver.observe($('#map'));
      }
      return adapter;
    })
    .catch(() => {
      state.mapPromise = null;
      setMapStatus('error', '지도를 불러오지 못했습니다. 네트워크 연결을 확인해 주세요. 단지 목록과 통계는 계속 이용할 수 있습니다.');
      return null;
    });
  return state.mapPromise;
}
async function renderMap() {
  const renderId = ++state.mapRenderId;
  const items = state.mapped.filter(validPoint);
  $('#fit-map').disabled = !items.length;
  if (!items.length && !state.map && !state.mapPromise) {
    $('#map').innerHTML = '<div class="map-placeholder"><svg viewBox="0 0 48 48" width="42" height="42" fill="none" aria-hidden="true"><path d="m5 12 12-5 14 5 12-5v29l-12 5-14-5-12 5V12Z" stroke="currentColor" stroke-width="2"/><path d="M17 7v29M31 12v29" stroke="currentColor" stroke-width="2"/></svg><strong>표시할 검증된 좌표가 없습니다</strong><p>지역·검색 조건을 넓히거나 좌표 자료를 연결해 주세요.</p></div>';
    $('#map-note').textContent = '좌표가 없는 단지를 임의 위치로 배치하지 않습니다. 목록에서는 모든 단지를 확인할 수 있습니다.';
    setMapStatus('empty', `${state.mapConfig.provider === 'kakao' ? '카카오맵' : 'OpenStreetMap'} · 표시할 좌표가 확보되면 지도를 연결합니다.`);
    return;
  }
  const adapter = await initializeMap();
  if (renderId !== state.mapRenderId) return;
  if (!adapter) {
    $('#map').innerHTML = '<div class="map-placeholder"><strong>지도를 불러오지 못했습니다</strong><p>네트워크 연결을 확인해 주세요. 단지 목록과 분석 결과는 계속 이용할 수 있습니다.</p></div>';
    return;
  }
  adapter.setPoints(items, state.selected);
  if (items.length) adapter.fitPoints();
  const count = state.analysis?.mapped_count;
  $('#map-note').textContent = `표시 ${fmt(items.length)}개${count != null ? ` · 선택 지역의 좌표 확인 ${fmt(count)} / ${fmt(state.analysis?.sample_count)}개` : ''}${state.filters.q ? ` · 이름 검색 ‘${state.filters.q}’ 적용` : ''}. 분석용 입지 도형: OpenStreetMap. 시설점의 원형은 위치 기호이며 실제 시설 범위를 나타내지 않습니다. 좌표 확보 지역에 한정된 분포입니다.`;
}
function fitMap() { state.map?.fitPoints(); }
async function loadFeatures(feature) {
  if (!state.map) return;
  const request = state.gisRequestId;
  try {
    let data = state.featureCache.get(feature);
    if (!data) {
      data = await api('/api/gis-features', { feature });
      if (data.type === 'FeatureCollection') state.featureCache.set(feature, data);
    }
    if (request !== state.gisRequestId || data.type !== 'FeatureCollection') return;
    state.map.setFeatures(data, feature);
  } catch {
    // Geometry is optional; measured statistics and provenance remain visible.
  }
}
function inferenceSummary(summary) {
  const parts = [];
  if (summary.adjusted_mean_difference_m != null) parts.push(`시군구·사용승인연대 보정 평균 거리 차이 ${Number(summary.adjusted_mean_difference_m) > 0 ? '+' : ''}${fmt(summary.adjusted_mean_difference_m)} m (대상 ${fmt(summary.matched_target_n)} / 비교 ${fmt(summary.matched_control_n)}개).`);
  if (summary.permutation_p_value != null) parts.push(`순열검정 p = ${Number(summary.permutation_p_value).toFixed(4)}.`);
  else if (state.runtimeMode === 'static') parts.push('순열검정 p값은 로컬 분석에서 제공합니다. 정적 화면에서는 현재 선택 표본의 거리 통계를 계산합니다.');
  return parts.join(' ');
}
function renderGIS(data){const config=hypotheses[state.hypothesis];$('#geography-title').textContent=config.title;$$('[data-hypothesis]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.hypothesis===state.hypothesis)));const s=data.summary||{},coverage=data.coverage||{};if(data.status!=='ready'||s.target_n==null||s.control_n==null){$('#hypothesis-content').innerHTML=`<div class="empty-state"><strong>${data.status==='insufficient'?'비교에 필요한 표본이 부족합니다.':'실제 거리 비교를 준비하고 있습니다.'}</strong><p>${escape(data.message||`${config.label} 자료와 단지 좌표가 모두 확보되면 비교할 수 있습니다.`)}</p>${coverage.measured_count!=null?`<p>측정 완료 ${fmt(coverage.measured_count)} / ${fmt(coverage.population_count)}개 단지</p>`:''}<a class="outline-button small" href="#methodology">자료 범위와 분석 방법 확인</a></div>${(data.warnings||[]).map(w=>`<p class="comparison-note">${escape(w)}</p>`).join('')}`;return;}const maxDistance=Math.max(Number(s.target_median_m)||0,Number(s.control_median_m)||0,1);const threshold=data.threshold_m||500;function group(type,title,n,median,near){return `<div class="comparison-group ${type}"><div class="comparison-label">${escape(title)}<small>측정 표본 ${fmt(n)}개</small></div><strong>${distance(median)}</strong><span class="small-label">가장 가까운 ${escape(config.label)}까지 거리 중앙값</span><div class="comparison-bar" aria-hidden="true"><span style="--bar-width:${100*(Number(median)||0)/maxDistance}%"></span></div><p class="comparison-note">${fmt(threshold)} m 이내 ${share(near)}</p></div>`;}const difference=s.median_difference_m;const comparison=difference==null?'거리 차이는 미측정입니다.':Math.abs(difference)<1?'두 집단의 중앙값 차이는 1 m 미만입니다.':`‘${config.token}’ 포함 단지는 비교군보다 거리 중앙값이 ${distance(Math.abs(difference))} ${difference<0?'짧습니다':'깁니다'}.`;$('#hypothesis-content').innerHTML=`<div class="hypothesis-comparison">${group('target',`‘${config.token}’ 포함`,s.target_n,s.target_median_m,s.target_near_share)}${group('control',`‘${config.token}’ 미포함`,s.control_n,s.control_median_m,s.control_near_share)}</div><p class="result-summary">${escape(comparison)}</p><p class="comparison-note">${escape(data.message||'현재 확보된 단지 좌표와 지리 자료를 기준으로 계산했습니다.')} ${coverage.measured_count!=null?`측정 ${fmt(coverage.measured_count)} / 지역 표본 ${fmt(coverage.population_count)}개.`:''} ${escape(inferenceSummary(s))}</p>${(data.warnings||[]).map(w=>`<p class="comparison-note">${escape(w)}</p>`).join('')}`;}
async function loadGIS(){const request=++state.gisRequestId;const config=hypotheses[state.hypothesis];$('#geography-title').textContent=config.title;$$('[data-hypothesis]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.hypothesis===state.hypothesis)));$('#hypothesis-content').innerHTML='<div class="empty-state"><strong>실제 거리와 비교군을 확인하고 있습니다.</strong><p>현재 선택한 지역과 사용승인연도 범위를 적용합니다.</p></div>';try{const data=await api('/api/gis',{...baseFilters(),token:config.token,feature:config.feature,threshold_m:500});if(request!==state.gisRequestId)return;renderGIS(data);await loadFeatures(config.feature);}catch{if(request===state.gisRequestId)renderGIS({status:'not_ready',message:'거리 분석 응답을 불러오지 못했습니다. 다른 가설을 선택하거나 조건을 다시 적용해 주세요.'});}}
async function applyFilters(){state.selected=cleanSelectedTokens(state.selected);const id=++state.requestId;state.tokenRequestId=(state.tokenRequestId||0)+1;state.tableRequestId=(state.tableRequestId||0)+1;$('#results').setAttribute('aria-busy','true');setError('');state.offset=0;state.rankingTokens=null;$('#token-search').value='';$('#token-search').removeAttribute('aria-invalid');$('#token-suggestions').innerHTML='';$('#scope-title').textContent=scopeLabel();const jobs=await Promise.allSettled([api('/api/analysis',{...baseFilters(),tokens:state.selected.join(','),category:state.filters.category}),api('/api/complexes',{...baseFilters(),q:state.filters.q,limit:state.pageSize,offset:0}),api('/api/complexes',{...baseFilters(),q:state.filters.q,mapped_only:1,limit:5000,offset:0})]);if(id!==state.requestId)return;const [analysis,table,mapped]=jobs;if(analysis.status==='fulfilled'){state.analysis=analysis.value;renderAnalysis();}else{state.analysis=null;['#metric-total','#metric-brand','#metric-tokens','#metric-geocoded'].forEach(id=>metric(id,null));$('#place-match-note').textContent='';renderChart();renderRanking();setError('통계를 불러오지 못했습니다. 서버 연결을 확인한 뒤 조건 적용하기로 다시 시도해 주세요.');}if(table.status==='fulfilled'){state.items=table.value.items||[];state.tableTotal=table.value.total||0;renderTable();}else{$('#apartments-body').innerHTML='<tr><td colspan="5" class="table-empty">단지 목록을 불러오지 못했습니다. 조건 적용하기로 다시 시도해 주세요.</td></tr>';}if(mapped.status==='fulfilled'){state.mapped=mapped.value.items||[];await renderMap();}else{state.mapped=[];await renderMap();}if(id!==state.requestId)return;$('#results').setAttribute('aria-busy','false');loadGIS();}
function readFilters(){return Object.fromEntries(new FormData($('#filter-form')).entries());}
async function downloadCSV(){const button=$('#download-csv');button.disabled=true;button.textContent='파일 준비 중…';try{const rows=[];let offset=0,total=Infinity;while(offset<total){const result=await api('/api/complexes',{...baseFilters(),q:state.filters.q,limit:5000,offset});if(!result.items?.length)break;rows.push(...result.items);total=result.total;offset+=result.items.length;}if(!rows.length){toast('내려받을 단지가 없습니다.');return;}const csvValue=v=>{let s=String(v??'');if(/^[=+\-@\t\r]/.test(s))s="'"+s;return '"'+s.replace(/"/g,'""')+'"';};const headers=['단지 ID','원문 이름','시도','시군구','법정동','법정동 코드','사용승인연도','위도','경도','토큰','분류','출처'];const lines=[headers,...rows.map(r=>[r.id,r.name,r.sido,r.sigungu,r.dong,r.dong_code,r.approval_year,r.lat,r.lon,tokensOf(r).map(tokenLabel).join(' | '),tokensOf(r).map(t=>labels[t.category]||t.category).join(' | '),r.source_url])].map(r=>r.map(csvValue).join(','));const url=URL.createObjectURL(new Blob(['\ufeff'+lines.join('\r\n')],{type:'text/csv;charset=utf-8'}));const a=document.createElement('a');a.href=url;a.download=`아파트이름_${scopeLabel().replace(/\s+/g,'_')}_${new Date().toISOString().slice(0,10)}.csv`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);toast(`${fmt(rows.length)}개 단지의 CSV를 저장했습니다.`);}catch{toast('CSV 파일을 만들지 못했습니다. 서버 연결을 확인하고 다시 시도해 주세요.');}finally{button.disabled=!state.tableTotal;button.innerHTML='CSV 내려받기 <span aria-hidden="true">↓</span>';}}
$('#filter-form').addEventListener('submit',event=>{event.preventDefault();const filters=readFilters();const invalid=filters.year_from&&filters.year_to&&Number(filters.year_from)>Number(filters.year_to);$('#filter-error').hidden=!invalid;$('#year-from').setAttribute('aria-invalid',String(Boolean(invalid)));if(invalid){$('#filter-error').textContent='시작 연도는 종료 연도보다 늦을 수 없습니다.';$('#year-from').focus();return;}state.filters=filters;applyFilters();});
$('#reset-filters').addEventListener('click',()=>{$('#filter-form').reset();$('#sigungu').innerHTML='<option value="">전체 시·군·구</option>';$('#dong').innerHTML='<option value="">전체 동·읍·면</option>';$('#sigungu').disabled=true;$('#dong').disabled=true;$('#filter-error').hidden=true;$('#year-from').removeAttribute('aria-invalid');state.filters={};applyFilters();});
$('#sido').addEventListener('change',()=>{$('#sigungu').innerHTML='<option value="">전체 시·군·구</option>';$('#dong').innerHTML='<option value="">전체 동·읍·면</option>';$('#sigungu').disabled=!$('#sido').value;$('#dong').disabled=true;if($('#sido').value)loadRegions('sigungu');});
$('#sigungu').addEventListener('change',()=>{$('#dong').innerHTML='<option value="">전체 동·읍·면</option>';$('#dong').disabled=!$('#sigungu').value;if($('#sigungu').value)loadRegions('dong');});
$('#token-ranking').addEventListener('click',event=>{const button=event.target.closest('[data-token]');if(!button)return;const token=button.dataset.token;const item=(state.rankingTokens||state.analysis?.top_tokens||[]).find(item=>item.token===token);if(!isFrequencyToken(item||token)){rejectNumberToken();return;}state.selected=cleanSelectedTokens(state.selected);if(state.selected.includes(token))state.selected=state.selected.filter(t=>t!==token);else if(state.selected.length<5)state.selected.push(token);else{toast('표현은 최대 5개까지 비교할 수 있습니다. 선택한 표현을 먼저 해제해 주세요.');return;}renderRanking();refreshTokenSeries();});
$('#chart-legend').addEventListener('click',event=>{const button=event.target.closest('[data-remove-token]');if(!button)return;state.selected=state.selected.filter(t=>t!==button.dataset.removeToken);renderRanking();refreshTokenSeries();});
$('#token-search').addEventListener('input',()=>{$('#token-search').removeAttribute('aria-invalid');clearTimeout(state.searchTimer);state.searchTimer=setTimeout(async()=>{const search=$('#token-search').value.trim();try{const result=await api('/api/tokens',{...baseFilters(),category:state.filters.category,token_q:search});if(search!==$('#token-search').value.trim())return;state.rankingTokens=(result.items||[]).filter(isFrequencyToken);$('#token-suggestions').innerHTML=state.rankingTokens.slice(0,30).map(item=>`<option value="${escape(item.token)}">${fmt(item.count)}개 단지</option>`).join('');renderRanking();}catch{toast('표현 검색을 불러오지 못했습니다. 직접 입력하여 비교에 추가할 수 있습니다.');}},200);});
$('#token-form').addEventListener('submit',event=>{event.preventDefault();const token=$('#token-search').value.trim();if(!token)return;const item=(state.rankingTokens||state.analysis?.top_tokens||[]).find(item=>item.token===token);if(!isFrequencyToken(item||token)){rejectNumberToken();return;}state.selected=cleanSelectedTokens(state.selected);if(state.selected.includes(token)){toast('이미 비교 중인 표현입니다.');return;}if(state.selected.length>=5){toast('표현은 최대 5개까지 비교할 수 있습니다. 그래프 범례의 ×로 먼저 해제해 주세요.');return;}state.selected.push(token);refreshTokenSeries();toast(`‘${token}’을 비교에 추가했습니다.`);});
$$('[data-chart-mode]').forEach(button=>button.addEventListener('click',()=>{state.chartMode=button.dataset.chartMode;renderChart();}));
$('#apartments-body').addEventListener('click',event=>{const button=event.target.closest('[data-complex]');if(!button)return;const item=state.items.find(i=>i.id===button.dataset.complex);if(item){showDetail(item,true);$('#name-detail-title').setAttribute('tabindex','-1');$('#name-detail-title').focus({preventScroll:true});$('#map-title').scrollIntoView({behavior:'auto',block:'start'});}});
$('#fit-map').addEventListener('click',fitMap);$('#prev-page').addEventListener('click',()=>{state.offset=Math.max(0,state.offset-state.pageSize);loadTable();});$('#next-page').addEventListener('click',()=>{state.offset+=state.pageSize;loadTable();});$('#download-csv').addEventListener('click',downloadCSV);
$$('[data-hypothesis]').forEach(button=>button.addEventListener('click',()=>{state.hypothesis=button.dataset.hypothesis;loadGIS();}));
$('#display-settings').addEventListener('click',()=>{const open=$('#display-panel').hidden;$('#display-panel').hidden=!open;$('#display-settings').setAttribute('aria-expanded',String(open));});
$('#font-scale').addEventListener('change',event=>{document.documentElement.style.setProperty('--font-scale',event.target.value);setTimeout(()=>state.map?.resize(),20);});$('#high-contrast').addEventListener('change',event=>document.documentElement.classList.toggle('high-contrast',event.target.checked));
async function init(){state.tokenRequestId=0;state.tableRequestId=0;const results=await Promise.allSettled([api('/api/meta'),loadRegions('sido'),api('/api/map-config')]);if(results[0].status==='fulfilled'){state.meta=results[0].value;renderMeta();}else{$('#dataset-badge').textContent='자료 상태 확인 불가';statusNotice('자료 출처 정보를 불러오지 못했습니다. 서버 연결을 확인한 뒤 새로고침해 주세요.');}state.mapConfig=results[2].status==='fulfilled'?results[2].value:{provider:'osm',config_error:true};await applyFilters();}
init();
