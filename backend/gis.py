"""Descriptive distance comparison and a within-district/cohort permutation test.

Input distances are measured independently of name labels; no coordinates are
imputed. A missing feature or coordinate is never interpreted as zero distance.
"""
from collections import defaultdict
import math
import random
from statistics import mean, median

FEATURES = {'park', 'water', 'forest', 'school', 'metro'}


def has_token(record, token):
    return any(t.get('canonical', t.get('surface')) == token for t in record.get('tokens', []))


def compute_gis(records, distances, token='파크', feature='park', threshold_m=500):
    if feature not in FEATURES:
        raise ValueError('지원하지 않는 입지 유형입니다.')
    threshold_m = float(threshold_m)
    if not math.isfinite(threshold_m) or not 50 <= threshold_m <= 10000:
        raise ValueError('거리 기준은 50~10,000m 사이여야 합니다.')
    if not token or len(token) > 80:
        raise ValueError('비교할 토큰을 입력해 주세요.')
    index = {}
    for row in distances:
        if row.get('feature') != feature:
            continue
        d = row.get('distance_m')
        if isinstance(d, (int, float)) and not isinstance(d, bool) and math.isfinite(d) and d >= 0:
            index[str(row['id'])] = row
    measured = [(r, index[str(r['id'])]['distance_m']) for r in records if str(r['id']) in index]
    targets = [d for r, d in measured if has_token(r, token)]
    controls = [d for r, d in measured if not has_token(r, token)]
    summary = {
        'target_n': len(targets), 'control_n': len(controls),
        'target_median_m': round(median(targets), 1) if targets else None,
        'control_median_m': round(median(controls), 1) if controls else None,
        'target_near_share': sum(d <= threshold_m for d in targets) / len(targets) if targets else None,
        'control_near_share': sum(d <= threshold_m for d in controls) / len(controls) if controls else None,
        'median_difference_m': round(median(targets) - median(controls), 1) if targets and controls else None,
        'permutation_p_value': None,
        'adjusted_mean_difference_m': None,
        'matched_target_n': 0, 'matched_control_n': 0, 'matched_strata': 0,
    }
    warnings = [
        '거리 중앙값은 현재 선택 표본의 기술통계입니다. 인과효과나 조망권을 뜻하지 않습니다.',
        '좌표와 지형 자료가 연결된 표본만 비교합니다. 미측정 단지는 0m 또는 시설 없음으로 처리하지 않습니다.',
        '현재 지도와 현재 이름을 비교하며 명명 당시의 지형·시설을 복원한 분석이 아닙니다.',
        'OpenStreetMap 지형의 누락, 좌표 결합 오류, 단지 대표점과 출입구의 차이가 결과에 영향을 줍니다.',
    ]
    status = 'not_ready' if not measured else 'insufficient'
    message = '선택 범위의 거리 측정 자료를 먼저 확보해 주세요.' if not measured else '토큰 포함·비포함 표본이 각 5개 이상일 때 비교 검정을 제공합니다.'
    if len(targets) >= 5 and len(controls) >= 5:
        status = 'ready'
        message = '실측 거리의 탐색적 비교입니다. 표본 범위와 측정 방법을 함께 확인해 주세요.'
        strata = defaultdict(list)
        for r, d in measured:
            year = r.get('approval_year')
            if isinstance(year, int) and not isinstance(year, bool):
                strata[(r.get('sido'), r.get('sigungu'), year // 10)].append((has_token(r, token), d))
        matched = []
        for values in strata.values():
            n = sum(label for label, _ in values)
            if n and n < len(values):
                matched.append((values, n))
        nt = sum(n for values, n in matched)
        nc = sum(len(values) - n for values, n in matched)
        summary.update(matched_target_n=nt, matched_control_n=nc, matched_strata=len(matched))
        if nt >= 5 and nc >= 5:
            # Each district/cohort is weighted by the number of named complexes.
            observed = sum(n * (mean(d for label,d in values if label) - mean(d for label,d in values if not label)) for values,n in matched) / nt
            rng = random.Random(20260928)
            exceed = 0
            for _ in range(999):
                value = 0
                for values,n in matched:
                    pool = [d for _,d in values]
                    rng.shuffle(pool)
                    value += n * (mean(pool[:n]) - mean(pool[n:]))
                exceed += abs(value / nt) >= abs(observed)
            summary['permutation_p_value'] = (exceed + 1) / 1000
            summary['adjusted_mean_difference_m'] = round(observed, 1)
            warnings.append('p값은 같은 시군구·사용승인 10년대 안의 가중 평균 거리 차이를 검정합니다(양측 순열 999회). 위의 전체 중앙값 차이에 대한 p값이 아닙니다. 최소 해상도는 0.001이며 다중검정 보정은 하지 않았습니다.')
            warnings.append('동네·사업단위 공간상관, 브랜드와 단지규모 등 남은 교란을 통제한 결과가 아닙니다.')
        else:
            warnings.append('같은 시군구·사용승인 연대에서 비교 가능한 표본이 부족해 보정 비교와 p값은 표시하지 않습니다.')
    return {
        'status': status, 'message': message, 'token': token, 'feature': feature,
        'threshold_m': threshold_m, 'summary': summary,
        'coverage': {'measured_count': len(measured), 'population_count': len(records)},
        'warnings': warnings,
        'method': '단지 대표 좌표에서 지도에 기록된 지형·시설까지의 평면 직선거리. 상세 기준은 GIS 문서 참조.',
    }
