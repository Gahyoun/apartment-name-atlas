import { cleanApartmentName } from './name-cleaning.js';

const $ = (selector, parent = document) => parent.querySelector(selector);
const $$ = (selector, parent = document) => [...parent.querySelectorAll(selector)];
const state = { data: null, rows: [], visible: [], selected: null, heatmapMode: 'share', decadeRanks: new Map(), loading: false };
const categoryLabels = { company: '기업·그룹', corporate_group: '기업·그룹', brand: '브랜드', place: '지명', region: '지명', building_type: '주거 형식', form: '주거 형식', number: '차수·동 번호', punctuation: '구두점' };
const shades = ['#f4f5f6', '#eef2f7', '#d4e1ed', '#abc5dd', '#759dc3', '#3f729f', '#1c4d7b'];
const escape = (value) => String(value ?? '').replace(/[&<>"']/g, (character) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[character]));
const number = (value) => typeof value === 'number' && Number.isFinite(value) ? value : null;
const count = (value) => number(value) == null ? '—' : new Intl.NumberFormat('ko-KR').format(value);
const percent = (value) => number(value) == null ? '미측정' : `${(value * 100).toFixed(2)}%`;
const normalize = (value) => String(value ?? '').normalize('NFKC').trim().toLocaleLowerCase('ko-KR');
const decadeLabel = (value) => `${value}년대`;
const safeLink = (value) => { try { const url = new URL(value); return ['https:', 'http:'].includes(url.protocol) ? url.href : null; } catch { return null; } };
const shareOf = (item, total) => number(item.share) != null ? item.share : number(item.count) != null && total > 0 ? item.count / total : null;

function validateRows(rows, denominator) {
  if (!Array.isArray(rows)) throw new Error('토큰 순위 자료의 형식을 확인해 주세요.');
  const seen = new Set();
  return rows.map((row) => {
    if (!row || typeof row.token !== 'string' || !row.token.trim() || !Number.isInteger(row.count) || row.count < 0 || seen.has(row.token)) throw new Error('토큰 이름·단지 수 또는 중복 항목을 확인해 주세요.');
    if (row.count > denominator) throw new Error('토큰의 포함 단지 수가 전체 표본보다 큽니다.');
    seen.add(row.token);
    const share = shareOf(row, denominator);
    if (share != null && (share < 0 || share > 1)) throw new Error('토큰 비율은 0과 1 사이여야 합니다.');
    return { ...row, share, examples: Array.isArray(row.examples) ? row.examples : [], by_decade: Array.isArray(row.by_decade) ? row.by_decade : [] };
  });
}

function setMetric(selector, value) {
  $(selector).innerHTML = `${count(value)}${number(value) != null ? '<span class="metric-unit">개</span>' : ''}`;
}

function setLoadState(message, error = false) {
  $('#load-status p').textContent = message;
  $('#load-status').hidden = error;
  $('#load-error').hidden = !error;
  if (error) $('#load-error p').textContent = message;
  $('#analysis-body').setAttribute('aria-busy', String(state.loading));
}

function rowFor(token) {
  return state.rows.find((row) => row.token === token) || state.data?.meme_tokens.find((row) => row.token === token) || null;
}

function renderSummary() {
  const { meta, decades } = state.data;
  setMetric('#total-complexes', meta.record_count);
  setMetric('#total-tokens', state.rows.length);
  setMetric('#dated-complexes', number(meta.valid_year_count) ?? decades.reduce((sum, item) => sum + item.total, 0));
  $('#dated-complexes-note').textContent = `${decades.length}개 사용승인연대 · 연도 미상 제외`;
  const leading = state.rows[0];
  $('#leading-token').textContent = leading?.token || '—';
  $('#leading-token-note').textContent = leading ? `${count(leading.count)}개 단지 · 전체의 ${percent(leading.share)}` : '수록된 표현이 없습니다.';
  $('#ranking-denominator').textContent = `전국 ${count(meta.record_count)}개 단지가 분모 · 같은 단지의 반복 표현은 한 번만 집계`;
  $('#denominator-description').textContent = `전체 비율의 분모는 분석 대상 ${count(meta.record_count)}개 단지입니다. 브랜드와 지명을 제거한 뒤 아무 표현도 남지 않는 단지도 분모에 포함합니다. 같은 단지의 반복 표현은 한 번만 세므로 문자열의 출현 횟수와는 다릅니다.`;
  const categories = Array.isArray(meta.excluded_categories) ? meta.excluded_categories : [];
  if (categories.length) $('#exclusion-labels').innerHTML = [...new Set(categories.map((category) => categoryLabels[category] || category))].map((label) => `<span>${escape(label)}</span>`).join('');
  $('#method-description').textContent = typeof meta.method === 'string' ? meta.method : Array.isArray(meta.method) ? meta.method.join(' ') : '기업·브랜드·지명 등을 제외한 뒤 남은 표현을 단지별로 중복 없이 집계했습니다. 상세 규칙은 분석 자료의 출처를 확인해 주세요.';
  const metadata = [['분석 단위', '고유 단지'], ['분류 사전', meta.dictionary_version || '자료에 미기재'], ...(meta.accepted_terms_version ? [['수식어 검수 사전', meta.accepted_terms_version]] : []), ...(meta.entity_dictionary_version ? [['기업·브랜드 사전', meta.entity_dictionary_version]] : []), ['이름 관측일', meta.observed_at || '자료에 미기재']];
  $('#analysis-metadata').innerHTML = metadata.map(([label, value]) => `<dt>${escape(label)}</dt><dd>${escape(value)}</dd>`).join('');
  const warnings = [meta.coverage, meta.coverage_note, ...(Array.isArray(meta.warnings) ? meta.warnings : [])].filter((warning) => typeof warning === 'string' && warning.trim());
  $('#analysis-warnings').innerHTML = warnings.length ? [...new Set(warnings)].map((warning) => `<p>${escape(warning)}</p>`).join('') : '<p>결과 파일에 추가 주의사항이 없습니다. 사전 분류와 현재 이름·준공 시점의 차이를 고려해 해석해 주세요.</p>';
  const sourceURL = safeLink(meta.source_url);
  $('#source-links').innerHTML = `${sourceURL ? `<a href="${escape(sourceURL)}" target="_blank" rel="noopener noreferrer">분석 원자료 확인 ↗</a>` : ''}<a href="./index.html#methodology">전체 이름·입지 분석의 자료와 방법 ↗</a>`;
  setLoadState(`전국 ${count(meta.record_count)}개 단지의 실제 현재명 · 사전·분절 규칙에 따른 탐색적 후보 · 사용승인연대 기준`);
}

function buildDecadeRanks() {
  state.decadeRanks.clear();
  for (const decade of state.data.decades) {
    const candidates = state.rows.map((row) => ({ token: row.token, cohort: row.by_decade.find((item) => Number(item.decade) === decade.decade) }))
      .filter((item) => number(item.cohort?.count) != null && item.cohort.count > 0)
      .sort((left, right) => right.cohort.count - left.cohort.count || left.token.localeCompare(right.token, 'ko'));
    const ranks = new Map();
    let rank = 0;
    let previous = null;
    candidates.forEach((item, index) => {
      if (item.cohort.count !== previous) rank = index + 1;
      ranks.set(item.token, rank);
      previous = item.cohort.count;
    });
    state.decadeRanks.set(decade.decade, ranks);
  }
}

function renderRanking() {
  const search = normalize($('#modifier-query').value);
  const filtered = state.rows.filter((row) => !search || normalize(row.token).includes(search));
  state.visible = filtered.slice(0, 20);
  $('#search-status').textContent = `${search ? `검색 결과 ${count(filtered.length)}개` : `전체 ${count(state.rows.length)}개 후보`} · ${Math.min(filtered.length, 20)}개 표시`;
  $('#clear-search').disabled = !search;
  const maximum = Math.max(1, ...state.visible.map((row) => row.count));
  $('#ranking-bars').innerHTML = state.visible.length ? state.visible.map((row) => `<button type="button" class="modifier-bar-row" data-token="${escape(row.token)}" aria-pressed="${state.selected === row.token}" aria-label="${escape(row.token)}, 포함 ${count(row.count)}개 단지, 전체의 ${percent(row.share)}"><span class="modifier-bar-rank">${row.rank}</span><span class="modifier-bar-token">${escape(row.token)}</span><span class="modifier-bar-track" aria-hidden="true"><span class="modifier-bar-fill" style="--bar-width:${100 * row.count / maximum}%"></span></span><span class="modifier-bar-value">${count(row.count)}<small>${percent(row.share)}</small></span></button>`).join('') : '<div class="empty-state"><strong>일치하는 표현이 없습니다.</strong><p>검색어를 짧게 입력하거나 초기화해 전체 후보를 확인해 주세요. 원자료에 없거나 제외한 표현은 순위에 표시하지 않습니다.</p></div>';
  $('#ranking-table').innerHTML = state.visible.length ? state.visible.map((row) => `<tr><td>${row.rank}</td><th scope="row">${escape(row.token)}</th><td>${count(row.count)}</td><td>${percent(row.share)}</td></tr>`).join('') : '<tr><td colspan="4" class="table-empty">표시할 검색 결과가 없습니다.</td></tr>';
  renderHeatmap();
}

function renderHeatmap() {
  if (!state.data) return;
  const decades = state.data.decades;
  const rows = state.visible;
  $$('[data-heatmap-mode]').forEach((button) => button.setAttribute('aria-pressed', String(button.dataset.heatmapMode === state.heatmapMode)));
  $('#heatmap-description').textContent = state.heatmapMode === 'share' ? '색이 진할수록 해당 연대 전체 단지 중 포함 비율이 높습니다.' : '색이 진할수록 해당 연대에서 순위가 높습니다. 0건은 순위를 매기지 않습니다.';
  if (!rows.length || !decades.length) {
    $('#heatmap').innerHTML = '<div class="empty-state"><strong>표시할 연대별 수치가 없습니다.</strong><p>검색어를 초기화하거나 사용승인연도가 있는 분석 자료를 확인해 주세요.</p></div>';
    return;
  }
  const maxShare = Math.max(0.000001, ...rows.flatMap((row) => decades.map((decade) => {
    const item = row.by_decade.find((value) => Number(value.decade) === decade.decade);
    return item ? shareOf(item, item.total ?? decade.total) ?? 0 : 0;
  })));
  const cells = (row) => decades.map((decade) => {
    const item = row.by_decade.find((value) => Number(value.decade) === decade.decade);
    if (!item || number(item.count) == null || !(item.total ?? decade.total)) return '<td class="missing" aria-label="측정 자료 없음">—</td>';
    const value = shareOf(item, item.total ?? decade.total);
    const rank = state.decadeRanks.get(decade.decade)?.get(row.token);
    const intensity = state.heatmapMode === 'share' ? Math.min(1, (value ?? 0) / maxShare) : rank ? 1 / Math.sqrt(rank) : 0;
    const shade = item.count === 0 ? 0 : Math.max(1, Math.min(shades.length - 1, Math.ceil(intensity * (shades.length - 1))));
    const content = state.heatmapMode === 'share' ? percent(value) : rank ? `${rank}위` : '0건';
    const description = `${row.token}, ${decadeLabel(decade.decade)}, ${count(item.count)} / ${count(item.total ?? decade.total)}개 단지, ${percent(value)}${rank ? `, 공동 ${rank}위` : ''}`;
    return `<td style="background:${shades[shade]};color:${shade >= 5 ? '#fff' : '#1e2124'}"><button type="button" data-token="${escape(row.token)}" title="${escape(description)}" aria-label="${escape(description)}">${content}</button></td>`;
  }).join('');
  $('#heatmap').innerHTML = `<table><caption class="sr-only">현재 검색 결과 상위 20개 표현의 사용승인연대별 ${state.heatmapMode === 'share' ? '포함 비율' : '빈도 순위'}</caption><thead><tr><th scope="col">표현</th>${decades.map((decade) => `<th scope="col">${decadeLabel(decade.decade)}<small>n=${count(decade.total)}</small></th>`).join('')}</tr></thead><tbody>${rows.map((row) => `<tr><th scope="row"><button type="button" data-token="${escape(row.token)}" aria-pressed="${state.selected === row.token}">${escape(row.token)}</button></th>${cells(row)}</tr>`).join('')}</tbody></table>`;
}

function markedName(name, token) {
  const text = String(name ?? '');
  const index = text.indexOf(token);
  return index < 0 ? escape(text) : `${escape(text.slice(0, index))}<mark>${escape(text.slice(index, index + token.length))}</mark>${escape(text.slice(index + token.length))}`;
}

function exampleName(example) {
  return (example.name_clean ?? cleanApartmentName(example.name)) || '명칭 미확인';
}

function exampleOriginal(example) {
  return exampleName(example) !== example.name ? `<details class="source-variants"><summary>원문 확인</summary><p class="field-help">${escape(example.name)}</p></details>` : '';
}

function renderDetail(token) {
  const row = rowFor(token);
  if (!row) return;
  state.selected = token;
  $('#selected-token-title').textContent = token;
  $('#selected-token-stat').textContent = `${count(row.count)}개 단지 · 전체 ${count(state.data.meta.record_count)}개 중 ${percent(row.share)}`;
  const strictCount = number(row.strict_count);
  const reviewCount = number(row.context_only_count);
  $('#token-context-review').hidden = strictCount == null && reviewCount == null;
  $('#token-context-review').textContent = [strictCount != null ? `미검토 고유명·외국어 태그 이웃을 제외하면 ${count(strictCount)}개` : '', reviewCount != null ? `문맥 검토 조건 때문에 추가된 단지 ${count(reviewCount)}개` : ''].filter(Boolean).join(' · ') + '입니다. 신뢰구간이 아닌 분절 조건에 따른 차이이며, 이웃 표현이 브랜드로 확정되었다는 뜻은 아닙니다.';
  const examples = row.examples.slice(0, 8);
  $('#example-count').textContent = examples.length ? `${examples.length}개 표시` : '';
  $('#token-examples').innerHTML = examples.length ? examples.map((example) => `<article><p class="example-original">${markedName(exampleName(example), example.surface || token)}</p><p class="example-address"><span>${escape([example.sido, example.sigungu, example.dong].filter(Boolean).join(' ') || '지역 미기재')}</span><span class="example-year">${Number.isInteger(example.year) ? `${example.year}년 승인` : '승인연도 미상'}</span></p>${exampleOriginal(example)}</article>`).join('') : '<p class="muted">이 표현의 이름 예시가 결과 자료에 포함되지 않았습니다. 전체 이름 탐색에서 직접 확인해 주세요.</p>';
  $('#example-note').textContent = row.examples.length > 8 ? `제공된 ${count(row.examples.length)}개 예시 중 8개를 표시합니다. 예시 목록은 대표 표본이 아닙니다.` : '원자료에서 확인한 일부 예시입니다. 예시 목록은 대표 표본이 아니며 고유명·중의어가 남아 있을 수 있습니다.';
  renderMiniChart(row);
  $$('[data-token]').forEach((button) => {
    if (button.hasAttribute('aria-pressed')) button.setAttribute('aria-pressed', String(button.dataset.token === token));
  });
}

function renderMiniChart(row) {
  const records = state.data.decades.map((decade) => {
    const item = row.by_decade.find((value) => Number(value.decade) === decade.decade);
    return { decade: decade.decade, share: item ? shareOf(item, item.total ?? decade.total) : null };
  });
  if (!records.some((item) => number(item.share) != null)) {
    $('#selected-token-timeline').innerHTML = '<p class="mini-chart-empty">이 표현의 연대별 수치는 아직 제공되지 않았습니다.</p>';
    return;
  }
  const width = 420, height = 150, left = 40, right = 20, top = 16, bottom = 30;
  const maximum = Math.max(0.005, ...records.map((item) => item.share ?? 0));
  const x = (index) => left + (width - left - right) * (records.length > 1 ? index / (records.length - 1) : 0.5);
  const y = (value) => height - bottom - value / maximum * (height - top - bottom);
  let paths = [[]];
  records.forEach((record, index) => { if (record.share == null) { if (paths.at(-1).length) paths.push([]); } else paths.at(-1).push(`${x(index)},${y(record.share)}`); });
  const svg = `<svg viewBox="0 0 ${width} ${height}" role="img" aria-labelledby="mini-title"><title id="mini-title">${escape(row.token)}의 연대별 포함 비율. ${escape(records.map((item) => `${decadeLabel(item.decade)} ${percent(item.share)}`).join(', '))}</title><line x1="${left}" x2="${width - right}" y1="${y(0)}" y2="${y(0)}" stroke="#cdd1d5"/><line x1="${left}" x2="${width - right}" y1="${y(maximum)}" y2="${y(maximum)}" stroke="#e6e8ea"/><text x="${left - 8}" y="${y(0) + 4}" text-anchor="end">0</text><text x="${left - 8}" y="${y(maximum) + 4}" text-anchor="end">${(maximum * 100).toFixed(1)}%</text>${paths.filter((path) => path.length).map((path) => `<polyline fill="none" stroke="#346fb2" stroke-width="2.5" stroke-linejoin="round" points="${path.join(' ')}"/>`).join('')}${records.map((record, index) => `${record.share != null ? `<circle cx="${x(index)}" cy="${y(record.share)}" r="3" fill="#346fb2"><title>${decadeLabel(record.decade)}: ${percent(record.share)}</title></circle>` : ''}<text x="${x(index)}" y="${height - 8}" text-anchor="middle">${record.decade}</text>`).join('')}</svg>`;
  $('#selected-token-timeline').innerHTML = `<p class="mini-chart-label">사용승인연대별 포함 비율 · 각 연대 전체 단지 기준</p>${svg}`;
}

function renderMemeTokens() {
  const rows = state.data.meme_tokens;
  $('#meme-tokens').innerHTML = rows.length ? rows.map((row) => `<button type="button" class="meme-token-card" data-token="${escape(row.token)}" aria-pressed="${state.selected === row.token}"><span class="meme-token-name">${escape(row.token)}</span><strong>${count(row.count)}<small>개 단지</small></strong><span class="meme-token-share">전체 표본의 ${percent(row.share)}</span></button>`).join('') : '<div class="empty-state"><strong>가설 표현의 별도 집계가 아직 없습니다.</strong><p>현재 결과 파일의 토큰 목록에서 표현을 검색해 주세요.</p></div>';
}

function renderFormTokens() {
  const rows = state.data.form_ranking;
  $('#form-token-section').hidden = !rows.length;
  $('#form-token-body').innerHTML = rows.map((row) => `<tr><th scope="row">${escape(row.token)}</th><td>${count(row.count)}</td><td>${percent(row.share)}</td></tr>`).join('');
}

async function loadAnalysis() {
  if (state.loading) return;
  state.loading = true;
  $('#retry-load').disabled = true;
  setLoadState('전국 단지 이름의 토큰 분석 결과를 불러오고 있습니다.');
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 30000);
  try {
    const response = await fetch(new URL('./data/modifier-analysis.json', import.meta.url), { signal: controller.signal, cache: 'no-cache' });
    if (!response.ok) throw new Error(response.status === 404 ? '분석 결과 파일이 아직 준비되지 않았습니다. 잠시 후 다시 불러오거나 전체 이름 탐색을 이용해 주세요.' : '분석 자료를 불러오지 못했습니다. 연결을 확인하고 다시 시도해 주세요.');
    const data = await response.json();
    if (!data.meta || !Number.isInteger(data.meta.record_count) || data.meta.record_count < 0 || !Array.isArray(data.decades)) throw new Error('분석 자료의 표본 수와 연대 정보를 확인해 주세요.');
    const decades = data.decades.map((item) => ({ ...item, decade: Number(item.decade) })).sort((left, right) => left.decade - right.decade);
    if (decades.some((item) => !Number.isInteger(item.decade) || !Number.isInteger(item.total) || item.total < 0)) throw new Error('연대별 전체 단지 수를 확인해 주세요.');
    const rows = validateRows(data.ranking, data.meta.record_count).sort((left, right) => right.count - left.count || left.token.localeCompare(right.token, 'ko'));
    let previousCount = null, rank = 0;
    rows.forEach((row, index) => { if (row.count !== previousCount) rank = index + 1; row.rank = rank; previousCount = row.count; });
    state.data = { ...data, decades, ranking: rows, meme_tokens: validateRows(data.meme_tokens || [], data.meta.record_count), form_ranking: validateRows(data.form_ranking || [], data.meta.record_count) };
    state.rows = rows;
    state.selected = rows[0]?.token || null;
    state.loading = false;
    $('#modifier-query').disabled = false;
    renderSummary();
    buildDecadeRanks();
    renderRanking();
    renderMemeTokens();
    renderFormTokens();
    if (state.selected) renderDetail(state.selected);
  } catch (error) {
    state.loading = false;
    setLoadState(error.name === 'AbortError' ? '분석 자료를 읽는 시간이 길어지고 있습니다. 연결을 확인한 뒤 다시 불러와 주세요.' : error.message || '분석 자료를 불러오지 못했습니다.', true);
    if (!state.data) {
      $('#ranking-bars').innerHTML = '<div class="empty-state"><strong>표시할 분석 결과가 없습니다.</strong><p>위의 다시 불러오기를 누르거나 전체 이름 탐색으로 이동해 주세요. 예시 수치로 대체하지 않습니다.</p><a class="outline-button small" href="./index.html">전체 이름 탐색</a></div>';
      $('#ranking-table').innerHTML = '<tr><td colspan="4" class="table-empty">분석 자료가 연결되지 않았습니다.</td></tr>';
      $('#heatmap').innerHTML = '<div class="empty-state"><strong>연대별 자료를 표시할 수 없습니다.</strong><p>분석 결과 파일을 불러온 뒤 연대별 비율을 확인할 수 있습니다.</p></div>';
      $('#meme-tokens').innerHTML = '<div class="empty-state"><strong>표현별 집계 자료가 연결되지 않았습니다.</strong><p>실제 집계값이 확인되기 전까지 수치를 표시하지 않습니다.</p></div>';
      $('#analysis-warnings').innerHTML = '<p>결과 자료가 연결되면 분석에 사용한 사전 버전과 주의사항을 함께 표시합니다.</p>';
      $('#search-status').textContent = '분석 자료가 연결되면 표현을 검색할 수 있습니다.';
    }
  } finally {
    clearTimeout(timer);
    $('#retry-load').disabled = false;
    $('#analysis-body').setAttribute('aria-busy', 'false');
  }
}

$('#modifier-search-form').addEventListener('submit', (event) => { event.preventDefault(); if (state.data) renderRanking(); });
$('#modifier-query').addEventListener('input', () => { if (state.data) renderRanking(); });
$('#clear-search').addEventListener('click', () => { $('#modifier-query').value = ''; renderRanking(); $('#modifier-query').focus(); });
$('#analysis-body').addEventListener('click', (event) => {
  const button = event.target.closest('[data-token]');
  if (!button || !state.data) return;
  renderDetail(button.dataset.token);
  if (button.closest('.meme-token-grid') || button.closest('.modifier-heatmap')) {
    $('#selected-token-title').setAttribute('tabindex', '-1');
    $('#selected-token-title').focus({ preventScroll: true });
    $('.modifier-detail').scrollIntoView({ behavior: 'auto', block: 'start' });
  }
});
$$('[data-heatmap-mode]').forEach((button) => button.addEventListener('click', () => { state.heatmapMode = button.dataset.heatmapMode; renderHeatmap(); }));
$('#retry-load').addEventListener('click', loadAnalysis);
$('#display-settings').addEventListener('click', () => { const open = $('#display-panel').hidden; $('#display-panel').hidden = !open; $('#display-settings').setAttribute('aria-expanded', String(open)); });
$('#font-scale').addEventListener('change', (event) => document.documentElement.style.setProperty('--font-scale', event.target.value));
$('#high-contrast').addEventListener('change', (event) => document.documentElement.classList.toggle('high-contrast', event.target.checked));
loadAnalysis();
