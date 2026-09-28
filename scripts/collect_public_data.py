#!/usr/bin/env python3
"""Download and normalize the official REB apartment-complex CSV.

Uses Python's standard library and the system curl (TLS certificate validation
remains enabled). No account or API key is needed for this public file.
"""
from __future__ import annotations

import argparse
import collections
import csv
import datetime as dt
import hashlib
import io
import json
from pathlib import Path
import re
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
SOURCE_URL = "https://www.data.go.kr/data/15106861/fileData.do"
SNAPSHOT_URL = "https://www.data.go.kr/cmm/cmm/fileDownload.do?atchFileId=FILE_000000007644006&fileDetailSn=1&insertDataPrcus=N"
DEFAULT_RAW = ROOT / "data/raw/reb_complex_basic_20260831.csv"
SEOUL_RAW = ROOT / "data/raw/seoul_openaptinfo_20260928.csv"
SEOUL_SOURCE_URL = "https://data.seoul.go.kr/dataList/OA-15818/A/1/datasetView.do"
SEOUL_EXPORT_URL = "https://datafile.seoul.go.kr/bigfile/iot/sheet/csv/download.do"
NAME_FIELDS = ["단지명_공시가격", "단지명_건축물대장", "단지명_도로명주소"]
SIDO_ALIASES = {
    "서울": "서울특별시", "부산": "부산광역시", "대구": "대구광역시",
    "인천": "인천광역시", "광주": "광주광역시", "대전": "대전광역시",
    "울산": "울산광역시", "세종": "세종특별자치시", "경기": "경기도",
    "강원": "강원특별자치도", "강원도": "강원특별자치도",
    "충북": "충청북도", "충남": "충청남도", "전북": "전북특별자치도",
    "전라북도": "전북특별자치도", "전남": "전라남도",
    "경북": "경상북도", "경남": "경상남도", "제주": "제주특별자치도",
}


def download(url: str, destination: Path, post_fields: dict | None = None) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as f:
        partial = Path(f.name)
    try:
        command = [
            "curl", "--location", "--fail", "--silent", "--show-error",
            "--connect-timeout", "20", "--max-time", "180",
            "--retry", "2", url, "--output", str(partial),
        ]
        for key, value in (post_fields or {}).items():
            command.extend(["--data-urlencode", f"{key}={value}"])
        subprocess.run(command, check=True)
        partial.replace(destination)
    finally:
        partial.unlink(missing_ok=True)


def read_csv(path: Path) -> tuple[list[dict], str, bytes]:
    payload = path.read_bytes()
    for encoding in ("utf-8-sig", "cp949"):
        try:
            text = payload.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise ValueError("The CSV is neither UTF-8 nor CP949")
    rows = list(csv.DictReader(io.StringIO(text)))
    required = {"단지고유번호", "필지고유번호", "주소", "단지종류", "사용승인일", *NAME_FIELDS}
    if not rows or not required.issubset(rows[0]):
        raise ValueError("Downloaded content does not match the expected REB CSV schema")
    return rows, encoding, payload


def parse_address(address: str) -> tuple[str, str, str]:
    parts = address.split()
    if not parts:
        return "미상", "미상", "미상"
    sido = SIDO_ALIASES.get(parts[0], parts[0])
    remainder = parts[1:]
    administrative = []
    while remainder and remainder[0].endswith(("시", "군", "구")):
        administrative.append(remainder.pop(0))
    sigungu = " ".join(administrative) or ("세종특별자치시" if sido == "세종특별자치시" else "미상")
    # Keep legal dong / eup / myeon; '리' is a subarea, not a replacement.
    dong = remainder[0] if remainder and not re.match(r"^(?:산\s*)?\d", remainder[0]) else "미상"
    return sido, sigungu, dong


def normalize(rows: list[dict], observed_at: str) -> tuple[list[dict], dict]:
    current_year = dt.date.fromisoformat(observed_at).year
    records = []
    seen = {}
    exact_duplicates = []
    conflicts = []
    skipped = collections.Counter()
    disagreements = 0
    year_errors = []
    for raw in rows:
        row = {k: (v or "").strip() for k, v in raw.items()}
        if row["단지종류"] != "1":
            skipped["non_apartment"] += 1
            continue
        original_id = row["단지고유번호"]
        if not original_id:
            raise ValueError("Apartment record is missing its original complex ID")
        if original_id in seen:
            if seen[original_id] == row:
                exact_duplicates.append(original_id)
                continue
            conflicts.append(original_id)
            continue
        seen[original_id] = row
        variants = {key: row[key] for key in NAME_FIELDS if row[key]}
        if not variants:
            skipped["missing_name"] += 1
            continue
        name_basis, name = next(iter(variants.items()))
        disagreement = len(set(variants.values())) > 1
        disagreements += disagreement
        approval_date = row["사용승인일"]
        year = None
        try:
            date = dt.date.fromisoformat(approval_date)
            if date.year < 1800 or date.year > current_year:
                raise ValueError("Year outside allowed interval")
            year = date.year
        except ValueError:
            year_errors.append({"source_id": original_id, "value": approval_date})
        sido, sigungu, dong = parse_address(row["주소"])
        pnu = row["필지고유번호"]
        records.append({
            "id": "reb:" + original_id,
            "source_id": original_id,
            "name": name,
            "name_basis": name_basis,
            "name_variants": variants,
            "name_disagreement": disagreement,
            "sido": sido, "sigungu": sigungu, "dong": dong,
            "dong_code": pnu[:10] if re.fullmatch(r"\d{19}", pnu) else None,
            "pnu": pnu,
            "address": row["주소"], "road_address": row.get("도로명주소", ""),
            "approval_date": approval_date, "approval_year": year,
            "year_basis": "사용승인일",
            "building_count": int(row["동수"]) if row["동수"].isdigit() else None,
            "household_count": int(row["세대수"]) if row["세대수"].isdigit() else None,
            "lat": None, "lon": None,
            "source_url": SOURCE_URL, "observed_at": observed_at, "is_demo": False,
        })
    if conflicts:
        raise ValueError("Conflicting duplicate complex IDs: " + ", ".join(conflicts[:20]))
    records.sort(key=lambda x: x["id"])
    audit = {
        "source_row_count": len(rows), "apartment_row_count": len(seen),
        "accepted_record_count": len(records), "skipped": dict(skipped),
        "exact_duplicate_count": len(exact_duplicates), "conflict_duplicate_count": len(conflicts),
        "name_disagreement_count": disagreements,
        "name_basis_counts": dict(collections.Counter(r["name_basis"] for r in records)),
        "missing_name_by_source": {k: sum(not r[k].strip() for r in rows if r["단지종류"].strip() == "1") for k in NAME_FIELDS},
        "invalid_approval_dates": year_errors,
        "sido_counts": dict(sorted(collections.Counter(r["sido"] for r in records).items())),
        "sigungu_count": len({(r["sido"], r["sigungu"]) for r in records}),
        "dong_count": len({r["dong_code"] for r in records if r["dong_code"]}),
        "missing_dong_code_count": sum(r["dong_code"] is None for r in records),
        "unknown_address_components_count": sum("미상" in (r["sido"], r["sigungu"], r["dong"]) for r in records),
    }
    return records, audit


def augment_seoul_coordinates(records: list[dict], path: Path) -> dict:
    """One-to-one exact road-address + legal-dong + approval-date linkage.

    A failed match leaves coordinates null. No fuzzy name match, geocoding or
    district-centroid substitution is performed.
    """
    payload = path.read_bytes()
    for encoding in ("utf-8-sig", "cp949"):
        try:
            text = payload.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise ValueError("Unknown Seoul CSV encoding")
    source = [{k: (v or "").strip() for k, v in r.items()} for r in csv.DictReader(io.StringIO(text))]
    required = {"k-아파트코드", "k-아파트명", "kapt도로명주소", "주소(읍면동)", "좌표X", "좌표Y", "k-사용검사일-사용승인일"}
    if not source or not required.issubset(source[0]):
        raise ValueError("Unexpected Seoul OpenAptInfo CSV schema")
    if len({r["k-아파트코드"] for r in source}) != len(source):
        raise ValueError("Duplicate Seoul apartment IDs require manual resolution")

    def road_key(value):
        value = re.sub(r"\s+", " ", value.strip())
        return re.sub(r"^서울(?:특별시)?\s+", "", value)

    reb_index = collections.defaultdict(list)
    seoul_index = collections.defaultdict(list)
    for r in records:
        if r["sido"] == "서울특별시" and r["road_address"]:
            reb_index[road_key(r["sigungu"] + " " + r["road_address"])].append(r)
    for r in source:
        if r["kapt도로명주소"]:
            seoul_index[road_key(r["kapt도로명주소"])].append(r)
    counts = collections.Counter()
    matches = []
    for key, candidates in reb_index.items():
        supplement = seoul_index.get(key, [])
        if len(candidates) != 1 or len(supplement) != 1:
            continue
        counts["unique_road_address_pairs"] += 1
        record, coord = candidates[0], supplement[0]
        if record["dong"] != coord["주소(읍면동)"]:
            counts["dong_mismatch"] += 1
            continue
        if record["approval_date"] != coord["k-사용검사일-사용승인일"][:10]:
            counts["approval_date_mismatch"] += 1
            continue
        try:
            lon, lat = float(coord["좌표X"]), float(coord["좌표Y"])
            if not (126.7 <= lon <= 127.3 and 37.4 <= lat <= 37.8):
                raise ValueError("Outside Seoul coordinate bounds")
        except ValueError:
            counts["invalid_coordinates"] += 1
            continue
        record.update({
            "lat": lat, "lon": lon,
            "coordinate_source_url": SEOUL_SOURCE_URL,
            "coordinate_source_id": coord["k-아파트코드"],
            "coordinate_match_basis": "unique_exact_road_address+legal_dong+approval_date",
            "coordinate_match_method": "unique_exact_road_address+legal_dong+approval_date",
            "coordinate_basis": "서울 OpenAptInfo 좌표X=경도, 좌표Y=위도; 값의 범위 점검, 측량 정확도 미확인",
            "coordinate_observed_at": "2026-09-28",
            "coordinate_precision": "official_complex_point; accuracy not independently surveyed",
        })
        counts["matched_records"] += 1
        matches.append({
            "reb_id": record["id"], "seoul_id": coord["k-아파트코드"],
            "reb_name": record["name"], "seoul_name": coord["k-아파트명"],
            "road_address_key": key, "legal_dong": record["dong"],
            "approval_date": record["approval_date"], "lon": lon, "lat": lat,
        })
    (ROOT / "data/raw/seoul_coordinate_matches.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in matches), encoding="utf-8")
    return {
        "source_url": SEOUL_SOURCE_URL, "download_url": SEOUL_EXPORT_URL,
        "access": "Official public Sheet CSV export; no API key, no sample API pagination",
        "raw_file": str(path.relative_to(ROOT)), "raw_encoding": encoding,
        "raw_sha256": hashlib.sha256(payload).hexdigest(),
        "source_record_count": len(source), "raw_bytes": len(payload),
        "downloaded_at": dt.datetime.fromtimestamp(path.stat().st_mtime, dt.timezone.utc).isoformat(),
        "counts": dict(counts),
        "match_basis": "Both sources must have exactly one record for the same road address, identical legal dong and identical approval date; bounded lon/lat required.",
        "license": "공공누리 제1유형 출처표시 — 서울특별시",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw", type=Path, default=DEFAULT_RAW)
    parser.add_argument("--download", action="store_true", help="Download the verified 2026-08-31 snapshot again")
    parser.add_argument("--download-seoul", action="store_true", help="Download the public Seoul coordinate supplement")
    parser.add_argument("--observed-at", default=dt.date.today().isoformat())
    args = parser.parse_args()
    if args.download:
        download(SNAPSHOT_URL, args.raw)
    if args.download_seoul:
        download(SEOUL_EXPORT_URL, SEOUL_RAW, {
            "srvType": "S", "infId": "OA-15818", "serviceKind": "1", "pageNo": "1",
            "ssUserId": "SAMPLE_VIEW", "strWhere": "", "strOrderby": "SN ASC",
            "filterCol": "필터선택", "txtFilter": "",
        })
    if not args.raw.is_file():
        parser.error("Raw CSV is missing. Use --download to acquire the verified public snapshot.")
    rows, encoding, payload = read_csv(args.raw)
    records, audit = normalize(rows, args.observed_at)
    coordinate_audit = augment_seoul_coordinates(records, SEOUL_RAW) if SEOUL_RAW.is_file() else None
    coordinate_count = sum(r["lat"] is not None and r["lon"] is not None for r in records)
    out = ROOT / "data/processed"
    out.mkdir(parents=True, exist_ok=True)
    with (out / "complexes.jsonl").open("w", encoding="utf-8") as stream:
        for record in records:
            stream.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
    years = [r["approval_year"] for r in records if r["approval_year"] is not None]
    downloaded_at = dt.datetime.fromtimestamp(args.raw.stat().st_mtime, dt.timezone.utc).isoformat()
    metadata = {
        "name": "한국부동산원 공동주택 단지 식별정보 — 아파트",
        "status": "real", "source_url": SOURCE_URL, "download_url": SNAPSHOT_URL,
        "source_snapshot_date": "2026-08-31", "downloaded_at": downloaded_at,
        "observed_at": args.observed_at, "record_count": len(records),
        "coordinate_count": coordinate_count, "year_min": min(years) if years else None,
        "year_max": max(years) if years else None,
        "coverage_note": "한국부동산원 원자료에서 단지종류=1인 전국 아파트. 현재 공시가격 DB 명칭을 사용승인연도와 비교하며 최초 명명연도는 알 수 없음. 지도 좌표는 서울 공식 자료와 엄격히 연결한 일부 단지만 포함.",
        "coordinate_coverage_note": "전국 REB 원본에는 좌표가 없음. 서울 OpenAptInfo에서 양쪽 유일 도로명주소·법정동·사용승인일이 모두 일치한 단지에만 좌표 보강. 행정구역 중심점 대체 없음.",
        "license": "이용허락범위 제한 없음 — 공공데이터포털 표기",
        "raw_file": str(args.raw.relative_to(ROOT)), "raw_encoding": encoding,
        "raw_bytes": len(payload), "raw_sha256": hashlib.sha256(payload).hexdigest(),
        "name_selection": "단지명_공시가격 → 단지명_건축물대장 → 단지명_도로명주소; first nonempty",
        "audit": audit,
        "coordinate_supplement": coordinate_audit,
    }
    (out / "dataset_meta.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (ROOT / "data/raw/reb_collection_manifest.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"records": len(records), "regions": len(audit["sido_counts"]), "years": [metadata["year_min"], metadata["year_max"]], "coordinates": coordinate_count, "output": str(out / "complexes.jsonl")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
