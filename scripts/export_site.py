#!/usr/bin/env python3
"""Build an auditable static site from the same records and tokenizer as the API."""

from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("atlas_server_export", ROOT / "backend" / "server.py")
SERVER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SERVER)
FEATURES = ["park", "water", "forest", "school", "metro"]
CORE_FIELDS = {
    "id", "name", "sido", "sigungu", "dong", "dong_code", "approval_year", "lat", "lon",
    "tokens", "name_normalized", "needs_review", "source_url", "is_demo",
}
PUBLIC_DETAIL_FIELDS = {
    "name_clean",
    "name_variants", "name_basis", "name_disagreement", "source_id", "pnu", "address", "road_address",
    "approval_date", "year_basis", "building_count", "household_count", "observed_at",
    "coordinate_basis", "coordinate_match_basis", "coordinate_match_method", "coordinate_observed_at",
    "coordinate_precision", "coordinate_source_id", "coordinate_source_url",
}
PUBLIC_DATASET_FIELDS = {
    "name", "status", "source_url", "download_url", "downloaded_at", "source_snapshot_date", "observed_at",
    "record_count", "coordinate_count", "year_min", "year_max", "coverage_note", "coordinate_coverage_note",
    "license", "name_selection", "coordinate_supplement",
}
PUBLIC_COORDINATE_FIELDS = {"source_url", "download_url", "downloaded_at", "license", "match_basis", "source_record_count", "access"}


def compact(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def write_json(path: Path, value) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = compact(value).encode("utf-8")
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(payload)
        temporary.replace(path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()
    return len(payload)


def pack_store(store) -> tuple[dict, dict[str, dict], dict]:
    categories = [entry["key"] for entry in SERVER.TAXONOMY]
    regions, tokens, reasons, sources = [], [], [], []
    region_index, token_index, reason_index, source_index = {}, {}, {}, {}
    packed_rows, detail_rows = [], defaultdict(dict)
    detail_fields = sorted({key for row in store.records for key in row if key in PUBLIC_DETAIL_FIELDS})
    fold_map = {}
    for row in store.records:
        if row.get("is_demo") is not False:
            raise ValueError("정적 배포에 데모 행을 포함할 수 없습니다.")
        region = tuple(row[key] for key in ("sido", "sigungu", "dong", "dong_code"))
        if region not in region_index:
            region_index[region] = len(regions)
            regions.append(region)
        source = row["source_url"]
        if source not in source_index:
            source_index[source] = len(sources)
            sources.append(source)
        runs = []
        for token in row["tokens"]:
            token_reasons = tuple(token.get("review_reasons", []))
            if token_reasons not in reason_index:
                reason_index[token_reasons] = len(reasons)
                reasons.append(token_reasons)
            extras = {key: value for key, value in token.items() if key not in {"surface", "canonical", "category", "start", "end", "provisional", "needs_review", "review_reasons"}}
            flags = int(bool(token["provisional"])) | (int(bool(token["needs_review"])) << 1)
            packed_token = [token["canonical"], categories.index(token["category"]), flags, reason_index[token_reasons], extras or None]
            identity = compact(packed_token)
            if identity not in token_index:
                token_index[identity] = len(tokens)
                tokens.append(packed_token)
            runs.extend((token_index[identity], token["end"] - token["start"]))
            for character in token["canonical"]:
                if character.casefold() != character:
                    fold_map[character] = character.casefold()
        coordinates = [row["lat"], row["lon"]] if SERVER.has_coordinates(row) else None
        packed_rows.append([
            row["id"], row["name"], region_index[region], row["approval_year"], coordinates,
            runs, row["name_normalized"] if row["name_normalized"] != row["name"] else None,
            source_index[source],
        ])
        for character in row["name_normalized"] + row["id"]:
            if character.casefold() != character:
                fold_map[character] = character.casefold()
        detail_values = []
        for field in detail_fields:
            value = row.get(field)
            if field == "name_variants" and isinstance(value, dict):
                value = {key: text for key, text in value.items() if key in {"단지명_공시가격", "단지명_건축물대장", "단지명_도로명주소"}}
            detail_values.append(value)
        detail_rows[row["sido"]][row["id"]] = detail_values
    detail_shards = {sido: f"./data/details/region-{index:02d}.json" for index, sido in enumerate(sorted(detail_rows))}
    meta = store.meta()
    meta["dataset"] = {key: value for key, value in meta["dataset"].items() if key in PUBLIC_DATASET_FIELDS}
    if isinstance(meta["dataset"].get("coordinate_supplement"), dict):
        meta["dataset"]["coordinate_supplement"] = {key: value for key, value in meta["dataset"]["coordinate_supplement"].items() if key in PUBLIC_COORDINATE_FIELDS}
    core = {
        "schema_version": 1, "meta": meta, "categories": categories,
        "regions": regions, "token_catalog": tokens, "review_reason_sets": reasons,
        "sources": sources, "records": packed_rows, "detail_fields": detail_fields,
        "detail_shards": detail_shards, "casefold_map": fold_map,
        "default_tokens": SERVER.DEFAULT_TOKENS, "warnings": SERVER.BASE_WARNINGS,
        "record_fields": ["id", "name", "region_index", "approval_year", "coordinates", "token_runs", "normalized_override", "source_index"],
        "token_fields": ["canonical", "category_index", "flags", "review_reason_index", "extra_metadata"],
        "flags": {"provisional": 1, "needs_review": 2},
        "token_runs": "Alternating catalog index and Unicode-code-point span length; spans are contiguous.",
    }
    index_by_id = {row["id"]: index for index, row in enumerate(store.records)}
    distance_rows = {}
    for row in store.distances:
        feature = row.get("feature")
        if feature not in FEATURES or str(row.get("id")) not in index_by_id:
            continue
        value = row.get("distance_m")
        if type(value) not in (int, float) or not SERVER.math.isfinite(value) or value < 0:
            continue
        index = index_by_id[str(row["id"])]
        distance_rows.setdefault(index, [None] * len(FEATURES))[FEATURES.index(feature)] = value
    distances = {"schema_version": 1, "features": FEATURES, "rows": [[index, *values] for index, values in sorted(distance_rows.items())]}
    shards = {detail_shards[sido]: {"fields": detail_fields, "rows": rows} for sido, rows in detail_rows.items()}
    return core, shards, distances


def copy_frontend(frontend: Path, output: Path) -> list[str]:
    copied = []
    allowed_suffixes = {"", ".html", ".css", ".js", ".mjs", ".json", ".png", ".jpg", ".jpeg", ".webp", ".svg", ".ico", ".woff", ".woff2", ".ttf"}
    for source in sorted(frontend.rglob("*")):
        relative = source.relative_to(frontend)
        if not source.is_file() or any(part.startswith(".") for part in relative.parts):
            continue
        if source.suffix.lower() not in allowed_suffixes or not source.resolve().is_relative_to(frontend.resolve()):
            continue
        target = output / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.suffix == ".html":
            # Project Pages lives below /repository/. Root-relative local assets would miss it.
            import re
            text = source.read_text(encoding="utf-8")
            text = re.sub(r"((?:src|href)=['\"])/(?!/)", r"\1./", text)
            target.write_text(text, encoding="utf-8")
        else:
            shutil.copy2(source, target)
        copied.append(str(relative))
    return copied


def export_site(output: Path, *, data_dir: Path | None = None, include_map_config=False, store=None, frontend: Path | None = None) -> dict:
    output = output.resolve()
    if output == ROOT or ROOT.is_relative_to(output) or output.is_relative_to(ROOT / "frontend") or output.is_relative_to(ROOT / "data"):
        raise ValueError("원본 프로젝트·frontend·data를 덮어쓰지 않는 별도 배포 디렉터리를 지정하세요.")
    if output.exists() and any(path.is_file() for path in output.rglob(".env*")):
        raise ValueError("배포 디렉터리에 환경설정 파일이 있습니다. 공개할 파일 범위를 먼저 정리하세요.")
    output.mkdir(parents=True, exist_ok=True)
    source_store = store if store is not None else SERVER.DataStore(data_dir)
    core, shards, distances = pack_store(source_store)
    asset_files = copy_frontend(frontend or ROOT / "frontend", output)
    sizes = {}
    sizes["data/core.json"] = write_json(output / "data" / "core.json", core)
    sizes["data/gis-distances.json"] = write_json(output / "data" / "gis-distances.json", distances)
    for relative, payload in shards.items():
        sizes[relative.removeprefix("./")] = write_json(output / relative.removeprefix("./"), payload)
    feature_paths = {}
    for feature in FEATURES:
        relative = f"data/gis/{feature}.geojson"
        feature_paths[feature] = "./" + relative
        collection = {"type": "FeatureCollection", "features": [
            {"type": "Feature", "geometry": entry.get("geometry"), "properties": {key: value for key, value in entry.get("properties", {}).items() if key in {"feature", "name", "source_url"}}}
            for entry in source_store.geo_features.get("features", []) if entry.get("properties", {}).get("feature") == feature
        ]}
        sizes[relative] = write_json(output / relative, collection)
    public_config = SERVER.map_config() if include_map_config else {"provider": "osm", "kakao_js_key": None, "fallback_provider": "osm"}
    write_json(output / "map-config.json", public_config)
    runtime = {
        "mode": "static", "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "data": {"core": "./data/core.json", "distances": "./data/gis-distances.json", "features": feature_paths, "map_config": "./map-config.json"},
        "gis_inference": "descriptive_only_no_permutation_p_value",
    }
    write_json(output / "runtime-config.json", runtime)
    (output / ".nojekyll").write_text("", encoding="utf-8")
    manifest = {
        "schema_version": 1, "generated_at": runtime["generated_at"],
        "record_count": len(source_store.records), "coordinate_count": source_store.meta()["dataset"]["coordinate_count"],
        "dictionary_version": source_store.dictionary["version"],
        "public_map_provider": public_config["provider"], "included_public_map_config": bool(include_map_config),
        "data_bytes": sizes, "frontend_files": asset_files,
        "sha256": {relative: hashlib.sha256((output / relative).read_bytes()).hexdigest() for relative in sorted(sizes)},
        "notes": ["Python backend is retained as the source of the export.", "No .env file is exported.", "Filtered GIS descriptive statistics are recomputed in the browser; permutation p-values are not exported or reused."],
    }
    write_json(output / "build-manifest.json", manifest)
    return {"output_dir": str(output), "record_count": len(source_store.records), "coordinate_count": manifest["coordinate_count"], "data_bytes": sum(sizes.values()), "core_bytes": sizes["data/core.json"], "public_map_provider": public_config["provider"]}


def main(argv=None):
    parser = argparse.ArgumentParser(description="GitHub Pages용 정적 분석 사이트 export")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "site")
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data" / "processed")
    parser.add_argument("--include-map-config", action="store_true", help="공개용 지도 JavaScript 키만 map-config.json에 포함")
    args = parser.parse_args(argv)
    try:
        result = export_site(args.output_dir, data_dir=args.data_dir, include_map_config=args.include_map_config)
    except (ValueError, OSError) as exc:
        print(f"정적 사이트를 만들지 못했습니다: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
