import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("atlas_name_cleaning_test", ROOT / "src" / "name_cleaning.py")
cleaning = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cleaning)
NODE = os.environ.get("ATLAS_TEST_NODE") or shutil.which("node")
if not NODE:
    candidate = Path.home() / ".cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node"
    NODE = str(candidate) if candidate.is_file() else None


CASES = [
    ("(101동)", ""),
    ("(101동,102동)", ""),
    ("(432-921)", ""),
    ("[제2차]", ""),
    ("（１０１동）", ""),
    ("그린(101동)", "그린"),
    ("그린(101동,102동)", "그린"),
    ("그린(101,102동)", "그린"),
    ("그린(101~103동)", "그린"),
    ("그린(105.106.107동)", "그린"),
    ("그린((101동))", "그린"),
    ("푸른마을(432-921)", "푸른마을"),
    ("그린[제2차]", "그린"),
    ("그린（１０１동）", "그린"),
    ("그린101동", "그린"),
    ("그린2차", "그린"),
    ("파크제3차", "파크"),
    ("거제2차", "거제"),
    ("장미1", "장미"),
    ("그린(101동,임대)", "그린(임대)"),
    ("그린(101동,고층)", "그린(고층)"),
    ("그린(한강뷰)", "그린(한강뷰)"),
    ("e편한세상", "e편한세상"),
    ("디에이치H1", "디에이치H1"),
    ("3.1기념", "3.1기념"),
    ("거제2차현대홈타운", "거제 현대홈타운"),
    ("DUO302", "DUO302"),
    ("LOFT129", "LOFT129"),
    ("교대역동서프라임36.5", "교대역동서프라임36.5"),
    ("청라반도유보라2.0", "청라반도유보라2.0"),
]


NODE_RUNNER = r"""
import { readFileSync } from 'node:fs';
const source = readFileSync(process.argv[1], 'utf8');
const { cleanApartmentName } = await import('data:text/javascript;base64,' + Buffer.from(source).toString('base64'));
const originals = JSON.parse(readFileSync(0, 'utf8'));
const cleaned = originals.map(value => cleanApartmentName(value));
process.stdout.write(JSON.stringify({ originals, cleaned, repeated: cleaned.map(value => cleanApartmentName(value)) }));
"""


class NameCleaningTests(unittest.TestCase):
    def test_numeric_metadata_is_removed_without_erasing_meaningful_names(self):
        for original, expected in CASES:
            with self.subTest(original=original):
                self.assertEqual(cleaning.clean_apartment_name(original), expected)

    def test_cleaning_is_idempotent_and_preserves_original_input(self):
        originals = [original for original, _ in CASES]
        snapshot = list(originals)
        first = [cleaning.clean_apartment_name(original) for original in originals]
        self.assertEqual(originals, snapshot)
        self.assertEqual([cleaning.clean_apartment_name(value) for value in first], first)

    @unittest.skipUnless(NODE, "Node.js is required for JavaScript/Python cleaning parity")
    def test_javascript_matches_python_and_is_idempotent(self):
        originals = [original for original, _ in CASES]
        completed = subprocess.run(
            [NODE, "--input-type=module", "-e", NODE_RUNNER, str(ROOT / "frontend" / "name-cleaning.js")],
            input=json.dumps(originals, ensure_ascii=False), text=True, capture_output=True, timeout=15,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertEqual(result["originals"], originals)
        self.assertEqual(result["cleaned"], [cleaning.clean_apartment_name(value) for value in originals])
        self.assertEqual(result["cleaned"], result["repeated"])

    @unittest.skipUnless(importlib.util.find_spec("kiwipiepy"), "Install kiwipiepy or run .venv-nlp/bin/python")
    def test_numeric_annotations_do_not_enter_modifier_frequency_or_change_denominator(self):
        nlp_spec = importlib.util.spec_from_file_location("atlas_cleaning_nlp_test", ROOT / "src" / "modifier_nlp.py")
        nlp = importlib.util.module_from_spec(nlp_spec)
        nlp_spec.loader.exec_module(nlp)
        rows = [{
            "id": f"nlp-clean-{index}", "name": name, "sido": "서울특별시", "sigungu": "종로구", "dong": "내수동",
            "approval_year": 2000, "source_url": "https://example.org/name-cleaning-fixtures", "is_demo": False,
        } for index, name in enumerate(["그린(101동)", "그린(102동)", "(432-921)"])]
        result = nlp.analyze_records(rows, entity_dictionary={"version": "test-empty", "entries": []}, accepted_terms=[])
        self.assertEqual(result["totals"]["complex_count"], 3)
        self.assertEqual([(entry["token"], entry["complex_count"], entry["share"]) for entry in result["ranking"]], [("그린", 2, 2 / 3)])
        self.assertTrue(any(event["surface"] == "101동" and event["status"] == "excluded" for event in result["occurrences"]))
        self.assertEqual(rows[0]["name"], "그린(101동)")


if __name__ == "__main__":
    unittest.main()
