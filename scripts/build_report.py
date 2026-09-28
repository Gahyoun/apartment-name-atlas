#!/usr/bin/env python3
"""Rebuild descriptive findings and a dictionary sensitivity audit from cached data."""
from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from statistics import mean
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.gis import compute_gis
from backend.server import DataStore, tokenize_with_address

PAIRS = [("파크", "park"), ("리버", "water"), ("포레", "forest"),
         ("메트로", "metro"), ("에듀", "school")]
LABELS = {"파크": "공원", "리버": "수면·하천", "포레": "산림·수림",
          "메트로": "지하철 시설", "에듀": "학교"}
PARK_CANDIDATES = ("한솔솔파크", "대주파크빌", "문영퀸즈파크")


def meters(value):
    return f"{value:,.1f}m" if value is not None else "미측정"


def p_value(value):
    return f"{value:.3f}" if value is not None else "검정 표본 부족"


def sensitivity(store):
    """Exclude flagged rows, without relabeling them as verified non-brands."""
    measured_ids = {row["id"] for row in store.distances if row["feature"] == "park"}
    variants = {}
    for key, terms in [("exclude_possible_brands", PARK_CANDIDATES),
                       ("exclude_possible_brands_and_parkio", PARK_CANDIDATES + ("파크리오",))]:
        excluded = [row for row in store.records if row["id"] in measured_ids
                    and "파크" in row["_token_keys"]
                    and any(term in row["name_normalized"] for term in terms)]
        ids = {row["id"] for row in excluded}
        remaining = [row for row in store.records if row["id"] not in ids]
        variants[key] = {
            "excluded_terms": list(terms), "excluded_count": len(excluded),
            "exclusion_basis": "unresolved_semantic_candidates_not_verified_brand_reclassification",
            "excluded_records": [{k: row[k] for k in ("id", "name", "sido", "sigungu", "dong", "approval_year")}
                                 for row in excluded],
            "gis": compute_gis(remaining, store.distances, "파크", "park"),
        }
    return variants


def dictionary_audit(store):
    """Rerun tokenization with/without this supplement on the identical sample."""
    supplement = json.loads((ROOT / "dictionaries/brands.supplement.json").read_text(encoding="utf-8"))
    additions = {entry["canonical"] for entry in supplement["entries"]}
    baseline = {**store.dictionary, "entries": [entry for entry in store.dictionary["entries"]
                                               if entry["canonical"] not in additions]}
    measured_ids = {row["id"] for row in store.distances}
    audit = {token: {"national_before": 0, "national_after": 0,
                     "gis_before": 0, "gis_after": 0} for token in ("파크", "리버", "포레", "하늘")}
    for row in store.records:
        old_keys = {token["canonical"] for token in tokenize_with_address(row, baseline)[1]}
        for token, counts in audit.items():
            counts["national_before"] += token in old_keys
            counts["national_after"] += token in row["_token_keys"]
            counts["gis_before"] += row["id"] in measured_ids and token in old_keys
            counts["gis_after"] += row["id"] in measured_ids and token in row["_token_keys"]
    return {"removed_canonical_entries_for_baseline": sorted(additions),
            "comparison": "current dictionary versus the same dictionary with this supplement removed",
            "target_counts": audit}


def main():
    store = DataStore()
    buckets = defaultdict(list)
    for row in store.records:
        if row["approval_year"] is not None:
            buckets[row["approval_year"] // 10 * 10].append(row)
    decades = []
    for decade, rows in sorted(buckets.items()):
        counts = {token: sum(token in row["_token_keys"] for row in rows)
                  for token in ("그린", "리버", "파크", "포레", "롯데캐슬", "카이저")}
        decades.append({"decade": decade, "n": len(rows),
                        "mean_name_length": round(mean(len(row["name_normalized"]) for row in rows), 2),
                        "recognized_brand_count": sum(any(token["category"] == "brand" for token in row["tokens"]) for row in rows),
                        "token_counts": counts,
                        "token_shares": {key: value / len(rows) for key, value in counts.items()}})
    gis = {token: store.gis({"token": token, "feature": feature}) for token, feature in PAIRS}
    variants = sensitivity(store)
    audit = dictionary_audit(store)
    gis_meta = json.loads((ROOT / "data/processed/gis_meta.json").read_text(encoding="utf-8"))
    inputs = ["data/processed/complexes.jsonl", "data/processed/gis_distances.jsonl",
              "dictionaries/tokens.v1.json", "dictionaries/brands.supplement.json"]
    output = {"built_at": datetime.now(timezone.utc).isoformat(), "dataset": store.meta()["dataset"],
              "dictionary_version": store.dictionary["version"], "decades": decades, "gis": gis,
              "park_sensitivity": variants, "dictionary_sensitivity": audit, "gis_metadata": gis_meta,
              "input_sha256": {path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest() for path in inputs},
              "statistical_method": {
                  "descriptive_statistic": "unadjusted full measured-sample median distance by token presence",
                  "test_statistic": "target-count-weighted within-sigungu-by-approval-decade mean difference",
                  "sign": "target minus control; negative means shorter target-group distance",
                  "permutations": 999, "seed": 20260928, "alternative": "two-sided",
                  "multiple_testing_adjusted": False, "causal_interpretation": False}}
    dest = ROOT / "results"
    dest.mkdir(exist_ok=True)
    (dest / "initial-findings.json").write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    metadata = output["dataset"]
    seoul_n = sum(row["sido"] == "서울특별시" for row in store.records)
    lines = ["# 첫 실데이터 분석", "",
             f"전국 자료 수록 아파트 **{metadata['record_count']:,}개**, 17개 시도, 사용승인연도 **{metadata['year_min']}–{metadata['year_max']}**를 분석했다. 지리 비교는 서울 공식 좌표가 엄격히 연결된 **{metadata['coordinate_count']:,}개** 표본이다. 사전 버전은 `{store.dictionary['version']}`이다.", "",
             "현재 공시가격 DB의 이름을 사용승인연도별로 비교한다. 실제 명명 시점·개명 이력의 분석이 아니다. 전국 모든 아파트와 철거 단지를 망라한다고 주장하지 않는다.", "",
             "## 사용승인 연대별 현재 이름", "",
             "| 사용승인 연대 | 단지 수 | 평균 글자 수 | 그린 토큰 | 리버 토큰 | 파크 토큰 | 포레 토큰 |",
             "|---|---:|---:|---:|---:|---:|---:|"]
    for row in decades:
        lines.append(f"| {row['decade']}년대 | {row['n']:,} | {row['mean_name_length']:.2f} | " +
                     " | ".join(f"{row['token_shares'][token] * 100:.2f}%" for token in ("그린", "리버", "파크", "포레")) + " |")
    lines += ["", "분모는 해당 사용승인 연대의 단지 수다. 2020년대는 2025년까지다. 평균 글자 수는 공백을 제거하고 유니코드를 정규화한 이름 길이다. 각 토큰은 한 단지에서 여러 번 나타나도 한 번 센다.", "",
              "긴 사전 표현을 먼저 보존하므로 `아이파크` 내부 `파크`, `꿈에그린` 내부 `그린`, `포레나`·`휴포레` 내부 `포레`는 독립 수식어로 세지 않는다. 이 정의는 브랜드 전체가 전달하는 자연 이미지를 측정하지 않는다. 미등록 브랜드·부분문자열 분절 오류는 남아 있다.", "",
              "## 현재 이름과 현재 입지", "",
              "먼저 좌표와 지형이 연결된 전체 표본의 **보정하지 않은 거리 중앙값**을 비교했다. 음의 차이는 토큰 포함 단지의 거리가 더 짧다는 뜻이다.", "",
              "| 토큰 / 대상 지형 | 포함 n | 비포함 n | 포함 거리 중앙값 | 비포함 거리 중앙값 | 중앙값 차이 |",
              "|---|---:|---:|---:|---:|---:|---:|"]
    for token, result in gis.items():
        summary = result["summary"]
        lines.append(f"| {token} / {LABELS[token]} | {summary['target_n']:,} | {summary['control_n']:,} | {meters(summary['target_median_m'])} | {meters(summary['control_median_m'])} | {meters(summary['median_difference_m'])} |")
    lines += ["", "다음 표는 **같은 시군구 × 사용승인 10년대** 안의 평균 거리 차이를 포함 단지 수로 가중한 통계량이다. 중앙값 차이와 다른 값이며, p값은 오직 이 층화 평균 차이에 대한 검정이다. 검정에 포함되는 단지 수 역시 전체 기술통계의 표본 수와 다를 수 있다.", "",
              "| 토큰 | 층화 평균 거리 차이 | 검정 포함 n | 검정 비포함 n | 비교 묶음 수 | 양측 순열 p값 |",
              "|---|---:|---:|---:|---:|---:|"]
    for token, result in gis.items():
        summary = result["summary"]
        if summary["permutation_p_value"] is None:
            lines.append(f"| {token} | 미계산 | — | — | — | 검정 표본 부족 |")
        else:
            lines.append(f"| {token} | {meters(summary['adjusted_mean_difference_m'])} | {summary['matched_target_n']:,} | {summary['matched_control_n']:,} | {summary['matched_strata']:,} | {p_value(summary['permutation_p_value'])} |")
    shorter = [token for token in ("파크", "리버", "포레") if gis[token]["summary"]["median_difference_m"] < 0]
    lines += ["", f"이 서울 매칭 표본에서 {'·'.join(shorter)} 토큰 포함 집단의 전체 거리 중앙값이 비교집단보다 작았다. 수면·하천은 연못·호수·강 중심선도 포함하므로 리버 결과를 곧바로 한강 접근성으로 해석할 수 없다. 메트로·에듀는 포함 단지가 적어 검정하지 않았다.", "",
              "검정은 999회, 난수 시드 20260928로 재현되며 최소 p값은 0.001이다. 여러 토큰 탐색에 대한 보정은 하지 않았다. 지역·연대 묶음 안에서도 동네·사업단위 공간상관, 브랜드, 단지규모 등 교란이 남는다. p값을 명명 의도·인과효과·조망권·부동산 가치의 증거로 읽지 않는다.", "",
              "## 브랜드와 분절에 대한 민감도", "",
              "누락 브랜드 14개와 리버티 전체어 보호를 추가한 뒤 동일한 46,385개 이름을 다시 분절했다. 아래는 현재 사전에서 보완 항목을 제거한 경우와 포함한 경우의 비교다.", "",
              "| 토큰 | 전국 보완 전 → 후 | 서울 GIS 보완 전 → 후 |",
              "|---|---:|---:|"]
    for token, counts in audit["target_counts"].items():
        lines.append(f"| {token} | {counts['national_before']:,} → {counts['national_after']:,} | {counts['gis_before']:,} → {counts['gis_after']:,} |")
    lines += ["", "포레나·휴포레는 브랜드로 보호하고, 리버티는 리버와 구별하기 위해 잠정 전체어로 보호했다. 이는 해당 표현에 자연 연상이 없다는 판정이 아니다. 근거 URL은 `dictionaries/brands.supplement.json`에 있다.", "",
              "파크 표본에는 공식 근거 검토가 끝나지 않은 한솔솔파크·대주파크빌·문영퀸즈파크 7개 관측행이 남아 있다. 이 7개를 **표본에서 제외**한 경우와, 경계가 모호한 개별 단지명 파크리오까지 총 8개를 제외한 경우를 계산했다. 사전 분류를 확정하거나 비포함 집단으로 옮긴 결과가 아니다. 거리 자체는 바꾸지 않았다.", "",
              "| 파크 비교 | 제외 n | 포함 n | 비포함 n | 포함 / 비포함 중앙값 | 층화 평균 차이 | 검정 포함 / 비포함 n | p값 |",
              "|---|---:|---:|---:|---:|---:|---:|---:|"]
    comparisons = [("기본 사전", 0, gis["파크"])] + [
        ("의심 브랜드 제외", variants["exclude_possible_brands"]["excluded_count"], variants["exclude_possible_brands"]["gis"]),
        ("의심 브랜드 + 파크리오 제외", variants["exclude_possible_brands_and_parkio"]["excluded_count"], variants["exclude_possible_brands_and_parkio"]["gis"])]
    for label, n, result in comparisons:
        summary = result["summary"]
        lines.append(f"| {label} | {n} | {summary['target_n']} | {summary['control_n']} | {meters(summary['target_median_m'])} / {meters(summary['control_median_m'])} | {meters(summary['adjusted_mean_difference_m'])} | {summary['matched_target_n']} / {summary['matched_control_n']} | {p_value(summary['permutation_p_value'])} |")
    adjusted_values = [result["summary"]["adjusted_mean_difference_m"] for _, _, result in comparisons]
    if all(value is not None and value < 0 for value in adjusted_values):
        lines += ["", f"세 경우 모두 파크 포함 단지의 층화 평균 거리가 더 짧았고, 차이의 크기는 {min(abs(value) for value in adjusted_values):.1f}–{max(abs(value) for value in adjusted_values):.1f}m였다. 이 후보들의 제외만으로 거리 차이의 방향이 뒤집히지는 않았다."]
    lines += ["", "민감도 비교에서도 중앙값과 층화 평균의 방향·크기를 함께 본다. 의심 사례는 데이터의 의미 검수에서 정했으므로 독립적인 사전등록 검정이 아니다. 제외한 ID·이름과 모든 수치는 JSON의 `park_sensitivity`에 기록했다. 이 소수 후보의 제외가 전체 사전의 정확성을 보장하지 않는다.", "",
              "## 자료 품질과 해석 범위", "",
              f"- GIS 표본은 전국 자료의 {metadata['coordinate_count'] / metadata['record_count'] * 100:.1f}%, 서울 수록 단지 {seoul_n:,}개 중 {metadata['coordinate_count'] / seoul_n * 100:.1f}%다. 이름 유사도로 매칭하지 않고 두 원천에서 각각 유일한 도로명주소·법정동·사용승인일의 일치를 요구했다. 성공한 서울 일부로 선택되었으므로 전국 입지 연관성으로 일반화하지 않는다.",
              f"- 다른 명칭 출처와 비어 있지 않은 값이 달랐던 단지는 {metadata['audit']['name_disagreement_count']:,}개다. 단순 표기 차이도 포함한다. 기본 이름은 공시가격 명칭이며 홍보명과 같다고 보장하지 않는다.",
              f"- 사용한 OSM 스냅샷 시각은 `{gis_meta['osm_timestamp']}`다. 현재 이름·현재 환경의 비교이며 명명 시점, 당시 시설·지형, 개명 원인이나 철거·생존 편향은 복원하지 않았다.",
              "- EPSG:5179에서 공식 단지 대표점부터 가장 가까운 기록 도형까지의 직선거리다. 면 안은 0m, 외부는 경계까지이며 내부 홀을 구분한다. 단지 출입구, 도보 접근성, 조망과는 다르다.",
              "- 좌표나 지형이 없거나 추출 경계 밖 더 가까운 시설 가능성을 배제하지 못하면 미측정으로 둔다. 누락을 0m·비인접·시설 없음으로 바꾸지 않는다. 현재 5개 지형 모두 2,361개 단지의 측정치가 있지만 OSM의 실제 시설 누락은 별개 문제다.",
              "- 공식 좌표 오차·연결 오류, OSM 도형·태그 누락, 이름 원천 차이, 부분문자열 오탐을 수동 검수해야 한다. 브랜드 내부 자연 이미지까지 포함하는 별도 정의 및 명칭 원천을 바꾼 분석은 후속 과제다.", "",
              "## 출처와 재현", "",
              "- [한국부동산원 공동주택 단지 식별정보](https://www.data.go.kr/data/15106861/fileData.do)",
              "- [서울시 공동주택 아파트 정보](https://data.seoul.go.kr/dataList/OA-15818/A/1/datasetView.do)",
              "- [OpenStreetMap 기여자, ODbL](https://www.openstreetmap.org/copyright)",
              "- 프로젝트 폴더에서 `python3 scripts/build_report.py`를 실행한다. 인터넷 재수집 없이 저장된 원자료·사전·거리 파일을 사용한다. 입력 SHA-256과 사전 버전은 JSON에 기록한다.",
              "- 상세 측정·비교 기준: `docs/gis-method.md`. 원자료와 연결 검증: `docs/collection-report.md`.", ""]
    (dest / "initial-findings.md").write_text("\n".join(lines), encoding="utf-8")
    print("Saved results/initial-findings.md and initial-findings.json")
    print(json.dumps({"dictionary": store.dictionary["version"],
                      "gis": {token: result["summary"] for token, result in gis.items()},
                      "park_sensitivity": {key: value["gis"]["summary"] for key, value in variants.items()}}, ensure_ascii=False))


if __name__ == "__main__":
    main()
