"""Behavioral invariants; no network or third-party packages required."""

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("analyze", ROOT / "src" / "analyze.py")
analyze = importlib.util.module_from_spec(spec)
spec.loader.exec_module(analyze)


class AnalyzeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dictionary = analyze.load_dictionary()

    def record(self, **overrides):
        result = {
            "complex_id": "test-1", "name_raw": "그린숲속아파트", "region": "서울특별시",
            "completion_year": 1995, "observed_at": "2026-09-28",
            "source_url": "https://example.org/test-fixture", "is_demo": False,
        }
        result.update(overrides)
        return result

    def read(self, records, allow_demo=False):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "records.jsonl"
            path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in records), encoding="utf-8")
            return analyze.load_records(path, allow_demo=allow_demo)

    def test_normalization_reconstruction_and_contiguous_spans(self):
        for original in ("  ＳＫ뷰\t그린 ２차", "미지의숲자이언트빌라", "🏠별빛·리버-아파트", "그린숲속아파트"):
            with self.subTest(name=original):
                normalized, tokens = analyze.tokenize(original, self.dictionary)
                self.assertEqual(normalized, analyze.normalize_name(original))
                self.assertEqual("".join(token["surface"] for token in tokens), normalized)
                self.assertEqual(tokens[0]["start"], 0)
                self.assertEqual(tokens[-1]["end"], len(normalized))
                for index, token in enumerate(tokens):
                    self.assertEqual(token["surface"], normalized[token["start"]:token["end"]])
                    if index:
                        self.assertEqual(tokens[index - 1]["end"], token["start"])
        rows, _, _ = analyze.analyze([self.record(name_raw="  ＳＫ뷰 ２차")], self.dictionary)
        self.assertEqual(rows[0]["name_raw"], "  ＳＫ뷰 ２차")
        self.assertEqual(rows[0]["name_normalized"], "SK뷰2차")

    def test_longest_brand_prevents_company_and_suffix_double_count(self):
        _, tokens = analyze.tokenize("화명카이저롯데캐슬", self.dictionary)
        self.assertEqual([token["canonical"] for token in tokens], ["화명", "카이저", "롯데캐슬"])
        self.assertEqual(tokens[-1]["category"], "brand")
        self.assertFalse(tokens[-1]["needs_review"])
        self.assertTrue(tokens[0]["provisional"] and tokens[0]["needs_review"])
        self.assertEqual(tokens[1]["category"], "brand")
        self.assertFalse(tokens[1]["provisional"])
        self.assertIsNone(tokens[1]["parent_brand"])
        self.assertEqual(tokens[-1]["corporate_group"], "롯데")
        self.assertEqual(sum(token["category"] == "brand" for token in tokens), 2)
        self.assertNotIn("롯데", [token["surface"] for token in tokens])
        _, tokens = analyze.tokenize("한화꿈에그린아파트", self.dictionary)
        self.assertEqual(tokens[0]["canonical"], "한화꿈에그린")

    def test_short_substrings_and_unknown_spans_require_review(self):
        rows, _, reviews = analyze.analyze([self.record(name_raw="미지의숲자이언트빌라")], self.dictionary)
        tokens = rows[0]["tokens"]
        for word in ("숲", "자이"):
            token = next(token for token in tokens if token["surface"] == word)
            self.assertTrue(token["provisional"])
            self.assertIn("short_substring_match", token["review_reasons"])
        self.assertTrue(any(token["category"] == "unclassified" for token in tokens))
        self.assertEqual(len(reviews), sum(token["needs_review"] for token in tokens))

    def test_brand_and_whole_word_protection_prevents_false_geographic_claims(self):
        cases = [
            ("한화포레나미아", "포레나", "포레"),
            ("동작협성휴포레시그니처", "휴포레", "포레"),
            ("화성파크드림", "파크드림", "파크"),
            ("코오롱하늘채", "하늘채", "하늘"),
            ("서초동롯데캐슬리버티", "리버티", "리버"),
        ]
        for name, protected, false_token in cases:
            with self.subTest(name=name):
                normalized, tokens = analyze.tokenize(name, self.dictionary)
                keys = {token["canonical"] for token in tokens}
                self.assertIn(protected, keys)
                self.assertNotIn(false_token, keys)
                self.assertEqual("".join(token["surface"] for token in tokens), normalized)

    def test_duplicate_identity_is_an_actionable_error(self):
        with self.assertRaisesRegex(analyze.AnalysisError, "중복 complex_id.*첫 행 1"):
            self.read([self.record(), self.record(name_raw="다른이름아파트")])

    def test_schema_year_date_and_real_source_are_validated(self):
        bad_records = [
            self.record(completion_year=True), self.record(completion_year=2027),
            self.record(completion_year="1995"), self.record(observed_at="2026-02-30"),
            self.record(observed_at="20260928"), self.record(source_url=None),
            self.record(source_url="file:///data.json"), self.record(is_demo="false"),
            self.record(region=""), self.record(typo_field=1),
        ]
        for record in bad_records:
            with self.subTest(record=record), self.assertRaises(analyze.AnalysisError):
                self.read([record])

    def test_unknown_year_is_tokenized_and_excluded_from_all_temporal_denominators(self):
        records = [self.record(completion_year=None)]
        rows, summary, _ = analyze.analyze(self.read(records), self.dictionary)
        self.assertTrue(rows[0]["tokens"])
        self.assertEqual(summary["decade_region"], [])
        self.assertEqual(summary["totals"]["missing_completion_year_count"], 1)
        records.append(self.record(complex_id="test-2"))
        _, summary, _ = analyze.analyze(self.read(records), self.dictionary)
        self.assertEqual(summary["decade_region"][0]["complex_count"], 1)

    def test_demo_is_rejected_by_default_and_taints_the_entire_output_when_allowed(self):
        records = [self.record(), self.record(complex_id="demo-2", is_demo=True, source_url=None)]
        with self.assertRaisesRegex(analyze.AnalysisError, "--allow-demo"):
            self.read(records)
        rows, summary, reviews = analyze.analyze(self.read(records, allow_demo=True), self.dictionary)
        self.assertTrue(summary["demo"])
        self.assertTrue(all(row["demo"] for row in rows))
        self.assertTrue(all(row["demo"] for row in reviews))
        self.assertEqual(summary["totals"]["demo_record_count"], 1)
        examples = analyze.load_records(ROOT / "data" / "examples" / "names.jsonl", allow_demo=True)
        self.assertEqual(len(examples), 4)
        self.assertTrue(all(row["is_demo"] and row["completion_year"] is None and row["source_url"] is None for row in examples))
        _, example_summary, _ = analyze.analyze(examples, self.dictionary)
        self.assertEqual(example_summary["decade_region"], [])

    def test_prevalence_counts_complexes_once_and_writes_readable_outputs(self):
        records = self.read([
            self.record(name_raw="그린그린아파트"),
            self.record(complex_id="test-2", name_raw="별빛아파트"),
            self.record(complex_id="test-3", name_raw="그린아파트", region="부산광역시"),
        ])
        rows, summary, reviews = analyze.analyze(records, self.dictionary)
        group = next(group for group in summary["decade_region"] if group["region"] == "서울특별시")
        green = next(token for token in group["token_prevalence"] if token["token"] == "그린")
        self.assertEqual(green["complex_count"], 1)
        self.assertEqual(green["share"], 0.5)
        self.assertEqual(group["mean_name_character_count"], (len("그린그린아파트") + len("별빛아파트")) / 2)
        with tempfile.TemporaryDirectory() as directory:
            analyze.write_outputs(Path(directory), rows, summary, reviews)
            saved = json.loads((Path(directory) / "summary.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["totals"]["complex_count"], 3)
            self.assertEqual(len((Path(directory) / "tokens.jsonl").read_text(encoding="utf-8").splitlines()), 3)
            self.assertTrue((Path(directory) / "review.jsonl").exists())


if __name__ == "__main__":
    unittest.main()
