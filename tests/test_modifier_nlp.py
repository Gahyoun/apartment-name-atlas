import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("modifier_nlp_test", ROOT / "src" / "modifier_nlp.py")
nlp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(nlp)


class FakeKiwi:
    """Controllable morphology output for overlap/error-boundary invariants."""
    def __init__(self, mappings=None):
        self.mappings = mappings or {}

    def add_user_word(self, *args):
        return True

    def add_pre_analyzed_word(self, *args):
        return True

    def tokenize(self, texts, **kwargs):
        for text in texts:
            tokens = self.mappings.get(text, [(text, "NNG", 0, len(text))])
            yield [SimpleNamespace(form=form, tag=tag, start=start, len=length) for form, tag, start, length in tokens]


class ModifierNLPTests(unittest.TestCase):
    def record(self, name, identity="test", **extra):
        return {
            "id": identity, "name": name, "sido": "서울특별시", "sigungu": "종로구", "dong": "내수동",
            "approval_year": 2000, "source_url": "https://example.org/nlp-test-fixtures", "is_demo": False, **extra,
        }

    def run_analysis(self, records, *, mappings=None, entities=None, accepted=None):
        result = nlp.analyze_records(
            records, dictionary=nlp._BACKEND.load_dictionary(),
            entity_dictionary=entities or {"version": "test-empty", "entries": []},
            accepted_terms=accepted if accepted is not None else [], kiwi=FakeKiwi(mappings),
        )
        for record in records:
            events = [event for event in result["occurrences"] if event["id"] == record["id"]]
            self.assertEqual("".join(event["surface"] for event in events), nlp.normalize_name(record["name"]))
            for index, event in enumerate(events):
                self.assertEqual(event["start"], events[index - 1]["end"] if index else 0)
                self.assertEqual(event["surface"], event["name_normalized"][event["start"]:event["end"]])
                self.assertEqual(event["source_url"], record["source_url"])
        return result

    def test_entity_interiors_are_not_recounted_as_modifiers(self):
        rows = [self.record("한화꿈에그린파크"), self.record("서울숲리버", "landmark")]
        result = self.run_analysis(rows)
        ranking = {row["token"]: row["complex_count"] for row in result["ranking"]}
        self.assertEqual(ranking, {"파크": 1, "리버": 1})
        self.assertNotIn("그린", ranking)
        self.assertNotIn("숲", ranking)
        self.assertTrue(any(event["surface"] == "서울숲" and event["status"] == "review" for event in result["occurrences"]))

    def test_entity_aliases_do_not_implicitly_add_an_unsafe_canonical_spelling(self):
        entities = {"version": "fixture", "entries": [{"canonical": "화성", "aliases": ["화성산업"], "category": "company", "source_url": "https://example.org/company-fixture"}]}
        result = self.run_analysis([self.record("화성파크"), self.record("화성산업파크", "company")], entities=entities)
        bare = [event for event in result["occurrences"] if event["id"] == "test"]
        self.assertFalse(any(event["category"] == "company" for event in bare))
        protected = next(event for event in result["occurrences"] if event["surface"] == "화성산업")
        self.assertEqual(protected["status"], "excluded")
        self.assertEqual(protected["source"]["dictionary_source_url"], "https://example.org/company-fixture")

    def test_new_common_nouns_require_explicit_review_then_enter_ranking(self):
        mappings = {"장미개나리": [("장미", "NNG", 0, 2), ("개나리", "NNG", 2, 3)]}
        records = [self.record("장미개나리아파트")]
        first = self.run_analysis(records, mappings=mappings)
        self.assertEqual(first["ranking"], [])
        self.assertEqual({row["token"] for row in first["candidate_ranking"]}, {"장미", "개나리"})
        reviewed = {"version": "manual-fixture-1", "entries": [{"canonical": "장미", "reason": "flower_word_checked_in_names", "source_url": "https://example.org/manual-review"}]}
        second = self.run_analysis(records, mappings=mappings, accepted=reviewed)
        self.assertEqual([row["token"] for row in second["ranking"]], ["장미"])
        event = next(event for event in second["occurrences"] if event["status"] == "accepted")
        self.assertEqual(event["source"]["lexicon_source_url"], "https://example.org/manual-review")

    def test_ambiguous_compound_does_not_leak_a_short_seed_fragment(self):
        mappings = {"포레스티아": [("포레", "NNG", 0, 2), ("스티", "NNP", 2, 2), ("아", "NNG", 4, 1)]}
        result = self.run_analysis([self.record("포레스티아")], mappings=mappings)
        self.assertEqual(result["ranking"], [])
        self.assertEqual(result["review_queue"][0]["surface"], "포레스티아")
        self.assertIn("unreviewed_proper_or_foreign_name", result["review_queue"][0]["reason_codes"])

    def test_known_noun_next_to_proper_name_has_a_separate_strict_count(self):
        mappings = {"아르테그린": [("아르테", "NNP", 0, 3), ("그린", "NNG", 3, 2)]}
        rows = [self.record("아르테그린"), self.record("그린", "strict")]
        result = self.run_analysis(rows, mappings=mappings)
        green = result["ranking"][0]
        self.assertEqual((green["token"], green["complex_count"], green["strict_count"], green["context_review_count"]), ("그린", 2, 1, 1))
        self.assertEqual((green["share"], green["strict_share"]), (1.0, 0.5))
        self.assertEqual(result["strict_ranking"][0]["complex_count"], 1)
        contextual = next(event for event in result["occurrences"] if event["id"] == "test" and event["status"] == "accepted")
        self.assertTrue(contextual["context_requires_review"])
        self.assertFalse(contextual["strict_eligible"])
        self.assertEqual((contextual["surface"], contextual["start"], contextual["end"]), ("그린", 3, 5))
        self.assertEqual(result["review_queue"][0]["canonical"], "아르테")
        self.assertEqual(result["review_queue"][0]["category"], "proper_name_candidate")

    def test_inflected_overlap_keeps_the_surface_instead_of_replacing_it_with_a_lemma(self):
        mappings = {"푸른마을": [("푸르", "VA-I", 0, 2), ("ᆫ", "ETM", 1, 1), ("마을", "NNG", 2, 2)]}
        result = self.run_analysis([self.record("푸른마을")], mappings=mappings)
        self.assertEqual(result["ranking"], [])
        self.assertEqual(result["review_queue"][0]["canonical"], "푸른마을")
        self.assertIn("lemma_or_surface_mismatch", result["review_queue"][0]["reason_codes"])

    def test_forms_and_numbers_are_preserved_without_double_counting_repeated_modifiers(self):
        mappings = {"그린그린": [("그린", "NNG", 0, 2), ("그린", "NNG", 2, 2)]}
        rows = [self.record("그린그린빌라트2차"), self.record("파크빌", "park"), self.record("시영아파트", "supply")]
        result = self.run_analysis(rows, mappings=mappings)
        green = next(row for row in result["ranking"] if row["token"] == "그린")
        self.assertEqual((green["complex_count"], green["occurrence_count"]), (1, 2))
        self.assertEqual({row["token"] for row in result["form_ranking"]}, {"빌라트", "빌", "시영", "아파트"})
        self.assertEqual(result["excluded_counts"]["number"][0]["token"], "2차")
        self.assertIn("파크", [row["token"] for row in result["ranking"]])
        self.assertNotIn("트", [row["token"] for row in result["candidate_ranking"]])

    def test_address_inner_substring_does_not_become_a_confirmed_place(self):
        result = self.run_analysis([self.record("세종로대우")])
        self.assertEqual([event["surface"] for event in result["occurrences"]], ["세종로", "대우"])
        self.assertFalse(any(event["canonical"] == "종로" for event in result["occurrences"]))

    def test_extended_housing_forms_are_not_marketing_modifiers(self):
        rows = [self.record(name, str(index)) for index, name in enumerate(["행복주택", "그린타워", "그린하우스", "그린하이츠"])]
        result = self.run_analysis(rows, accepted=["행복"])
        self.assertEqual({row["token"] for row in result["form_ranking"]}, {"행복주택", "타워", "하우스", "하이츠"})
        self.assertEqual([row["token"] for row in result["ranking"]], ["그린"])

    def test_duplicate_ids_and_demo_rows_fail_before_analysis(self):
        row = self.record("그린")
        with self.assertRaisesRegex(ValueError, "중복"):
            self.run_analysis([row, row])
        with self.assertRaisesRegex(ValueError, "is_demo=false"):
            self.run_analysis([{**row, "is_demo": True}])

    @unittest.skipUnless(importlib.util.find_spec("kiwipiepy"), "Install kiwipiepy or run .venv-nlp/bin/python")
    def test_real_kiwi_compounds_and_new_noun_discovery(self):
        rows = [self.record("리버뷰빌라트"), self.record("센트럴포레스트", "forest"), self.record("장미개나리", "new")]
        result = nlp.analyze_records(rows, entity_dictionary={"version": "empty", "entries": []}, accepted_terms=[])
        self.assertEqual({row["token"] for row in result["ranking"]}, {"리버", "뷰", "센트럴", "포레스트"})
        self.assertEqual({row["token"] for row in result["candidate_ranking"]}, {"장미", "개나리"})
        self.assertTrue(result["method"]["engine_version"].startswith("0."))

    @unittest.skipUnless(importlib.util.find_spec("kiwipiepy"), "Install kiwipiepy or run .venv-nlp/bin/python")
    def test_real_corpus_entity_regressions_preserve_generic_modifiers(self):
        protected_names = [
            "중흥에스-클래스", "중흥s-클래스", "동일하이빌", "우림루미아트",
            "신명스카이뷰", "유림노르웨이숲",
        ]
        rows = [self.record(name, name) for name in protected_names + ["리버뷰", "파크빌"]]
        result = nlp.analyze_records(rows, accepted_terms=ROOT / "dictionaries" / "nlp-modifiers.v1.json")
        for name in protected_names:
            with self.subTest(name=name):
                events = [event for event in result["occurrences"] if event["id"] == name]
                self.assertEqual("".join(event["surface"] for event in events), name)
                self.assertFalse(any(event["status"] == "accepted" for event in events))
                self.assertTrue(any(event["category"] in {"brand", "protected"} for event in events))
        accepted = {
            name: {event["canonical"] for event in result["occurrences"] if event["id"] == name and event["status"] == "accepted"}
            for name in ["리버뷰", "파크빌"]
        }
        self.assertEqual(accepted, {"리버뷰": {"리버", "뷰"}, "파크빌": {"파크"}})
        self.assertTrue(any(event["id"] == "파크빌" and event["canonical"] == "빌" and event["category"] == "building_type" for event in result["occurrences"]))


if __name__ == "__main__":
    unittest.main()
