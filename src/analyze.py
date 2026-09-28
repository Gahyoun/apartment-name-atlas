#!/usr/bin/env python3
"""Explore current apartment names by completion cohort using only the stdlib."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
import json
from pathlib import Path
import re
import sys
import tempfile
import unicodedata
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DICTIONARY = ROOT / "dictionaries" / "tokens.v1.json"
CATEGORIES = {"brand", "company", "place", "nature", "premium", "building_type", "number"}
REQUIRED_FIELDS = {
    "complex_id", "name_raw", "region", "completion_year", "observed_at",
    "source_url", "is_demo",
}
WARNINGS = [
    "현재 이름을 준공연대별로 비교한 결과이며, 실제 명명 시점이나 이름 변경의 역사가 아닙니다.",
    "준공연도가 없는 단지는 토큰화에 포함하지만 연대별 집계의 분모에서 제외합니다.",
    "토큰 비율의 분모는 해당 준공연대·지역의 단지 수이며 같은 단지의 반복 토큰은 한 번 셉니다.",
    "사전 분류는 탐색용입니다. provisional 토큰도 집계에 포함되므로 review.jsonl 검토가 필요합니다.",
    "입력 표본의 범위와 수집 방식에 따라 결과가 달라지며 전국의 역사적 유행을 대표하지 않을 수 있습니다.",
]


class AnalysisError(ValueError):
    """An actionable input or dictionary error."""


def normalize_name(value: str) -> str:
    return "".join(unicodedata.normalize("NFKC", value).split())


def load_records(path: Path, *, allow_demo: bool = False) -> list[dict]:
    records, seen = [], {}
    try:
        with path.open(encoding="utf-8-sig") as handle:
            for line_number, line in enumerate(handle, 1):
                if not line.strip():
                    continue
                location = f"{path}:{line_number}"
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise AnalysisError(f"{location}: JSON 구문 오류: {exc.msg}") from exc
                if not isinstance(record, dict):
                    raise AnalysisError(f"{location}: 각 줄은 JSON 객체여야 합니다.")
                missing = REQUIRED_FIELDS - record.keys()
                extra = record.keys() - REQUIRED_FIELDS
                if missing or extra:
                    raise AnalysisError(
                        f"{location}: 스키마 필드를 확인하세요. "
                        f"누락={sorted(missing)}, 알 수 없는 필드={sorted(extra)}"
                    )
                for key in ("complex_id", "name_raw", "region", "observed_at"):
                    if not isinstance(record[key], str) or not record[key].strip():
                        raise AnalysisError(f"{location}: {key}는 비어 있지 않은 문자열이어야 합니다.")
                if record["complex_id"] != record["complex_id"].strip():
                    raise AnalysisError(f"{location}: complex_id 앞뒤 공백을 제거하세요.")
                if record["region"] != record["region"].strip():
                    raise AnalysisError(f"{location}: region 앞뒤 공백을 제거하고 시도 표기를 통일하세요.")
                if not normalize_name(record["name_raw"]):
                    raise AnalysisError(f"{location}: 정규화 후 name_raw가 비어 있습니다.")
                if type(record["is_demo"]) is not bool:
                    raise AnalysisError(f"{location}: is_demo는 JSON true/false여야 합니다.")
                if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", record["observed_at"]):
                    raise AnalysisError(f"{location}: observed_at은 YYYY-MM-DD 형식이어야 합니다.")
                try:
                    observed = date.fromisoformat(record["observed_at"])
                except ValueError as exc:
                    raise AnalysisError(f"{location}: observed_at은 실제 달력 날짜여야 합니다.") from exc
                year = record["completion_year"]
                if year is not None and (
                    type(year) is not int or not 1800 <= year <= observed.year
                ):
                    raise AnalysisError(
                        f"{location}: completion_year는 null 또는 1800~관측연도({observed.year})의 정수여야 합니다."
                    )
                source = record["source_url"]
                if source is not None:
                    if not isinstance(source, str):
                        raise AnalysisError(f"{location}: source_url은 URL 문자열 또는 null이어야 합니다.")
                    try:
                        parsed = urlsplit(source)
                        valid = parsed.scheme in {"http", "https"} and bool(parsed.hostname)
                    except ValueError:
                        valid = False
                    if not valid or any(char.isspace() for char in source):
                        raise AnalysisError(f"{location}: source_url에 유효한 http(s) 출처 URL을 입력하세요.")
                if not record["is_demo"] and source is None:
                    raise AnalysisError(f"{location}: 실데이터에는 source_url이 필수입니다.")
                if record["is_demo"] and not allow_demo:
                    raise AnalysisError(
                        f"{location}: 데모 자료를 거부했습니다. 별도 예제 실행에만 --allow-demo를 사용하세요. "
                        "실자료와 혼합해도 결과 전체가 demo=true로 표시됩니다."
                    )
                identity = record["complex_id"]
                if identity in seen:
                    raise AnalysisError(
                        f"{location}: 중복 complex_id={identity!r} (첫 행 {seen[identity]}). "
                        "현재명 단지 1행으로 정리하고, 이름 변경 이력은 별도 파일로 분리하세요."
                    )
                seen[identity] = line_number
                records.append(record)
    except (OSError, UnicodeError) as exc:
        raise AnalysisError(f"입력 파일을 UTF-8로 읽을 수 없습니다: {path}: {exc}") from exc
    if not records:
        raise AnalysisError(f"{path}: 입력이 비어 있습니다. JSON 객체를 한 줄에 하나씩 입력하세요.")
    return records


def load_dictionary(path: Path = DEFAULT_DICTIONARY) -> dict:
    try:
        dictionary = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise AnalysisError(f"사전을 읽을 수 없습니다: {path}: {exc}") from exc
    if not isinstance(dictionary, dict) or not isinstance(dictionary.get("version"), str):
        raise AnalysisError("사전에는 문자열 version이 필요합니다.")
    if not isinstance(dictionary.get("entries"), list) or not dictionary["entries"]:
        raise AnalysisError("사전에는 비어 있지 않은 entries 배열이 필요합니다.")
    seen = set()
    for entry in dictionary["entries"]:
        if not isinstance(entry, dict) or entry.get("category") not in CATEGORIES:
            raise AnalysisError(f"사전의 category가 잘못되었습니다: {entry!r}")
        if not isinstance(entry.get("canonical"), str) or not entry["canonical"].strip():
            raise AnalysisError(f"사전 canonical은 비어 있지 않은 문자열이어야 합니다: {entry!r}")
        aliases = entry.get("aliases", [entry["canonical"]])
        if not isinstance(aliases, list) or not aliases:
            raise AnalysisError(f"사전 aliases는 비어 있지 않은 배열이어야 합니다: {entry!r}")
        if type(entry.get("provisional", False)) is not bool:
            raise AnalysisError(f"사전 provisional은 boolean이어야 합니다: {entry!r}")
        reasons = entry.get("review_reasons", [])
        if not isinstance(reasons, list) or any(not isinstance(reason, str) for reason in reasons):
            raise AnalysisError(f"사전 review_reasons는 문자열 배열이어야 합니다: {entry!r}")
        for alias in aliases:
            if not isinstance(alias, str) or not normalize_name(alias):
                raise AnalysisError(f"사전 alias는 비어 있지 않은 문자열이어야 합니다: {entry!r}")
            normalized = normalize_name(alias)
            if normalized in seen:
                raise AnalysisError(f"정규화 후 중복 사전 alias={normalized!r}. 한 항목으로 통합하세요.")
            seen.add(normalized)
    return dictionary


def tokenize(name_raw: str, dictionary: dict) -> tuple[str, list[dict]]:
    """Select longest nonoverlapping spans, then retain every unmatched character."""
    name = normalize_name(name_raw)
    candidates = []
    for entry in dictionary["entries"]:
        for alias in entry.get("aliases", [entry["canonical"]]):
            word = normalize_name(alias)
            start = name.find(word)
            while start != -1:
                candidates.append((start, start + len(word), entry))
                start = name.find(word, start + 1)
    for match in re.finditer(r"(?:제)?[0-9]+(?:차|단지|블록|동)?", name):
        candidates.append((match.start(), match.end(), {
            "canonical": match.group(), "category": "number",
        }))
    occupied = [False] * len(name)
    accepted = {}
    for start, end, entry in sorted(
        candidates, key=lambda item: (-(item[1] - item[0]), item[0], item[2]["canonical"])
    ):
        if any(occupied[start:end]):
            continue
        occupied[start:end] = [True] * (end - start)
        reasons = list(entry.get("review_reasons", []))
        provisional = entry.get("provisional", False)
        # A short brand or a one-character lexeme can occur inside another word.
        if (entry["category"] == "brand" and end - start <= 2) or (entry["category"] == "nature" and end - start == 1):
            if "short_substring_match" not in reasons:
                reasons.append("short_substring_match")
            provisional = True
        if provisional and not reasons:
            reasons.append("provisional_dictionary_entry")
        accepted[start] = {
            "surface": name[start:end], "canonical": entry["canonical"],
            "category": entry["category"], "start": start, "end": end,
            "provisional": provisional, "needs_review": bool(reasons),
            "review_reasons": reasons,
        }
        for key in ("corporate_group", "parent_brand", "classification_basis", "entity_scope"):
            if key in entry:
                accepted[start][key] = entry[key]
    tokens, position = [], 0
    while position < len(name):
        if position in accepted:
            token = accepted[position]
            position = token["end"]
        else:
            start = position
            while position < len(name) and position not in accepted:
                position += 1
            token = {
                "surface": name[start:position], "canonical": name[start:position],
                "category": "unclassified", "start": start, "end": position,
                "provisional": True, "needs_review": True,
                "review_reasons": ["unclassified_span"],
            }
        tokens.append(token)
    if "".join(token["surface"] for token in tokens) != name:
        raise RuntimeError("토큰 재구성 불변식이 깨졌습니다.")
    return name, tokens


def analyze(records: list[dict], dictionary: dict) -> tuple[list[dict], dict, list[dict]]:
    demo = any(record["is_demo"] for record in records)
    token_records, reviews = [], []
    groups = defaultdict(list)
    for record in records:
        name, tokens = tokenize(record["name_raw"], dictionary)
        result = {
            **record, "demo": demo, "name_normalized": name,
            "character_count": len(name), "token_count": len(tokens), "tokens": tokens,
            "needs_review": any(token["needs_review"] for token in tokens),
        }
        token_records.append(result)
        for token in tokens:
            if token["needs_review"]:
                reviews.append({
                    "complex_id": record["complex_id"], "name_raw": record["name_raw"],
                    "name_normalized": name, "is_demo": record["is_demo"], "demo": demo,
                    "source_url": record["source_url"], "token": token,
                })
        if record["completion_year"] is not None:
            decade = record["completion_year"] // 10 * 10
            groups[(decade, record["region"])].append(result)
    temporal = []
    for (decade, region), members in sorted(groups.items()):
        counts, categories = Counter(), Counter()
        provisional_keys = set()
        for member in members:
            # Residual name candidates are counted, but punctuation-only spans are not lexical tokens.
            known = [token for token in member["tokens"] if token["category"] != "unclassified" or any(character.isalnum() for character in token["canonical"])]
            counts.update({(token["category"], token["canonical"]) for token in known})
            categories.update({token["category"] for token in known})
            provisional_keys.update(
                (token["category"], token["canonical"]) for token in known if token["provisional"]
            )
        size = len(members)
        temporal.append({
            "decade_start": decade, "decade": f"{decade}년대", "region": region,
            "complex_count": size,
            "mean_name_character_count": sum(member["character_count"] for member in members) / size,
            "review_required_complex_count": sum(member["needs_review"] for member in members),
            "token_prevalence": [
                {"token": token, "category": category, "complex_count": count,
                 "share": count / size, "provisional": (category, token) in provisional_keys}
                for (category, token), count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
            ],
            "category_prevalence": [
                {"category": category, "complex_count": count, "share": count / size}
                for category, count in sorted(categories.items())
            ],
        })
    missing_years = sum(record["completion_year"] is None for record in records)
    warnings = list(WARNINGS)
    if demo:
        warnings.insert(0, "데모 행을 포함한 결과 전체입니다(demo=true). 실측 결과로 인용하거나 실자료 결과와 혼합하지 마세요.")
    if missing_years == len(records):
        warnings.append("모든 행의 준공연도가 없어 연대별 집계를 생성하지 않았습니다.")
    summary = {
        "schema_version": 1, "demo": demo,
        "analysis_kind": "current_names_by_completion_cohort",
        "dictionary_version": dictionary["version"],
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "normalization": "NFKC followed by removal of Unicode whitespace; original preserved",
        "span_coordinates": "Python Unicode code-point offsets in name_normalized; end exclusive",
        "temporal_basis": "completion_year",
        "provisional_tokens_included": True,
        "unclassified_spans_in_token_prevalence": True,
        "warnings": warnings,
        "totals": {
            "complex_count": len(records), "known_completion_year_count": len(records) - missing_years,
            "missing_completion_year_count": missing_years,
            "review_required_complex_count": sum(record["needs_review"] for record in token_records),
            "review_span_count": len(reviews),
            "demo_record_count": sum(record["is_demo"] for record in records),
        },
        "decade_region": temporal,
    }
    return token_records, summary, reviews


def write_outputs(output_dir: Path, token_records: list[dict], summary: dict, reviews: list[dict]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    outputs = {
        "tokens.jsonl": "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in token_records),
        "summary.json": json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        "review.jsonl": "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in reviews),
    }
    for filename, content in outputs.items():
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=output_dir, delete=False) as handle:
                temporary = Path(handle.name)
                handle.write(content)
            temporary.replace(output_dir / filename)
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="아파트 현재 이름의 준공연대·지역별 토큰 탐색 (stdlib)")
    parser.add_argument("--input", required=True, type=Path, help="현재명 단지 1행의 UTF-8 JSONL")
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--dictionary", type=Path, default=DEFAULT_DICTIONARY)
    parser.add_argument("--allow-demo", action="store_true", help="데모 허용; 혼합 입력도 결과 전체 demo=true")
    args = parser.parse_args(argv)
    try:
        records = load_records(args.input, allow_demo=args.allow_demo)
        dictionary = load_dictionary(args.dictionary)
        token_records, summary, reviews = analyze(records, dictionary)
        summary["input_file"] = str(args.input.resolve())
        summary["dictionary_file"] = str(args.dictionary.resolve())
        write_outputs(args.output_dir, token_records, summary, reviews)
    except (AnalysisError, OSError) as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({
        "output_dir": str(args.output_dir.resolve()), "demo": summary["demo"],
        "complex_count": summary["totals"]["complex_count"],
        "temporal_group_count": len(summary["decade_region"]),
        "review_span_count": len(reviews),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
