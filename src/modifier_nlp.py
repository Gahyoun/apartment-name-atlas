"""Residual-name NLP with entity protection and an explicit lexical review gate.

Kiwi analyzes every residual lexical span. The accepted ranking contains only
reviewed modifier vocabulary also supported by a lossless Kiwi analysis. Novel
common nouns and proper names are returned separately for corpus review.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from copy import deepcopy
import importlib.metadata
import importlib.util
import json
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location("atlas_modifier_backend", ROOT / "backend" / "server.py")
_BACKEND = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_BACKEND)
normalize_name = _BACKEND.normalize_name
MODULE_VERSION = "modifier-nlp-1.1.0"
KIWI_DOCUMENTATION = "https://github.com/bab2min/kiwipiepy"

# This is a starting lexicon, not the universe of discoverable NLP candidates.
# Town/city/palace are retained as marketing modifiers by the research policy.
SEED_MODIFIERS = (
    "그린", "센트럴", "파크", "리버", "레이크", "오션", "포레", "포레스트",
    "힐", "뷰", "팰리스", "가든", "시티", "타운", "메트로", "에듀", "더퍼스트",
    "퍼스트", "스카이", "로열", "노블레스", "프레스티지", "클래스", "스위트",
    "드림", "에코", "골드", "골든", "블루", "프리미엄", "숲", "숲속", "하늘",
)
COMPOUND_SPLITS = {
    "리버뷰": ("리버", "뷰"), "파크뷰": ("파크", "뷰"), "오션뷰": ("오션", "뷰"),
    "레이크뷰": ("레이크", "뷰"), "스카이뷰": ("스카이", "뷰"),
}
HOUSING_FORMS = (
    "아파트", "아파아트", "공동주택", "도시형생활주택", "주상복합", "오피스텔",
    "연립주택", "연립", "주택", "맨션", "맨숀", "빌라트", "빌리지", "빌라", "빌",
    "단지", "차", "국민임대", "공공임대", "장기전세", "행복주택", "임대", "분양",
    "타워", "하우스", "하이츠",
)
SUPPLY_FORMS = ("시영", "공영", "주공")
# These guards assert only that a shorter substring is unsafe, not entity truth.
DEFAULT_GUARDS = ("리버티", "자이언트", "서울숲", "한강", "북한산", "한라산", "위례", "DMC", "센텀")
ENTITY_CATEGORIES = {"brand", "company", "place", "protected"}


def _read_spec(value, *, default=None) -> dict:
    if value is None:
        return deepcopy(default or {"version": "none", "entries": []})
    if isinstance(value, (str, Path)):
        with Path(value).open(encoding="utf-8") as handle:
            value = json.load(handle)
    if isinstance(value, (list, tuple, set)):
        return {"version": "caller-reviewed", "entries": [{"canonical": item} if isinstance(item, str) else item for item in value]}
    if not isinstance(value, dict):
        raise ValueError("사전은 JSON 경로, entries 객체 또는 단어 목록이어야 합니다.")
    if "entries" not in value:
        # A simple alias -> canonical mapping is useful during manual review.
        return {"version": "caller-reviewed", "entries": [{"canonical": canonical, "aliases": [alias]} for alias, canonical in value.items()]}
    if not isinstance(value["entries"], list):
        raise ValueError("사전 entries는 배열이어야 합니다.")
    return deepcopy(value)


def load_entity_dictionary(value=None) -> dict:
    if value is None:
        path = ROOT / "dictionaries" / "nlp-entities.json"
        value = path if path.exists() else None
    spec = _read_spec(value)
    for entry in spec["entries"]:
        if not isinstance(entry, dict) or not isinstance(entry.get("canonical"), str):
            raise ValueError("엔터티 사전의 각 항목에는 canonical 문자열이 필요합니다.")
        if entry.get("category") not in ENTITY_CATEGORIES:
            raise ValueError(f"지원하지 않는 엔터티 category: {entry.get('category')}")
    return spec


def modifier_lexicon(accepted_terms=None) -> tuple[dict[str, dict], dict]:
    reviewed = _read_spec(accepted_terms)
    entries = [{"canonical": term, "reason": "research_seed_modifier", "source": "project_seed_lexicon"} for term in SEED_MODIFIERS]
    entries.append({"canonical": "로열", "aliases": ["로얄"], "reason": "reviewed_spelling_alias", "source": "project_seed_lexicon"})
    entries.extend(reviewed["entries"])
    by_alias = {}
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("canonical"), str):
            raise ValueError("검수 수식어 항목에는 canonical 문자열이 필요합니다.")
        canonical = normalize_name(entry["canonical"])
        if not canonical:
            raise ValueError("빈 수식어는 등록할 수 없습니다.")
        if canonical in HOUSING_FORMS or canonical in SUPPLY_FORMS:
            raise ValueError(f"주거 형식·공급주체 {canonical!r}는 수식어 순위 대신 form_ranking에서 확인하세요.")
        for alias in set(entry.get("aliases", []) + [entry["canonical"]]):
            alias = normalize_name(alias)
            if not alias:
                raise ValueError("빈 alias는 등록할 수 없습니다.")
            by_alias[alias] = {
                **entry, "canonical": canonical,
                "reason": entry.get("reason", "manually_reviewed_modifier"),
                "source": entry.get("source", "accepted_terms_corpus_review"),
            }
    return by_alias, reviewed


def build_kiwi(lexicon: dict[str, dict]):
    try:
        from kiwipiepy import Kiwi
    except ImportError as exc:
        raise RuntimeError("실제 NLP 분석에는 kiwipiepy가 필요합니다. .venv-nlp/bin/python으로 실행하세요.") from exc
    kiwi = Kiwi(num_workers=2, load_typo_dict=False, typos=None)
    configure_kiwi(kiwi, lexicon)
    return kiwi


def configure_kiwi(kiwi, lexicon: dict[str, dict]) -> None:
    for word in sorted(lexicon, key=lambda text: (-len(text), text)):
        kiwi.add_user_word(word, "NNG", 3.0)
    for surface, pieces in COMPOUND_SPLITS.items():
        cursor, analyzed = 0, []
        for piece in pieces:
            analyzed.append((piece, "NNG", cursor, cursor + len(piece)))
            cursor += len(piece)
        kiwi.add_pre_analyzed_word(surface, analyzed, 5.0)


def protection_dictionary(dictionary: dict, entities: dict) -> tuple[dict, dict[str, dict]]:
    # Do not inherit nature/premium segmentation: it can leave artificial fragments.
    chosen = {}
    guards = [{"canonical": word, "category": "protected", "provisional": True, "reason": "whole_word_false_positive_guard"} for word in DEFAULT_GUARDS]
    guards.extend({**entry, "category": "protected"} for entry in dictionary["entries"] if "lexical_false_positive_protection" in entry.get("review_reasons", []))
    main = [entry for entry in dictionary["entries"] if entry["category"] in {"brand", "company", "place"}]
    forms = [{"canonical": word, "category": "building_type", "reason": "housing_form_separate_count"} for word in HOUSING_FORMS]
    forms.extend({"canonical": word, "category": "housing_provider", "reason": "housing_supply_separate_count"} for word in SUPPLY_FORMS)
    for collection, origin, version in (
        (forms, "nlp_form_policy", MODULE_VERSION), (guards, "whole_word_guard", MODULE_VERSION),
        (main, "main_entity_dictionary", dictionary.get("version")),
        (entities["entries"], "supplementary_entity_dictionary", entities.get("version")),
    ):
        for entry in collection:
            for alias in set(entry.get("aliases", [entry["canonical"]])):
                alias = normalize_name(alias)
                if alias:
                    chosen[alias] = {**entry, "aliases": [alias], "_origin": origin, "_version": version}
    # Each alias is an independent entry so a supplementary override cannot erase other aliases.
    return {"version": MODULE_VERSION, "entries": list(chosen.values())}, chosen


def _event(record, normalized, start, end, canonical, status, category, reason, *, pos=None, source=None, **extra):
    return {
        "id": record["id"], "complex_id": record["id"], "name_raw": record["name"],
        "name_normalized": normalized, "canonical": canonical, "surface": normalized[start:end],
        "start": start, "end": end, "status": status, "category": category,
        "reason": reason, "reason_codes": [reason], "source": source or {},
        "source_url": record.get("source_url"), "pos": pos,
        "approval_year": record.get("approval_year"), "sido": record.get("sido", ""),
        "sigungu": record.get("sigungu", ""), "dong": record.get("dong", ""), **extra,
    }


def _serialize_analysis(tokens) -> list[dict]:
    return [{"form": token.form, "pos": token.tag, "start": token.start, "end": token.start + token.len} for token in tokens]


def _residual_events(record, normalized, start, end, analysis, lexicon, engine_source):
    text = normalized[start:end]
    lexical = [token for token in analysis if token["end"] > token["start"]]
    suspicious = []
    context_reasons = []
    cursor = 0
    for token in lexical:
        left, right, pos = token["start"], token["end"], token["pos"]
        surface = text[left:right] if 0 <= left <= right <= len(text) else ""
        if left != cursor or not surface or right > len(text):
            suspicious.append("overlap_or_uncovered_surface")
        cursor = max(cursor, right)
        if normalize_name(token["form"]) != surface:
            suspicious.append("lemma_or_surface_mismatch")
        if pos not in {"NNG", "NNP", "SL", "SH"}:
            suspicious.append("non_noun_or_inflected_analysis")
        if surface not in lexicon:
            if pos in {"NNP", "SL", "SH"}:
                context_reasons.append("unreviewed_proper_or_foreign_name")
            if len(surface) < 2:
                suspicious.append("unreviewed_short_fragment")
    if cursor != len(text) or len(lexical) != len(analysis):
        suspicious.append("overlap_or_uncovered_surface")
    if not lexical:
        suspicious.append("empty_morphological_analysis")
    if suspicious:
        reason = "ambiguous_residual_whole_span"
        return [_event(
            record, normalized, start, end, text, "review", "unknown", reason,
            source=engine_source, reason_codes=sorted(set(suspicious + context_reasons)), analysis=analysis,
        )]
    context_requires_review = bool(context_reasons)
    result = []
    for token in lexical:
        surface = text[token["start"]:token["end"]]
        accepted = lexicon.get(surface)
        if accepted:
            source = {
                **engine_source, "lexicon_source": accepted["source"], "lexicon_reason": accepted["reason"],
                "lexicon_source_url": accepted.get("source_url"),
            }
            result.append(_event(
                record, normalized, start + token["start"], start + token["end"], accepted["canonical"],
                "accepted", "modifier", "reviewed_modifier_and_kiwi_noun", pos=token["pos"], source=source,
                context_requires_review=context_requires_review, strict_eligible=not context_requires_review,
                context_reason_codes=sorted(set(context_reasons)), residual_surface=text,
            ))
        else:
            is_proper = token["pos"] in {"NNP", "SL", "SH"}
            result.append(_event(
                record, normalized, start + token["start"], start + token["end"], surface,
                "review", "proper_name_candidate" if is_proper else "common_noun_candidate",
                "unreviewed_proper_or_foreign_name" if is_proper else "unreviewed_common_noun",
                pos=token["pos"], source=engine_source, residual_surface=text,
            ))
    return result


def _rank(events, total, *, example_limit=5):
    complex_ids, occurrences = defaultdict(set), Counter()
    examples, reasons, categories, parts_of_speech = defaultdict(list), defaultdict(set), defaultdict(set), defaultdict(set)
    for event in events:
        token = event["canonical"]
        complex_ids[token].add(event["id"])
        occurrences[token] += 1
        reasons[token].update(event["reason_codes"])
        categories[token].add(event["category"])
        if event["pos"]:
            parts_of_speech[token].add(event["pos"])
        if len(examples[token]) < example_limit and event["id"] not in {example["id"] for example in examples[token]}:
            examples[token].append({key: event[key] for key in ("id", "name_raw", "surface", "start", "end", "source_url", "sido", "approval_year")})
    return [{
        "token": token, "canonical": token, "complex_count": len(complex_ids[token]),
        "occurrence_count": occurrences[token], "share": len(complex_ids[token]) / total if total else None,
        "categories": sorted(categories[token]), "pos_tags": sorted(parts_of_speech[token]),
        "reasons": sorted(reasons[token]), "examples": examples[token],
    } for token in sorted(complex_ids, key=lambda word: (-len(complex_ids[word]), word))]


def _accepted_ranking(events, total):
    """Count a token once per complex at both reviewed-token and strict levels.

    A complex can contain both a strict and a context-sensitive occurrence of one
    token. Context counts are therefore an annotation, not an additive partition.
    """
    ranking = _rank(events, total)
    strict = {row["token"]: row for row in _rank([event for event in events if event["strict_eligible"]], total)}
    context = {row["token"]: row for row in _rank([event for event in events if event["context_requires_review"]], total)}
    for row in ranking:
        conservative, contextual = strict.get(row["token"], {}), context.get(row["token"], {})
        row.update({
            "strict_count": conservative.get("complex_count", 0),
            "strict_occurrence_count": conservative.get("occurrence_count", 0),
            "strict_share": conservative.get("complex_count", 0) / total if total else None,
            "context_review_count": contextual.get("complex_count", 0),
            "context_review_occurrence_count": contextual.get("occurrence_count", 0),
        })
    return ranking


def analyze_records(records, *, dictionary=None, entity_dictionary=None, accepted_terms=None, kiwi=None) -> dict:
    """Analyze unique current-name records; accepted_terms admits manually reviewed discoveries.

    No demo rows are accepted. All offsets refer to NFKC names with whitespace
    removed; raw names and provenance remain attached to every occurrence.
    """
    records = list(records)
    seen = set()
    for record in records:
        if not isinstance(record, dict) or not isinstance(record.get("id"), str) or not record["id"]:
            raise ValueError("각 단지에는 비어 있지 않은 문자열 id가 필요합니다.")
        if record["id"] in seen:
            raise ValueError(f"중복 단지 id={record['id']!r}. 단지 한 행으로 정리하세요.")
        seen.add(record["id"])
        if record.get("is_demo") is not False:
            raise ValueError("NLP 실자료 분석에는 is_demo=false가 명시된 행만 허용합니다.")
        if not isinstance(record.get("name"), str) or not normalize_name(record["name"]):
            raise ValueError(f"단지 {record['id']!r}의 이름이 비어 있습니다.")
    dictionary = dictionary if dictionary is not None else _BACKEND.load_dictionary()
    entities = load_entity_dictionary(entity_dictionary)
    lexicon, reviewed = modifier_lexicon(accepted_terms)
    protection, entry_by_alias = protection_dictionary(dictionary, entities)
    if kiwi is None:
        kiwi = build_kiwi(lexicon)
    else:
        configure_kiwi(kiwi, lexicon)
    try:
        kiwi_version = importlib.metadata.version("kiwipiepy")
    except importlib.metadata.PackageNotFoundError:
        kiwi_version = "injected_analyzer"
    engine_source = {"method": "kiwi_residual_noun_analysis", "engine": "kiwipiepy", "version": kiwi_version, "documentation_url": KIWI_DOCUMENTATION}
    occurrences, segments, entity_reviews = [], [], []
    residual_names = set()
    for record in records:
        normalized, tokens = _BACKEND.tokenize_with_address(record, protection)
        for token in tokens:
            category, surface = token["category"], token["surface"]
            if category == "unclassified":
                # Punctuation is a safe visible boundary; alphanumeric compounds stay intact.
                for match in re.finditer(r"[^\W_]+|[\W_]+", surface, flags=re.UNICODE):
                    start, end = token["start"] + match.start(), token["start"] + match.end()
                    if any(character.isalnum() for character in match.group()):
                        segments.append((record, normalized, start, end))
                        residual_names.add(match.group())
                    else:
                        occurrences.append(_event(record, normalized, start, end, match.group(), "excluded", "punctuation", "punctuation_boundary", source={"method": "surface_boundary"}))
                continue
            entry = entry_by_alias.get(surface, {})
            source = {"method": entry.get("_origin", "address_context_match"), "version": entry.get("_version", MODULE_VERSION), "dictionary_source_url": entry.get("source_url"), "dictionary_reason": entry.get("reason", entry.get("source_note"))}
            status, reason = "excluded", f"protected_{category}"
            if category == "protected":
                status, reason = "review", "protected_whole_name_requires_review"
            elif category == "place":
                if token.get("address_match") is True:
                    reason = "local_address_name_match"
                else:
                    status, reason = "review", "place_candidate_without_local_address_confirmation"
            elif category == "number":
                reason = "number_or_phase_separate_count"
            elif category == "building_type":
                reason = "housing_form_separate_count"
            elif category == "housing_provider":
                reason = "housing_supply_separate_count"
            event = _event(record, normalized, token["start"], token["end"], token["canonical"], status, category, reason, source=source)
            if "address_match" in token:
                event["address_match"] = token["address_match"]
            if token.get("provisional") and category in {"brand", "company"}:
                event["entity_match_needs_review"] = True
                entity_reviews.append(event)
            occurrences.append(event)
    names = sorted(residual_names)
    analyses = {}
    if names:
        # Iterable input lets Kiwi batch actual NLP work; repeated residuals are analyzed once.
        for text, result in zip(names, kiwi.tokenize(names, normalize_coda=False, split_complex=False), strict=True):
            analyses[text] = _serialize_analysis(result)
    for record, normalized, start, end in segments:
        occurrences.extend(_residual_events(record, normalized, start, end, analyses[normalized[start:end]], lexicon, engine_source))
    order = {record["id"]: index for index, record in enumerate(records)}
    occurrences.sort(key=lambda event: (order[event["id"]], event["start"], event["end"]))
    # Events form a lossless, nonoverlapping partition, even when Kiwi itself overlaps.
    by_id = defaultdict(list)
    for event in occurrences:
        by_id[event["id"]].append(event)
    for record in records:
        events = by_id[record["id"]]
        normalized = normalize_name(record["name"])
        if "".join(event["surface"] for event in events) != normalized or any(event["start"] != (events[index - 1]["end"] if index else 0) for index, event in enumerate(events)):
            raise RuntimeError(f"단지 {record['id']}의 NLP span 재구성 불변식이 깨졌습니다.")
    accepted = [event for event in occurrences if event["status"] == "accepted"]
    strict_accepted = [event for event in accepted if event["strict_eligible"]]
    contextual_accepted = [event for event in accepted if event["context_requires_review"]]
    review_queue = [event for event in occurrences if event["status"] == "review"]
    excluded = [event for event in occurrences if event["status"] == "excluded"]
    form_events = [event for event in excluded if event["category"] in {"building_type", "housing_provider"} or event["canonical"] in SUPPLY_FORMS]
    raw_residual = [event for event in occurrences if event["status"] in {"accepted", "review"} or event["category"] in {"building_type", "housing_provider"} or event["canonical"] in SUPPLY_FORMS]
    total = len(records)
    return {
        "method": {
            "version": MODULE_VERSION, "engine": "kiwipiepy", "engine_version": kiwi_version,
            "dictionary_version": dictionary.get("version"), "entity_dictionary_version": entities.get("version"),
            "accepted_terms_version": reviewed.get("version"), "accepted_alias_count": len(lexicon),
            "documentation_url": KIWI_DOCUMENTATION,
            "ranking_definition": "사전 검수 수식어이며 Kiwi가 표면을 보존한 명사로 분석한 occurrence의 단지별 포함 수. 미검수 고유명 이웃이 있으면 context_requires_review로 표시한다.",
            "strict_ranking_definition": "미검수 고유명·외국어 이웃이 없는 residual에서 추출한 수식어의 단지별 포함 수. ranking의 strict_count도 같은 기준이다.",
            "context_review_policy": "NNP·외국어 이웃은 별도 review로 남기며 표면과 span이 정확한 검수 수식어를 함께 추출한다. overlap·lemma 불일치·비명사·미검수 1글자 조각이 있으면 residual 전체를 검수로 보낸다.",
            "candidate_definition": "사전 밖 일반명사·고유명·불명확한 복합명은 별도 검수 큐이며 확정 순위에 포함하지 않음",
            "compound_policy": COMPOUND_SPLITS, "marketing_terms_retained": ["타운", "시티", "팰리스"],
            "form_policy": "아파트·맨션·빌라트·빌 등 형식과 주공·시영 등 공급주체/형식은 보조 표로 보존한다. 숫자·차수는 별도 범주다.",
            "named_place_policy": "주소와 일치하는 지명만 지역 제외로 확정한다. 미확인 권역·명소 전체어는 검수 대상으로 보존한다.",
            "span_coordinates": "NFKC and whitespace-removed name; Unicode code points; end exclusive",
            "surface_policy": "원문 표면어를 보존하며 어간·lemma로 자동 치환하지 않는다.",
        },
        "totals": {
            "complex_count": total, "accepted_complex_count": len({event["id"] for event in accepted}),
            "review_complex_count": len({event["id"] for event in review_queue}),
            "occurrence_count": len(occurrences), "accepted_occurrence_count": len(accepted),
            "strict_accepted_complex_count": len({event["id"] for event in strict_accepted}),
            "strict_accepted_occurrence_count": len(strict_accepted),
            "context_review_complex_count": len({event["id"] for event in contextual_accepted}),
            "context_review_occurrence_count": len(contextual_accepted),
            "review_occurrence_count": len(review_queue), "excluded_occurrence_count": len(excluded),
            "unique_residuals_analyzed": len(names), "entity_review_occurrence_count": len(entity_reviews),
        },
        "ranking": _accepted_ranking(accepted, total), "strict_ranking": _rank(strict_accepted, total),
        "candidate_ranking": _rank(review_queue, total),
        "raw_residual_ranking": _rank(raw_residual, total), "form_ranking": _rank(form_events, total),
        "excluded_counts": {category: _rank([event for event in excluded if event["category"] == category], total) for category in sorted({event["category"] for event in excluded})},
        "occurrences": occurrences, "review_queue": review_queue, "entity_review_queue": entity_reviews,
        "warnings": [
            "Kiwi 품사 태그만으로 고유명·브랜드 여부나 수식어 의미를 확정할 수 없습니다. 미검수 후보는 순위와 분리했습니다.",
            "ranking에는 미검수 고유명 이웃의 문맥 검토가 필요한 수식어도 포함됩니다. strict_count·strict_ranking과 비교해 민감도를 확인하세요. context_review_count와 strict_count는 같은 단지를 포함할 수 있습니다.",
            "정규화된 이름에서 식별 가능한 엔터티만 제외합니다. 엔터티 사전 누락과 주소 중의성은 검수 큐를 통해 보완해야 합니다.",
            "빈도는 실제 이름의 언어적 사용량이며 해당 단지의 입지·시설·품질을 검증한 수치가 아닙니다.",
            "form_ranking은 같은 occurrence를 참조하는 보조 표입니다. 다른 표와 단순 합산하면 중복됩니다.",
        ],
    }
