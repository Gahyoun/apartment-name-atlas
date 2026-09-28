#!/usr/bin/env python3
"""Local, dependency-free apartment-name API and frontend server."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import importlib.util
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import math
import mimetypes
import os
from pathlib import Path
import re
import sys
import threading
from urllib.parse import parse_qs, unquote, urlsplit


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from analyze import load_dictionary, normalize_name, tokenize  # noqa: E402

TAXONOMY = [
    {"key": "company", "label": "기업·그룹"}, {"key": "brand", "label": "브랜드"},
    {"key": "place", "label": "지명"}, {"key": "nature", "label": "자연"},
    {"key": "premium", "label": "고급·기타 이미지"},
    {"key": "building_type", "label": "건물 유형"}, {"key": "number", "label": "숫자·차수"},
    {"key": "unclassified", "label": "개별 명칭·미분류"},
]
DEFAULT_TOKENS = ["그린", "리버", "파크", "롯데캐슬", "카이저"]
MAX_LIMIT = 5000
BASE_WARNINGS = [
    "사용승인연도별 현재 명칭을 비교합니다. 실제 명명 시점이나 개명 역사가 아닙니다.",
    "준공연도가 없는 단지는 연도별 분모에서 제외합니다.",
    "짧은 부분문자열·주소 기반 지명 등 잠정 분류도 집계에 포함됩니다. 분절의 검토 필요 표시를 확인하세요.",
    "누적 수는 선택한 연도 범위 안에서만 누적됩니다. 토큰 선택은 모집단을 줄이지 않습니다.",
]


def map_config(env_file: Path | None = None, environ=None) -> dict:
    """Expose only the intentionally public JavaScript map key, never other settings."""
    environment = os.environ if environ is None else environ
    name = "KAKAO_MAP_JS_KEY"
    key = environment.get(name)
    if key is None:
        path = env_file if env_file is not None else ROOT / ".env.local"
        try:
            with path.open(encoding="utf-8") as handle:
                for line in handle:
                    field, separator, value = line.strip().partition("=")
                    if separator and field.strip() == name:
                        key = value.strip()
                        if len(key) >= 2 and key[0] == key[-1] and key[0] in {"\"", "'"}:
                            key = key[1:-1]
                        break
        except OSError:
            pass
    key = key.strip() if isinstance(key, str) else ""
    return {"provider": "kakao" if key else "osm", "kakao_js_key": key or None, "fallback_provider": "osm"}


class RequestError(ValueError):
    pass


def first(params: dict, key: str, default: str = "") -> str:
    value = params.get(key, default)
    return value[0] if isinstance(value, list) and value else value


def integer_param(params: dict, key: str, default=None, minimum=0, maximum=2100):
    value = first(params, key)
    if value in (None, ""):
        return default
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]+", value):
        raise RequestError(f"{key}는 정수여야 합니다.")
    number = int(value)
    if not minimum <= number <= maximum:
        raise RequestError(f"{key}는 {minimum}~{maximum} 범위여야 합니다.")
    return number


def parse_filters(params: dict) -> dict:
    values = {key: first(params, key).strip() for key in ("sido", "sigungu", "dong")}
    values["year_from"] = integer_param(params, "year_from", minimum=1800)
    values["year_to"] = integer_param(params, "year_to", minimum=1800)
    if values["year_from"] is not None and values["year_to"] is not None and values["year_from"] > values["year_to"]:
        raise RequestError("year_from은 year_to보다 클 수 없습니다.")
    return values


def filter_records(records: list[dict], filters: dict, *, include_years: bool = True) -> list[dict]:
    selected = []
    for record in records:
        if any(filters.get(key) and record.get(key) != filters[key] for key in ("sido", "sigungu", "dong")):
            continue
        year = record.get("approval_year")
        if include_years:
            low, high = filters.get("year_from"), filters.get("year_to")
            if (low is not None or high is not None) and year is None:
                continue
            if low is not None and year < low:
                continue
            if high is not None and year > high:
                continue
        selected.append(record)
    return selected


def local_place_entries(record: dict) -> list[dict]:
    """Address context supplies candidates, never validated semantic truth."""
    candidates = set()
    for field in ("sido", "sigungu", "dong"):
        for word in str(record.get(field) or "").split():
            candidates.add(normalize_name(word))
            stem = re.sub(r"(?:특별자치시|특별자치도|광역시|특별시|시|군|구|읍|면|동|리)$", "", word)
            candidates.add(normalize_name(stem))
            candidates.add(normalize_name(re.sub(r"[0-9]+(?:가)?$", "", stem)))
    return [
        {"canonical": candidate, "category": "place", "provisional": True,
         "review_reasons": ["address_name_match_requires_review"]}
        for candidate in sorted(candidates, key=lambda value: (-len(value), value)) if len(candidate) >= 2
    ]


def tokenize_with_address(record: dict, dictionary: dict) -> tuple[str, list[dict]]:
    normalized, original_tokens = tokenize(record["name"], dictionary)
    entries = local_place_entries(record)
    if not entries:
        for token in original_tokens:
            if token["category"] == "place":
                token["address_match"] = None
        return normalized, original_tokens
    result = []
    for token in original_tokens:
        # Existing brand matches remain intact even if address text overlaps them.
        if token["category"] != "unclassified":
            result.append(token)
            continue
        # Only a prefix is evidence: "세종로" in 종로구 must not become "세 | 종로".
        # The entries are already sorted longest-first, and existing brands are protected above.
        prefix_entry = next((entry for entry in entries if token["surface"].startswith(entry["canonical"])), None)
        if prefix_entry is None:
            result.append(token)
            continue
        prefix = prefix_entry["canonical"]
        _, subtokens = tokenize(prefix, {"version": "address-context", "entries": [prefix_entry]})
        for subtoken in subtokens:
            subtoken["start"] += token["start"]
            subtoken["end"] += token["start"]
            result.append(subtoken)
        if len(prefix) < len(token["surface"]):
            result.append({
                **token, "surface": token["surface"][len(prefix):],
                "canonical": token["surface"][len(prefix):], "start": token["start"] + len(prefix),
            })
    address_words = {entry["canonical"] for entry in entries}
    for token in result:
        if token["category"] == "place":
            token["address_match"] = token["canonical"] in address_words
    return normalized, result


def has_coordinates(record: dict) -> bool:
    lat, lon = record.get("lat"), record.get("lon")
    return (
        type(lat) in (int, float) and type(lon) in (int, float)
        and math.isfinite(lat) and math.isfinite(lon)
        and -90 <= lat <= 90 and -180 <= lon <= 180 and (lat != 0 or lon != 0)
    )


def countable_token(token: dict) -> bool:
    return token["category"] != "unclassified" or any(character.isalnum() for character in token["canonical"])


def prepare_records(records: list[dict], dictionary: dict, metadata: dict) -> list[dict]:
    prepared, seen = [], set()
    for line, raw in enumerate(records, 1):
        if not isinstance(raw, dict):
            raise ValueError(f"단지 자료 {line}행은 객체여야 합니다.")
        if raw.get("is_demo") is not False:
            raise ValueError(f"단지 자료 {line}행: 실자료 서버에는 is_demo=false가 명시된 행만 허용합니다.")
        identity = raw.get("id", "").strip() if isinstance(raw.get("id"), str) else ""
        if not identity or identity in seen:
            raise ValueError(f"단지 자료 {line}행: 비어 있거나 중복된 id={identity!r}. 단지 1행으로 정리하세요.")
        seen.add(identity)
        if not isinstance(raw.get("name"), str) or not raw["name"].strip():
            raise ValueError(f"단지 자료 {line}행: name이 필요합니다.")
        source_url = raw.get("source_url") or metadata.get("source_url")
        if not isinstance(source_url, str) or urlsplit(source_url).scheme not in {"http", "https"} or not urlsplit(source_url).hostname:
            raise ValueError(f"단지 자료 {line}행: 실자료 source_url이 필요합니다.")
        year = raw.get("approval_year")
        if year is not None and (type(year) is not int or not 1800 <= year <= 2100):
            raise ValueError(f"단지 자료 {line}행: approval_year는 1800~2100 정수 또는 null이어야 합니다.")
        record = {**raw, "id": identity, "source_url": source_url, "approval_year": year}
        for key in ("sido", "sigungu", "dong", "dong_code"):
            record[key] = str(raw.get(key) or "").strip()
        for key in ("lat", "lon"):
            value = raw.get(key)
            try:
                record[key] = float(value) if value is not None and not isinstance(value, bool) else None
            except (TypeError, ValueError):
                record[key] = None
        if not has_coordinates(record):
            record["lat"] = record["lon"] = None
        record["name_normalized"], record["tokens"] = tokenize_with_address(record, dictionary)
        record["_token_keys"] = frozenset(
            token["canonical"] for token in record["tokens"] if countable_token(token)
        )
        record["needs_review"] = any(token["needs_review"] for token in record["tokens"])
        prepared.append(record)
    return prepared


def public_record(record: dict) -> dict:
    return {key: value for key, value in record.items() if not key.startswith("_")}


class DataStore:
    def __init__(self, data_dir: Path | None = None, *, records=None, metadata=None, distances=None):
        self.data_dir = data_dir or ROOT / "data" / "processed"
        self.lock = threading.RLock()
        self.signature = None
        self.static = records is not None
        self.dictionary = load_dictionary()
        self.metadata = metadata or {}
        self.records = prepare_records(records or [], self.dictionary, self.metadata)
        self.distances = distances if distances is not None else []
        self.geo_features = {"type": "FeatureCollection", "features": []}
        if not self.static:
            self.ensure_loaded()

    def ensure_loaded(self):
        if self.static:
            return
        paths = [self.data_dir / name for name in ("complexes.jsonl", "dataset_meta.json", "gis_distances.jsonl")]
        paths.append(ROOT / "dictionaries" / "tokens.v1.json")
        paths.append(self.data_dir / "gis_features.geojson")
        signature = tuple((path.stat().st_mtime_ns, path.stat().st_size) if path.exists() else None for path in paths)
        if signature == self.signature:
            return
        with self.lock:
            if signature == self.signature:
                return
            metadata = json.loads(paths[1].read_text(encoding="utf-8")) if paths[1].exists() else {}
            if "dataset" in metadata and isinstance(metadata["dataset"], dict):
                metadata = metadata["dataset"]
            rows = self.read_jsonl(paths[0]) if paths[0].exists() else []
            dictionary = load_dictionary()
            records = prepare_records(rows, dictionary, metadata)
            distances = self.read_jsonl(paths[2]) if paths[2].exists() else []
            geo_features = json.loads(paths[4].read_text(encoding="utf-8")) if paths[4].exists() else {"type": "FeatureCollection", "features": []}
            self.records, self.metadata, self.distances, self.dictionary = records, metadata, distances, dictionary
            self.geo_features = geo_features
            self.signature = signature

    @staticmethod
    def read_jsonl(path: Path) -> list[dict]:
        rows = []
        with path.open(encoding="utf-8-sig") as handle:
            for index, line in enumerate(handle, 1):
                if line.strip():
                    try:
                        rows.append(json.loads(line))
                    except json.JSONDecodeError as exc:
                        raise ValueError(f"{path}:{index}: JSON 구문 오류") from exc
        return rows

    def meta(self) -> dict:
        years = [row["approval_year"] for row in self.records if row["approval_year"] is not None]
        coordinate_count = sum(has_coordinates(row) for row in self.records)
        status = "real" if self.records else "empty"
        dataset = {
            **self.metadata,
            "name": self.metadata.get("name", "전국 아파트 현재명 자료"), "status": status,
            "source_url": self.metadata.get("source_url"),
            "downloaded_at": self.metadata.get("downloaded_at"),
            "record_count": len(self.records), "coordinate_count": coordinate_count,
            "year_min": min(years) if years else None, "year_max": max(years) if years else None,
            "coverage_note": self.metadata.get("coverage_note", "실자료를 아직 연결하지 않았습니다." if not self.records else "현재 연결된 자료원의 수록 단지입니다."),
        }
        return {
            "dataset": dataset, "taxonomy": TAXONOMY, "dictionary_version": self.dictionary["version"],
            "geo_status": "ready" if self.distances else ("coordinates_only" if coordinate_count else "missing_coordinates"),
            "geo_feature_count": len(self.geo_features.get("features", [])),
            "demo": False,
        }

    def regions(self, params: dict) -> dict:
        level = first(params, "level", "sido")
        if level not in {"sido", "sigungu", "dong"}:
            raise RequestError("level은 sido, sigungu, dong 중 하나여야 합니다.")
        filters = {"sido": "", "sigungu": "", "dong": ""}
        if level != "sido":
            filters["sido"] = first(params, "sido").strip()
        if level == "dong":
            filters["sigungu"] = first(params, "sigungu").strip()
        counts = Counter(row[level] for row in filter_records(self.records, filters) if row[level])
        return {"level": level, "options": [{"value": value, "label": value, "count": count} for value, count in sorted(counts.items())]}

    def analysis(self, params: dict) -> dict:
        filters = parse_filters(params)
        regional = filter_records(self.records, filters, include_years=False)
        selected = filter_records(regional, filters)
        raw_tokens = first(params, "tokens", ",".join(DEFAULT_TOKENS))
        selected_tokens = list(dict.fromkeys(normalize_name(value) for value in raw_tokens.split(",") if normalize_name(value)))
        if len(selected_tokens) > 30:
            raise RequestError("한 번에 비교할 토큰은 30개 이하로 선택하세요.")
        by_year = defaultdict(list)
        known_years = []
        top_counts, category_for = Counter(), {}
        provisional_tokens = set()
        for row in selected:
            if row["approval_year"] is not None:
                known_years.append(row["approval_year"])
                by_year[row["approval_year"]].append(row)
            seen_tokens = set()
            for token in row["tokens"]:
                if not countable_token(token):
                    continue
                key = token["canonical"]
                category_for[key] = token["category"]
                seen_tokens.add(key)
                if token["provisional"]:
                    provisional_tokens.add(key)
            top_counts.update(seen_tokens)
        series = []
        cumulative_total = 0
        cumulative_tokens = Counter()
        if known_years:
            low = filters["year_from"] if filters["year_from"] is not None else min(known_years)
            high = filters["year_to"] if filters["year_to"] is not None else max(known_years)
            for year in range(low, high + 1):
                members = by_year[year]
                total = len(members)
                counts = {token: sum(token in row["_token_keys"] for row in members) for token in selected_tokens}
                cumulative_total += total
                cumulative_tokens.update(counts)
                series.append({
                    "year": year, "total_count": total, "cumulative_total": cumulative_total,
                    "token_counts": counts,
                    "token_cumulative": {token: cumulative_tokens[token] for token in selected_tokens},
                    "token_shares": {token: counts[token] / total if total else None for token in selected_tokens},
                })
        warnings = list(BASE_WARNINGS)
        missing = sum(row["approval_year"] is None for row in regional)
        if missing:
            warnings.append(f"지역 표본 중 사용승인연도 미상 {missing:,}개가 있습니다. 연도 범위를 지정하면 이 행은 현재 표본에서도 제외됩니다.")
        if not self.records:
            warnings.append("실자료가 연결되지 않아 결과가 비어 있습니다. 데모 수치로 대체하지 않습니다.")
        elif not selected:
            warnings.append("현재 필터에 해당하는 단지가 없습니다.")
        elif not known_years:
            warnings.append("현재 표본에 유효한 사용승인연도가 없어 시계열을 만들지 않았습니다.")
        denominator = len(selected)
        category = first(params, "category").strip()
        if category and category not in {entry["key"] for entry in TAXONOMY}:
            raise RequestError("지원하지 않는 토큰 category입니다.")
        token_query = normalize_name(first(params, "token_q")).casefold()
        ranking = [
            {"token": token, "category": category_for[token], "count": count,
             "share": count / denominator if denominator else None, "provisional": token in provisional_tokens}
            for token, count in sorted(top_counts.items(), key=lambda item: (-item[1], item[0]))
            if (not category or category_for[token] == category) and (not token_query or token_query in token.casefold())
        ]
        return {
            "sample_count": denominator, "total_count": len(regional), "dataset_count": len(self.records),
            "valid_year_count": len(known_years), "mapped_count": sum(has_coordinates(row) for row in selected),
            "brand_count": sum(any(token["category"] == "brand" for token in row["tokens"]) for row in selected),
            "unique_token_count": len(top_counts),
            "place_name_count": sum(any(token["category"] == "place" for token in row["tokens"]) for row in selected),
            "place_local_match_count": sum(any(token["category"] == "place" and token.get("address_match") is True for token in row["tokens"]) for row in selected),
            "year_min": min(known_years) if known_years else None,
            "year_max": max(known_years) if known_years else None,
            "selected_tokens": selected_tokens, "series": series,
            "top_tokens": ranking[:100], "ranked_token_count": len(ranking),
            "warnings": warnings, "demo": False,
        }

    def tokens(self, params: dict) -> dict:
        result = self.analysis({**params, "tokens": ""})
        return {
            "total": result["ranked_token_count"], "items": result["top_tokens"],
            "sample_count": result["sample_count"], "truncated": result["ranked_token_count"] > len(result["top_tokens"]),
            "demo": False,
        }

    def complexes(self, params: dict) -> dict:
        selected = filter_records(self.records, parse_filters(params))
        mapped_only = first(params, "mapped_only", "0")
        if mapped_only not in {"", "0", "1"}:
            raise RequestError("mapped_only는 0 또는 1이어야 합니다.")
        if mapped_only == "1":
            selected = [row for row in selected if has_coordinates(row)]
        query = normalize_name(first(params, "q")).casefold()
        if query:
            selected = [row for row in selected if query in row["name_normalized"].casefold() or query in row["id"].casefold()]
        limit = integer_param(params, "limit", default=100, minimum=1, maximum=MAX_LIMIT)
        offset = integer_param(params, "offset", default=0, maximum=10_000_000)
        items = selected[offset:offset + limit]
        return {
            "total": len(selected), "items": [public_record(row) for row in items],
            "limit": limit, "offset": offset, "truncated": offset + len(items) < len(selected),
            "next_offset": offset + len(items) if offset + len(items) < len(selected) else None,
            "demo": False,
        }

    def map_geojson(self, params: dict) -> dict:
        selected = filter_records(self.records, parse_filters(params))
        mapped = [row for row in selected if has_coordinates(row)]
        limit = integer_param(params, "limit", default=2000, minimum=1, maximum=MAX_LIMIT)
        offset = integer_param(params, "offset", default=0, maximum=10_000_000)
        page = mapped[offset:offset + limit]
        return {
            "type": "FeatureCollection",
            "features": [
                {"type": "Feature", "geometry": {"type": "Point", "coordinates": [row["lon"], row["lat"]]},
                 "properties": {key: row.get(key) for key in ("id", "name", "sido", "sigungu", "dong", "approval_year")}}
                for row in page
            ],
            "sample_count": len(selected), "mapped_count": len(mapped),
            "limit": limit, "offset": offset, "truncated": offset + len(page) < len(mapped),
            "next_offset": offset + len(page) if offset + len(page) < len(mapped) else None,
            "demo": False,
        }

    def gis(self, params: dict) -> dict:
        selected = filter_records(self.records, parse_filters(params))
        token = normalize_name(first(params, "token", "파크"))
        feature = first(params, "feature", "park").strip()
        if feature not in {"park", "water", "forest", "school", "metro"}:
            raise RequestError("지원하지 않는 입지 유형입니다.")
        if not token:
            raise RequestError("GIS 비교할 token을 지정하세요.")
        threshold = integer_param(params, "threshold_m", default=500, minimum=50, maximum=10000)
        module_path = ROOT / "backend" / "gis.py"
        if not module_path.exists():
            return {
                "status": "unavailable", "sample_count": len(selected), "token": token,
                "feature": feature, "threshold_m": threshold,
                "warnings": ["GIS 거리 계산 모듈을 아직 연결하지 않았습니다. 미측정을 0m나 비인접으로 처리하지 않습니다."],
                "demo": False,
            }
        spec = importlib.util.spec_from_file_location("apartment_gis", module_path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        try:
            result = module.compute_gis(selected, self.distances, token, feature, threshold)
        except ValueError as exc:
            raise RequestError(str(exc)) from exc
        result["demo"] = False
        return result

    def gis_features(self, params: dict) -> dict:
        feature = first(params, "feature", "all").strip()
        if feature not in {"all", "park", "water", "forest", "school", "metro"}:
            raise RequestError("지원하지 않는 입지 유형입니다.")
        features = self.geo_features.get("features", [])
        if feature != "all":
            features = [item for item in features if item.get("properties", {}).get("feature") == feature]
        return {
            **{key: value for key, value in self.geo_features.items() if key != "features"},
            "type": "FeatureCollection", "features": features, "feature": feature,
            "feature_count": len(features), "demo": False,
        }


class Handler(SimpleHTTPRequestHandler):
    store: DataStore
    frontend_root = ROOT / "frontend"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(self.frontend_root), **kwargs)

    def send_json(self, data, status=200):
        payload = json.dumps(data, ensure_ascii=False, allow_nan=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(payload)

    def do_GET(self):
        parsed = urlsplit(self.path)
        if parsed.path.startswith("/api/"):
            try:
                params = parse_qs(parsed.query, keep_blank_values=True, max_num_fields=100)
                self.store.ensure_loaded()
                routes = {
                    "/api/map-config": lambda: map_config(),
                    "/api/meta": lambda: self.store.meta(),
                    "/api/regions": lambda: self.store.regions(params),
                    "/api/analysis": lambda: self.store.analysis(params),
                    "/api/tokens": lambda: self.store.tokens(params),
                    "/api/complexes": lambda: self.store.complexes(params),
                    "/api/map.geojson": lambda: self.store.map_geojson(params),
                    "/api/gis": lambda: self.store.gis(params),
                    "/api/gis-features": lambda: self.store.gis_features(params),
                }
                if parsed.path not in routes:
                    self.send_json({"error": "알 수 없는 API 경로입니다."}, 404)
                    return
                self.send_json(routes[parsed.path]())
            except RequestError as exc:
                self.send_json({"error": str(exc)}, 400)
            except (ValueError, OSError) as exc:
                self.send_json({"error": f"자료를 읽거나 처리하지 못했습니다: {exc}"}, 503)
            except Exception as exc:
                self.log_error("API exception: %s", exc)
                self.send_json({"error": "API 처리 중 오류가 발생했습니다. 서버 로그를 확인하세요."}, 500)
            return
        self.serve_static(parsed.path)

    def do_HEAD(self):
        self.do_GET()

    def serve_static(self, url_path: str):
        decoded = unquote(url_path)
        if "\x00" in decoded or "\\" in decoded or ".." in Path(decoded).parts:
            self.send_error(403, "Invalid path")
            return
        if decoded.startswith("/vendor/"):
            base = ROOT / "vendor"
            if not base.exists():
                base = self.frontend_root / "vendor"
            relative = decoded[len("/vendor/"):]
        else:
            base = self.frontend_root
            relative = decoded.lstrip("/") or "index.html"
        target = (base / relative).resolve()
        if not target.is_relative_to(base.resolve()):
            self.send_error(403, "Invalid path")
            return
        if target.is_dir():
            target = (target / "index.html").resolve()
            if not target.is_relative_to(base.resolve()):
                self.send_error(403, "Invalid path")
                return
        if not target.is_file():
            self.send_error(404, "File not found")
            return
        try:
            payload = target.read_bytes()
        except OSError:
            self.send_error(404, "File not found")
            return
        mime = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", mime + ("; charset=utf-8" if mime.startswith("text/") or mime in {"application/javascript", "application/json"} else ""))
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(payload)


def main(argv=None):
    parser = argparse.ArgumentParser(description="아파트 이름 연대기 로컬 웹 플랫폼")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data" / "processed")
    args = parser.parse_args(argv)
    try:
        Handler.store = DataStore(args.data_dir)
        server = ThreadingHTTPServer((args.host, args.port), Handler)
    except (ValueError, OSError) as exc:
        print(f"서버를 시작하지 못했습니다: {exc}", file=sys.stderr)
        return 2
    print(f"아파트 이름 연대기: http://{args.host}:{server.server_port} — 실자료 {len(Handler.store.records):,}개", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
