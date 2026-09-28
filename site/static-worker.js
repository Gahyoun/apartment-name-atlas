/* Static API implementation. No prior-region p-value is shipped or reused. */
const FEATURES = ['park', 'water', 'forest', 'school', 'metro'];
const WS = /[\u0009-\u000d\u001c-\u0020\u0085\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]+/gu;
const normalize = value => String(value ?? '').normalize('NFKC').replace(WS, '');
const numericDesignator = value => /[0-9]/u.test(normalize(value)) && /^[0-9제차단지블록동호층번지()\[\]{},·;:/~\-–—.ㆍ]+$/u.test(normalize(value));
const countableToken = token => token.category !== 'number' && !numericDesignator(token.canonical)
  && (token.category !== 'unclassified' || /[\p{L}\p{N}]/u.test(token.canonical));
const first = (params, key, fallback = '') => {
  const value = Object.hasOwn(params, key) ? params[key] : fallback;
  return String(Array.isArray(value) ? value[0] ?? '' : value ?? '');
};
const compareText = (a, b) => {
  const left = Array.from(a), right = Array.from(b);
  for (let i = 0; i < Math.min(left.length, right.length); i++) {
    const delta = left[i].codePointAt(0) - right[i].codePointAt(0);
    if (delta) return delta;
  }
  return left.length - right.length;
};
const integer = (params, key, fallback, min = 0, max = 2100) => {
  const raw = first(params, key);
  if (!raw) return fallback;
  if (!/^\d+$/.test(raw) || !Number.isSafeInteger(Number(raw)) || Number(raw) < min || Number(raw) > max) {
    throw new Error(`${key}는 ${min}~${max} 범위의 정수여야 합니다.`);
  }
  return Number(raw);
};
const mean = values => values.reduce((sum, value) => sum + value, 0) / values.length;
const median = values => {
  if (!values.length) return null;
  const sorted = [...values].sort((a, b) => a - b), middle = Math.floor(sorted.length / 2);
  return sorted.length % 2 ? sorted[middle] : (sorted[middle - 1] + sorted[middle]) / 2;
};
const round1 = value => {
  if (value == null) return null;
  const scaled = value * 10, lower = Math.floor(scaled);
  // Binary-exact quarter ties need Python's round-to-even rule; other cases use the actual double.
  if (Number.isInteger(value * 4) && scaled - lower === 0.5) return (lower % 2 === 0 ? lower : lower + 1) / 10;
  return Number(value.toFixed(1));
};

export class StaticEngine {
  constructor(core, loadJSON, config = {}) {
    if (core.schema_version !== 1) throw new Error('지원하지 않는 데이터 버전입니다.');
    this.core = core;
    this.loadJSON = loadJSON;
    this.config = config;
    this.cache = new Map();
    this.templates = core.token_catalog.map(([canonical, categoryIndex, flags, reasonIndex, extra]) => ({
      canonical, category: core.categories[categoryIndex], provisional: Boolean(flags & 1),
      needs_review: Boolean(flags & 2), review_reasons: core.review_reason_sets[reasonIndex], ...(extra || {}),
    }));
    this.records = core.records.map((packed, index) => {
      const [id, name, regionIndex, approval_year, coordinates, runs, override, sourceIndex] = packed;
      const [sido, sigungu, dong, dong_code] = core.regions[regionIndex];
      const tokenIDs = [];
      for (let i = 0; i < runs.length; i += 2) tokenIDs.push(runs[i]);
      const templates = tokenIDs.map(id => this.templates[id]);
      const keys = new Set(templates.filter(countableToken).map(t => t.canonical));
      return {
        index, id, name, sido, sigungu, dong, dong_code, approval_year,
        lat: coordinates ? coordinates[0] : null, lon: coordinates ? coordinates[1] : null,
        name_normalized: override ?? name, source_url: core.sources[sourceIndex],
        tokenIDs, runs, keys, needs_review: templates.some(t => t.needs_review),
        brand: templates.some(t => t.category === 'brand'),
        place: templates.some(t => t.category === 'place'),
        placeMatch: templates.some(t => t.category === 'place' && t.address_match === true),
      };
    });
  }

  fold(value) {
    return Array.from(normalize(value)).map(character => this.core.casefold_map?.[character] ?? character.toLowerCase()).join('');
  }

  cached(path) {
    if (!path) return Promise.resolve(null);
    if (!this.cache.has(path)) {
      this.cache.set(path, Promise.resolve(this.loadJSON(path)).catch(error => { this.cache.delete(path); throw error; }));
    }
    return this.cache.get(path);
  }

  filters(params) {
    const result = Object.fromEntries(['sido', 'sigungu', 'dong'].map(key => [key, first(params, key).trim()]));
    result.year_from = integer(params, 'year_from', null, 1800);
    result.year_to = integer(params, 'year_to', null, 1800);
    if (result.year_from != null && result.year_to != null && result.year_from > result.year_to) throw new Error('year_from은 year_to보다 클 수 없습니다.');
    return result;
  }

  select(filters, includeYears = true, rows = this.records) {
    return rows.filter(row => {
      if (['sido', 'sigungu', 'dong'].some(key => filters[key] && row[key] !== filters[key])) return false;
      if (!includeYears) return true;
      if ((filters.year_from != null || filters.year_to != null) && row.approval_year == null) return false;
      if (filters.year_from != null && row.approval_year < filters.year_from) return false;
      if (filters.year_to != null && row.approval_year > filters.year_to) return false;
      return true;
    });
  }

  regions(params) {
    const level = first(params, 'level', 'sido');
    if (!['sido', 'sigungu', 'dong'].includes(level)) throw new Error('level은 sido, sigungu, dong 중 하나여야 합니다.');
    const filters = { sido: level !== 'sido' ? first(params, 'sido').trim() : '', sigungu: level === 'dong' ? first(params, 'sigungu').trim() : '' };
    const counts = new Map();
    for (const row of this.select(filters)) if (row[level]) counts.set(row[level], (counts.get(row[level]) || 0) + 1);
    return { level, options: [...counts].sort(([a], [b]) => compareText(a, b)).map(([value, count]) => ({ value, label: value, count })) };
  }

  analysis(params) {
    const filters = this.filters(params), regional = this.select(filters, false), selected = this.select(filters, true, regional);
    const tokens = [...new Set(first(params, 'tokens', this.core.default_tokens.join(',')).split(',').map(normalize).filter(token => token && !numericDesignator(token)))];
    if (tokens.length > 30) throw new Error('한 번에 비교할 토큰은 30개 이하로 선택하세요.');
    const byYear = new Map(), topCounts = new Map(), categoryFor = new Map(), provisional = new Set();
    let valid = 0, lowYear = null, highYear = null;
    for (const row of selected) {
      if (row.approval_year != null) {
        valid++;
        lowYear = lowYear == null ? row.approval_year : Math.min(lowYear, row.approval_year);
        highYear = highYear == null ? row.approval_year : Math.max(highYear, row.approval_year);
        if (!byYear.has(row.approval_year)) byYear.set(row.approval_year, []);
        byYear.get(row.approval_year).push(row);
      }
      for (const key of row.keys) topCounts.set(key, (topCounts.get(key) || 0) + 1);
      for (const id of row.tokenIDs) {
        const token = this.templates[id];
        if (!row.keys.has(token.canonical)) continue;
        categoryFor.set(token.canonical, token.category);
        if (token.provisional) provisional.add(token.canonical);
      }
    }
    const series = [], cumulative = Object.fromEntries(tokens.map(token => [token, 0]));
    let cumulativeTotal = 0;
    if (valid) {
      for (let year = filters.year_from ?? lowYear; year <= (filters.year_to ?? highYear); year++) {
        const members = byYear.get(year) || [], counts = {}, shares = {};
        cumulativeTotal += members.length;
        for (const token of tokens) {
          counts[token] = members.reduce((count, row) => count + Number(row.keys.has(token)), 0);
          cumulative[token] += counts[token];
          shares[token] = members.length ? counts[token] / members.length : null;
        }
        series.push({ year, total_count: members.length, cumulative_total: cumulativeTotal, token_counts: counts, token_cumulative: { ...cumulative }, token_shares: shares });
      }
    }
    const category = first(params, 'category').trim(), query = this.fold(first(params, 'token_q'));
    if (category && !this.core.categories.includes(category)) throw new Error('지원하지 않는 토큰 category입니다.');
    const ranking = [...topCounts].filter(([token]) => (!category || categoryFor.get(token) === category) && (!query || this.fold(token).includes(query)))
      .sort(([a, ac], [b, bc]) => bc - ac || compareText(a, b))
      .map(([token, count]) => ({ token, category: categoryFor.get(token), count, share: selected.length ? count / selected.length : null, provisional: provisional.has(token) }));
    const warnings = [...this.core.warnings];
    const missing = regional.filter(row => row.approval_year == null).length;
    if (missing) warnings.push(`지역 표본 중 사용승인연도 미상 ${missing.toLocaleString('en-US')}개가 있습니다. 연도 범위를 지정하면 이 행은 현재 표본에서도 제외됩니다.`);
    if (!this.records.length) warnings.push('실자료가 연결되지 않아 결과가 비어 있습니다. 데모 수치로 대체하지 않습니다.');
    else if (!selected.length) warnings.push('현재 필터에 해당하는 단지가 없습니다.');
    else if (!valid) warnings.push('현재 표본에 유효한 사용승인연도가 없어 시계열을 만들지 않았습니다.');
    return {
      sample_count: selected.length, total_count: regional.length, dataset_count: this.records.length,
      valid_year_count: valid, mapped_count: selected.filter(row => row.lat != null && row.lon != null).length,
      brand_count: selected.filter(row => row.brand).length, unique_token_count: topCounts.size,
      place_name_count: selected.filter(row => row.place).length, place_local_match_count: selected.filter(row => row.placeMatch).length,
      year_min: lowYear, year_max: highYear, selected_tokens: tokens, series,
      top_tokens: ranking.slice(0, 100), ranked_token_count: ranking.length, warnings, demo: false,
    };
  }

  tokens(params) {
    const result = this.analysis({ ...params, tokens: '' });
    return { total: result.ranked_token_count, items: result.top_tokens, sample_count: result.sample_count, truncated: result.ranked_token_count > result.top_tokens.length, demo: false };
  }

  expand(row) {
    const characters = Array.from(row.name_normalized), tokens = [];
    let start = 0;
    for (let i = 0; i < row.runs.length; i += 2) {
      const end = start + row.runs[i + 1];
      tokens.push({ ...this.templates[row.runs[i]], surface: characters.slice(start, end).join(''), start, end });
      start = end;
    }
    return {
      id: row.id, name: row.name, sido: row.sido, sigungu: row.sigungu, dong: row.dong, dong_code: row.dong_code,
      approval_year: row.approval_year, lat: row.lat, lon: row.lon, name_normalized: row.name_normalized,
      source_url: row.source_url, is_demo: false, tokens, needs_review: row.needs_review,
    };
  }

  async withDetails(rows) {
    const shards = await Promise.all([...new Set(rows.map(row => this.core.detail_shards[row.sido]).filter(Boolean))].map(async path => [path, await this.cached(path)]));
    const byPath = new Map(shards);
    return rows.map(row => {
      const result = this.expand(row), shard = byPath.get(this.core.detail_shards[row.sido]), values = shard?.rows?.[row.id];
      if (values) (shard.fields || this.core.detail_fields).forEach((field, index) => { result[field] = values[index]; });
      return result;
    });
  }

  async complexes(params) {
    let selected = this.select(this.filters(params));
    const mappedOnly = first(params, 'mapped_only', '0');
    if (!['', '0', '1'].includes(mappedOnly)) throw new Error('mapped_only는 0 또는 1이어야 합니다.');
    if (mappedOnly === '1') selected = selected.filter(row => row.lat != null && row.lon != null);
    const query = this.fold(first(params, 'q'));
    if (query) selected = selected.filter(row => this.fold(row.name_normalized).includes(query) || this.fold(row.id).includes(query));
    const limit = integer(params, 'limit', 100, 1, 5000), offset = integer(params, 'offset', 0, 0, 10000000);
    const page = selected.slice(offset, offset + limit), items = await this.withDetails(page);
    const more = offset + items.length < selected.length;
    return { total: selected.length, items, limit, offset, truncated: more, next_offset: more ? offset + items.length : null, demo: false };
  }

  mapGeoJSON(params) {
    const selected = this.select(this.filters(params)), mapped = selected.filter(row => row.lat != null && row.lon != null);
    const limit = integer(params, 'limit', 2000, 1, 5000), offset = integer(params, 'offset', 0, 0, 10000000), page = mapped.slice(offset, offset + limit);
    return {
      type: 'FeatureCollection', features: page.map(row => ({ type: 'Feature', geometry: { type: 'Point', coordinates: [row.lon, row.lat] }, properties: Object.fromEntries(['id', 'name', 'sido', 'sigungu', 'dong', 'approval_year'].map(key => [key, row[key]])) })),
      sample_count: selected.length, mapped_count: mapped.length, limit, offset,
      truncated: offset + page.length < mapped.length, next_offset: offset + page.length < mapped.length ? offset + page.length : null, demo: false,
    };
  }

  async gis(params) {
    const selected = this.select(this.filters(params)), token = normalize(first(params, 'token', '파크')), feature = first(params, 'feature', 'park').trim();
    if (!token || Array.from(token).length > 80) throw new Error('비교할 토큰을 입력해 주세요.');
    if (!FEATURES.includes(feature)) throw new Error('지원하지 않는 입지 유형입니다.');
    const threshold = integer(params, 'threshold_m', 500, 50, 10000);
    const distanceData = await this.cached(this.config.data?.distances);
    const featureIndex = distanceData?.features.indexOf(feature) ?? -1, index = new Map();
    if (featureIndex >= 0) for (const row of distanceData.rows) {
      const value = row[featureIndex + 1];
      if (typeof value === 'number' && Number.isFinite(value) && value >= 0) index.set(row[0], value);
    }
    const measured = selected.filter(row => index.has(row.index)).map(row => ({ row, distance: index.get(row.index), target: row.keys.has(token) }));
    const targets = measured.filter(item => item.target).map(item => item.distance), controls = measured.filter(item => !item.target).map(item => item.distance);
    const summary = {
      target_n: targets.length, control_n: controls.length,
      target_median_m: round1(median(targets)), control_median_m: round1(median(controls)),
      target_near_share: targets.length ? targets.filter(value => value <= threshold).length / targets.length : null,
      control_near_share: controls.length ? controls.filter(value => value <= threshold).length / controls.length : null,
      median_difference_m: targets.length && controls.length ? round1(median(targets) - median(controls)) : null,
      permutation_p_value: null, adjusted_mean_difference_m: null, matched_target_n: 0, matched_control_n: 0, matched_strata: 0,
    };
    const warnings = [
      '거리 중앙값은 현재 선택 표본의 기술통계입니다. 인과효과나 조망권을 뜻하지 않습니다.',
      '좌표와 지형 자료가 연결된 표본만 비교합니다. 미측정 단지는 0m 또는 시설 없음으로 처리하지 않습니다.',
      '현재 지도와 현재 이름을 비교하며 명명 당시의 지형·시설을 복원한 분석이 아닙니다.',
      'OpenStreetMap 지형의 누락, 좌표 결합 오류, 단지 대표점과 출입구의 차이가 결과에 영향을 줍니다.',
      '정적 배포에서는 선택 표본의 거리 통계만 다시 계산하며 순열검정 p값을 제공하지 않습니다. 검정은 로컬 Python API에서 실행할 수 있습니다. 다른 필터의 p값을 재사용하지 않습니다.',
    ];
    let status = !measured.length ? 'not_ready' : 'insufficient';
    let message = !measured.length ? '선택 범위의 거리 측정 자료를 먼저 확보해 주세요.' : '토큰 포함·비포함 표본이 각 5개 이상일 때 비교 결과를 제공합니다.';
    if (targets.length >= 5 && controls.length >= 5) {
      status = 'ready';
      message = '현재 선택 표본의 실측 거리를 다시 계산한 탐색적 비교입니다.';
      const strata = new Map();
      for (const item of measured) if (Number.isInteger(item.row.approval_year)) {
        const key = JSON.stringify([item.row.sido, item.row.sigungu, Math.floor(item.row.approval_year / 10)]);
        if (!strata.has(key)) strata.set(key, []);
        strata.get(key).push(item);
      }
      const matched = [...strata.values()].filter(items => items.some(item => item.target) && items.some(item => !item.target));
      summary.matched_target_n = matched.reduce((n, items) => n + items.filter(item => item.target).length, 0);
      summary.matched_control_n = matched.reduce((n, items) => n + items.filter(item => !item.target).length, 0);
      summary.matched_strata = matched.length;
      if (summary.matched_target_n >= 5 && summary.matched_control_n >= 5) {
        summary.adjusted_mean_difference_m = round1(matched.reduce((sum, items) => {
          const named = items.filter(item => item.target).map(item => item.distance), other = items.filter(item => !item.target).map(item => item.distance);
          return sum + named.length * (mean(named) - mean(other));
        }, 0) / summary.matched_target_n);
      } else warnings.push('같은 시군구·사용승인 연대에서 비교 가능한 표본이 부족해 보정 비교를 표시하지 않습니다.');
    }
    return { status, message, token, feature, threshold_m: threshold, summary, coverage: { measured_count: measured.length, population_count: selected.length }, warnings, method: '단지 대표 좌표에서 지도에 기록된 지형·시설까지의 평면 직선거리. 정적 화면에서 선택 표본의 기술통계를 재계산.', demo: false };
  }

  async gisFeatures(params) {
    const feature = first(params, 'feature', 'all').trim();
    if (!['all', ...FEATURES].includes(feature)) throw new Error('지원하지 않는 입지 유형입니다.');
    const selected = feature === 'all' ? FEATURES : [feature];
    const collections = await Promise.all(selected.map(key => this.cached(this.config.data?.features?.[key])));
    const features = collections.flatMap(collection => collection?.features || []);
    return { type: 'FeatureCollection', features, feature, feature_count: features.length, demo: false };
  }

  async request(path, params = {}) {
    switch (path) {
      case '/api/meta': return this.core.meta;
      case '/api/map-config': return await this.cached(this.config.data?.map_config) || { provider: 'osm', kakao_js_key: null, fallback_provider: 'osm' };
      case '/api/regions': return this.regions(params);
      case '/api/analysis': return this.analysis(params);
      case '/api/tokens': return this.tokens(params);
      case '/api/complexes': return this.complexes(params);
      case '/api/map.geojson': return this.mapGeoJSON(params);
      case '/api/gis': return this.gis(params);
      case '/api/gis-features': return this.gisFeatures(params);
      default: throw new Error('알 수 없는 API 경로입니다.');
    }
  }
}

if (typeof self !== 'undefined' && typeof self.postMessage === 'function') {
  let engine;
  self.onmessage = async ({ data }) => {
    try {
      let result;
      if (data.path === 'initialize') {
        const { config, baseURL } = data.params;
        const loadJSON = async path => {
          const response = await fetch(new URL(path, baseURL));
          if (!response.ok) throw new Error(`정적 자료를 불러오지 못했습니다 (${response.status}).`);
          return response.json();
        };
        engine = new StaticEngine(await loadJSON(config.data.core), loadJSON, config);
        result = { ready: true };
      } else {
        if (!engine) throw new Error('정적 자료 초기화가 필요합니다.');
        result = await engine.request(data.path, data.params);
      }
      self.postMessage({ id: data.id, result });
    } catch (error) {
      self.postMessage({ id: data.id, error: error instanceof Error ? error.message : '정적 분석 중 오류가 발생했습니다.' });
    }
  };
}
