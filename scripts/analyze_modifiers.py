#!/usr/bin/env python3
"""Reproducible apartment-name NLP report; never changes the original GIS tokens."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.name_cleaning import clean_apartment_name, VERSION as NAME_CLEANING_VERSION


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def main():
    from src.modifier_nlp import analyze_records

    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=ROOT / "data/processed/complexes.jsonl")
    parser.add_argument("--entities", type=Path, default=ROOT / "dictionaries/nlp-entities.json")
    parser.add_argument("--accepted-terms", type=Path, default=ROOT / "dictionaries/nlp-modifiers.v1.json")
    parser.add_argument("--draft", action="store_true", help="Write intermediate audit only; do not publish results")
    args = parser.parse_args()
    records = [json.loads(line) for line in args.input.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not records or any(r.get("is_demo") is not False for r in records):
        raise ValueError("NLP report requires nonempty, real apartment records")
    if len({r['id'] for r in records}) != len(records):
        raise ValueError("Duplicate complex IDs")
    if not args.draft and (read_json(args.accepted_terms) or {}).get('status') != 'reviewed_vocabulary_candidates':
        raise ValueError('Publish only after the vocabulary review is complete')
    result = analyze_records(records, entity_dictionary=read_json(args.entities), accepted_terms=read_json(args.accepted_terms))
    work = ROOT / "work/nlp"
    work.mkdir(parents=True, exist_ok=True)
    (work / "full-analysis.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"method": result.get("method"), "totals": result.get("totals"), "top": result.get("ranking", [])[:25]}, ensure_ascii=False))
    if args.draft:
        return
    build_report(records, result, args)


def build_report(records, result, args):
    """Aggregate audited occurrences with a stable all-complex denominator."""
    by_id = {r['id']: r for r in records}
    decade_totals = Counter(r['approval_year'] // 10 * 10 for r in records if r.get('approval_year') is not None)
    token_ids = defaultdict(set)
    strict_ids = defaultdict(set)
    example_surfaces = {}
    for item in result['occurrences']:
        if item['status'] == 'accepted':
            token_ids[item['canonical']].add(item['id'])
            example_surfaces.setdefault((item['canonical'], item['id']), item['surface'])
            if not item.get('context_requires_review', False):
                strict_ids[item['canonical']].add(item['id'])
    rows = []
    for token, ids in sorted(token_ids.items(), key=lambda item: (-len(item[1]), item[0])):
        matches = [by_id[identity] for identity in sorted(ids)]
        by_decade = Counter(r['approval_year'] // 10 * 10 for r in matches if r.get('approval_year') is not None)
        by_region = Counter(r['sido'] for r in matches)
        examples, seen_regions = [], set()
        for record in matches:
            if record['sido'] in seen_regions:
                continue
            examples.append(record)
            seen_regions.add(record['sido'])
            if len(examples) == 6:
                break
        example_ids = {r['id'] for r in examples}
        for record in matches:
            if len(examples) == 6:
                break
            if record['id'] not in example_ids:
                examples.append(record)
                example_ids.add(record['id'])
        rows.append({
            'token': token, 'count': len(ids), 'share': len(ids) / len(records),
            'strict_count': len(strict_ids[token]), 'context_only_count': len(ids - strict_ids[token]),
            'examples': [{**{k: r[k] for k in ('id', 'name', 'sido', 'sigungu', 'dong')}, 'name_clean': clean_apartment_name(r['name']), 'year': r.get('approval_year'), 'surface': example_surfaces[(token, r['id'])]} for r in examples],
            'by_decade': [{'decade': d, 'count': by_decade[d], 'total': total, 'share': by_decade[d] / total} for d, total in sorted(decade_totals.items())],
            'by_region': [{'sido': sido, 'count': count} for sido, count in by_region.most_common()],
        })
    row_by_token = {r['token']: r for r in rows}
    meme_words = ['더퍼스트', '퍼스트', '센트럴', '리버', '레이크', '오션', '마리나', '파크', '뷰', '포레', '포레스트', '메트로', '에듀', '시티']
    meme = [row_by_token.get(t, {'token': t, 'count': 0, 'share': 0, 'examples': [], 'by_decade': [{'decade': d, 'count': 0, 'total': n, 'share': 0} for d, n in sorted(decade_totals.items())]}) for t in meme_words]
    warnings = [
        '현재 공시가격 DB 명칭을 사용승인연도별로 비교합니다. 실제 명명 시점이나 개명 이력을 나타내지 않습니다.',
        f'각 토큰의 분모는 전국 {len(records):,}개 전체 단지이며, 같은 단지 안에서 반복된 토큰은 한 번 셉니다. 연대별 분모는 해당 연대 전체 단지입니다.',
        '브랜드·기업·주소 기반 지명을 보호한 뒤 잔여 표현을 Kiwi와 네이밍 어휘 사전으로 분절했습니다. 주거 형식은 별도 표로 분리합니다.',
        '고유명과 일반어는 중의적입니다. 미검토 형태소·고유명 후보는 확정 후보 순위에서 보류하므로 모든 한국어 단어의 전수 순위가 아닙니다.',
        '순위에는 미검토 고유명·외국어와 이웃한 수식어도 포함합니다. 엄격 집계는 그러한 이웃이 없는 잔여 구간에서만 셉니다. 두 기준 모두 브랜드·지명 제거의 완전성이나 명명 의도를 보증하지 않습니다.',
        '로얄/로열은 로열, 씨티/시티는 시티로 통합합니다. 리버뷰·파크뷰 등은 리버/파크와 뷰로 나누며 원문 표기는 보존합니다.',
        '예시 표기에서 동·호·층 번호, 차수, 숫자만 있는 괄호·지번 부가정보를 제거합니다. 원문과 단지 ID는 보존하며 정리한 이름이 같아져도 단지를 합치지 않습니다.',
        '브랜드 내부의 자연 표현은 제외합니다. 예: 아이파크의 파크, 꿈에그린의 그린, 포레나의 포레는 세지 않습니다.',
        '사진의 작명법은 가설입니다. 이름 빈도는 주변 시설 존재나 조망을 증명하지 않습니다.',
        '이 페이지는 전국 스냅샷입니다. 기존 지도 페이지와 분절 규칙이 달라 토큰별 수가 다를 수 있습니다.',
    ]
    # Counts and formats remain explicit in the module; no rows are silently discarded here.
    forms = []
    for entry in result.get('form_ranking', []):
        token = entry.get('token', entry.get('canonical'))
        count = entry.get('count', entry.get('complex_count', entry.get('document_frequency')))
        if token is None or count is None:
            raise ValueError(f'Unexpected form_ranking schema: {entry}')
        forms.append({'token': token, 'count': count, 'share': count / len(records)})
    meta = {
        'record_count': len(records), 'valid_year_count': sum(decade_totals.values()),
        'dictionary_version': read_json(ROOT / 'dictionaries/tokens.v1.json')['version'],
        'method': 'Kiwi 형태소 분석 + 브랜드·기업·지명 보호 + 네이밍 어휘 검수',
        'method_details': result.get('method'), 'coverage': result.get('totals'),
        'coverage_note': f"검수 어휘 {len(rows)}종 중 하나 이상이 추출된 단지는 {len(set().union(*token_ids.values())):,}개입니다. 이는 사전과 분절 조건에 따른 어휘 범위이며, 나머지 단지에 수식어가 없다는 뜻은 아닙니다.",
        'excluded_categories': ['브랜드', '기업·공급 주체', '주소 기반 지명', '숫자·차수', '주거 형식(별도 표)'],
        'warnings': warnings, 'source_url': 'https://www.data.go.kr/data/15106861/fileData.do', 'source_snapshot_date': '2026-08-31',
        'observed_at': '2026-09-28', 'generated_at': datetime.now(timezone.utc).isoformat(),
        'input_sha256': hashlib.sha256(args.input.read_bytes()).hexdigest(),
        'entity_dictionary_version': (read_json(args.entities) or {}).get('version'),
        'accepted_terms_version': (read_json(args.accepted_terms) or {}).get('version'),
        'name_cleaning_version': NAME_CLEANING_VERSION,
    }
    output = {'meta': meta, 'ranking': rows, 'decades': [{'decade': d, 'total': n} for d, n in sorted(decade_totals.items())], 'meme_tokens': meme, 'form_ranking': forms,
              'excluded_counts_top25_per_category': {k: v[:25] for k, v in result.get('excluded_counts', {}).items()}, 'candidate_ranking': result.get('candidate_ranking', [])[:100]}
    dest = ROOT / 'results'
    dest.mkdir(exist_ok=True)
    (dest / 'modifier-analysis.json').write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding='utf-8')
    public = ROOT / 'frontend/data'
    public.mkdir(exist_ok=True)
    (public / 'modifier-analysis.json').write_text(json.dumps(output, ensure_ascii=False, separators=(',', ':')), encoding='utf-8')
    lines = ['# 브랜드와 지명을 제외한 아파트 이름 NLP', '', f"전국 {len(records):,}개 단지의 현재 공시가격 DB 이름을 분석했다. 빈도는 토큰이 한 번 이상 나타나는 단지 수이고, 비율의 분모는 전체 {len(records):,}개다.", '',
             f"형태소 분석과 어휘 검수를 거쳐 {len(rows)}종을 집계했다. 하나 이상의 후보가 추출된 단지는 {len(set().union(*token_ids.values())):,}개이며, 단어가 추출되지 않은 단지도 전체 분모에 포함한다.", '',
             '## 수식어 후보 최빈순', '', '| 순위 | 토큰 | 단지 수 | 전체 비율 | 엄격 조건 단지 수 | 정리한 이름 예시 |', '|---|---|---:|---:|---:|---|']
    previous_count, rank = None, 0
    for index, row in enumerate(rows[:30], 1):
        if row['count'] != previous_count:
            rank = index
            previous_count = row['count']
        examples = ', '.join(r['name_clean'].replace('|', '\\|') for r in row['examples'][:2])
        lines.append(f"| {rank} | {row['token']} | {row['count']:,} | {row['share']:.2%} | {row['strict_count']:,} | {examples} |")
    lines += ['', '## 사용승인 연대별 포함 비율', '', '| 토큰 | 1980년대 | 1990년대 | 2000년대 | 2010년대 | 2020–2025년 |', '|---|---:|---:|---:|---:|---:|']
    for token in ['그린', '파크', '로얄', '로열', '타운', '팰리스', '센트럴', '리버', '포레', '포레스트', '장미', '청솔']:
        row = row_by_token.get(token)
        if not row:
            continue
        rates = {v['decade']: v['share'] for v in row['by_decade']}
        lines.append('| ' + token + ' | ' + ' | '.join(f'{rates.get(d, 0):.2%}' for d in (1980, 1990, 2000, 2010, 2020)) + ' |')
    lines += ['', '### 관찰', '', '절대 개수는 해당 연대의 전체 단지 수에 영향을 받으므로 어휘 변화는 포함 비율로 비교한다. 아래는 현재 명칭과 승인 연대의 관계에 대한 기술통계이며, 실제 과거 명칭이나 유행의 인과효과는 아니다.', '']
    for token in ['그린', '파크', '센트럴', '리버', '포레']:
        row = row_by_token.get(token)
        if not row:
            continue
        cohorts = {v['decade']: v for v in row['by_decade']}
        if 1990 in cohorts and 2020 in cohorts:
            old, new = cohorts[1990], cohorts[2020]
            lines.append(f"- {token}: 1990년대 승인 단지의 {old['share']:.2%} ({old['count']:,}/{old['total']:,}) → 2020–2025년 승인 단지의 {new['share']:.2%} ({new['count']:,}/{new['total']:,}).")
    lines += ['', '## 주거 형식과 공급 형태: 별도 집계', '', '브랜드·지명을 제외하더라도 이 표현은 남는다. 의미 수식어 표에서는 분리했으며 여기서 빈도를 공개한다.', '', '| 토큰 | 단지 수 | 전체 비율 |', '|---|---:|---:|']
    lines += [f"| {r['token']} | {r['count']:,} | {r['share']:.2%} |" for r in forms[:15]]
    lines += ['', '## 사진의 작명법 어휘', '', '| 토큰 | 단지 수 | 전체 비율 |', '|---|---:|---:|']
    lines += [f"| {r['token']} | {r['count']:,} | {r['share']:.2%} |" for r in meme]
    lines += ['', '## 방법과 한계', ''] + ['- ' + value for value in warnings]
    lines += ['', '## 재현', '', '```bash', 'python3 -m venv .venv-nlp', '.venv-nlp/bin/python -m pip install -r requirements-nlp.txt', '.venv-nlp/bin/python scripts/analyze_modifiers.py', '```', '',
              '- [자료 원문: 한국부동산원 공동주택 단지 식별정보](https://www.data.go.kr/data/15106861/fileData.do)',
              '- [Kiwi 공식 문서](https://github.com/bab2min/kiwipiepy)',
              '- 보호 사전: `dictionaries/nlp-entities.json`; 추가 검수 어휘: `dictionaries/nlp-modifiers.v1.json`.',
              '- 감사 산출물: `work/nlp/full-analysis.json`. 미검토 후보를 실제 수식어로 단정하지 않는다.', '']
    (dest / 'modifier-analysis.md').write_text('\n'.join(lines), encoding='utf-8')
    print(json.dumps({'ranking_tokens': len(rows), 'form_tokens': len(forms), 'public_json': str(public / 'modifier-analysis.json')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
