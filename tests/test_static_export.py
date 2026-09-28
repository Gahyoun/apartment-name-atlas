import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("atlas_export_test", ROOT / "scripts" / "export_site.py")
exporter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(exporter)
server = exporter.SERVER
NODE = os.environ.get("ATLAS_TEST_NODE") or shutil.which("node")
if not NODE:
    candidate = Path.home() / ".cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node"
    NODE = str(candidate) if candidate.is_file() else None


NODE_RUNNER = r"""
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
const [directory, workerFile] = process.argv.slice(1);
const source = readFileSync(workerFile, 'utf8');
const { StaticEngine } = await import('data:text/javascript;base64,' + Buffer.from(source).toString('base64'));
const loader = async path => JSON.parse(readFileSync(resolve(directory, path), 'utf8'));
const config = await loader('./runtime-config.json');
const engine = new StaticEngine(await loader(config.data.core), loader, config);
const queries = JSON.parse(readFileSync(0, 'utf8'));
const results = [];
for (const query of queries) {
  try { results.push(await engine.request(query.path, query.params || {})); }
  catch (error) { results.push({ error: error.message }); }
}
process.stdout.write(JSON.stringify(results));
"""


class StaticExportTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.frontend = self.directory / "frontend"
        self.frontend.mkdir()
        (self.frontend / "index.html").write_text('<script src="/app.js"></script><link href="/styles.css">', encoding="utf-8")
        (self.frontend / "app.js").write_text("/* fixture */", encoding="utf-8")
        (self.frontend / ".env.local").write_text("PRIVATE_FIXTURE=must-not-be-copied", encoding="utf-8")
        self.rows = []
        self.distances = []
        for index in range(12):
            name = "파크파크아파트" if index < 6 else "리버아파트"
            identity = f"fixture-{index}"
            self.rows.append({
                "id": identity, "name": name, "sido": "서울특별시", "sigungu": "강남구", "dong": "역삼동",
                "dong_code": "test-code", "approval_year": 2000 + index % 3,
                "lat": 37.5 + index / 1000, "lon": 127.0,
                "source_url": "https://example.org/public-test-fixtures", "is_demo": False,
                "name_variants": {"단지명_공시가격": name, "단지명_건축물대장": name + "(원문)"},
                "name_basis": "단지명_공시가격", "name_disagreement": True, "address": "공개 단지 주소",
            })
            self.distances.append({"id": identity, "feature": "park", "distance_m": (50.25 + index * 10) if index < 6 else (170.75 + index * 10)})
        self.rows.extend([
            {**self.rows[0], "id": "missing-year", "name": "🏠미지별자리", "dong": "삼성동", "approval_year": None, "lat": None, "lon": None},
            {**self.rows[0], "id": "brand", "name": "화명카이저롯데캐슬", "sido": "부산광역시", "sigungu": "북구", "dong": "화명동", "approval_year": 1995, "lat": None, "lon": None},
        ])
        self.store = server.DataStore(records=self.rows, distances=self.distances, metadata={"name": "표준화 테스트 자료", "source_url": "https://example.org/public-test-fixtures"})
        self.store.geo_features = {"type": "FeatureCollection", "features": [
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [127.0, 37.5]}, "properties": {"feature": "park", "name": "공원 예제", "source_url": "https://example.org/gis-fixture"}},
        ]}

    def build(self, **options):
        output = self.directory / "site"
        exporter.export_site(output, store=self.store, frontend=self.frontend, **options)
        return output

    def run_worker(self, output, queries):
        result = subprocess.run(
            [NODE, "--input-type=module", "-e", NODE_RUNNER, str(output), str(ROOT / "frontend" / "static-worker.js")],
            input=json.dumps(queries, ensure_ascii=False), text=True, capture_output=True, timeout=45,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_export_preserves_spans_and_only_whitelisted_public_data(self):
        self.store.records[0]["manager_phone"] = "must-not-export-phone"
        self.store.metadata["private_note"] = "must-not-export-private-note"
        self.store.records[0]["name_variants"]["manager_name"] = "must-not-export-manager"
        with mock.patch.object(server, "map_config", side_effect=AssertionError("public key flag was not set")):
            output = self.build()
        core = json.loads((output / "data/core.json").read_text(encoding="utf-8"))
        self.assertNotIn("manager_phone", core["detail_fields"])
        self.assertNotIn("private_note", core["meta"]["dataset"])
        self.assertFalse((output / ".env.local").exists())
        self.assertEqual(json.loads((output / "map-config.json").read_text())["provider"], "osm")
        self.assertIn('src="./app.js"', (output / "index.html").read_text())
        self.assertEqual(len(core["records"]), len(self.rows))
        for original, packed in zip(self.store.records, core["records"]):
            name = packed[6] if packed[6] is not None else packed[1]
            lengths = packed[5][1::2]
            self.assertEqual(sum(lengths), len(name))
            self.assertEqual(packed[1], original["name"])
        public_text = "".join(path.read_text(encoding="utf-8") for path in output.rglob("*.json"))
        for sentinel in ("must-not-export-phone", "must-not-export-private-note", "must-not-export-manager", "must-not-be-copied"):
            self.assertNotIn(sentinel, public_text)

    def test_public_map_config_requires_explicit_flag_and_has_only_three_fields(self):
        fake = {"provider": "kakao", "kakao_js_key": "public-test-key", "fallback_provider": "osm"}
        with mock.patch.object(server, "map_config", return_value=fake) as read_config:
            output = self.build(include_map_config=True)
        read_config.assert_called_once_with()
        saved = json.loads((output / "map-config.json").read_text())
        self.assertEqual(saved, fake)
        self.assertEqual(json.loads((output / "runtime-config.json").read_text())["mode"], "static")
        self.assertNotIn("public-test-key", (output / "build-manifest.json").read_text())

    def test_details_are_region_shards_and_source_variants_survive(self):
        core, shards, distances = exporter.pack_store(self.store)
        self.assertEqual(len(shards), 2)
        fields = core["detail_fields"]
        shard = shards[core["detail_shards"]["서울특별시"]]
        values = shard["rows"]["fixture-0"]
        self.assertEqual(values[fields.index("name_variants")], self.rows[0]["name_variants"])
        self.assertEqual(len(distances["rows"]), 12)
        for row in distances["rows"]:
            self.assertIsNone(row[2])  # Water was not measured: never export zero.

    @unittest.skipUnless(NODE, "Node.js is required for JavaScript/Python contract parity")
    def test_worker_matches_python_filters_counts_series_ranking_and_rows(self):
        output = self.build()
        queries = [
            {"path": "/api/meta"},
            {"path": "/api/regions", "params": {"level": "sido"}},
            {"path": "/api/regions", "params": {"level": "dong", "sido": "서울특별시", "sigungu": "강남구"}},
            {"path": "/api/analysis", "params": {"tokens": "파크,리버,롯데캐슬,카이저"}},
            {"path": "/api/analysis", "params": {"sido": "서울특별시", "year_from": "2001", "year_to": "2004", "tokens": "파크,리버", "category": "nature"}},
            {"path": "/api/analysis", "params": {"dong": "삼성동", "tokens": "미지별자리"}},
            {"path": "/api/analysis", "params": {"dong": "없는동"}},
            {"path": "/api/tokens", "params": {"category": "unclassified", "token_q": "미지"}},
            {"path": "/api/complexes", "params": {"q": "파크", "limit": "3", "offset": "1"}},
            {"path": "/api/complexes", "params": {"mapped_only": "1", "limit": "5000"}},
            {"path": "/api/complexes", "params": {"q": "🏠"}},
            {"path": "/api/complexes", "params": {"q": "카이저"}},
            {"path": "/api/map.geojson", "params": {"limit": "2", "offset": "1"}},
            {"path": "/api/gis-features", "params": {"feature": "park"}},
        ]
        results = self.run_worker(output, queries)
        routes = {
            "/api/meta": lambda params: self.store.meta(), "/api/regions": self.store.regions,
            "/api/analysis": self.store.analysis, "/api/tokens": self.store.tokens,
            "/api/complexes": self.store.complexes, "/api/map.geojson": self.store.map_geojson,
            "/api/gis-features": self.store.gis_features,
        }
        for query, actual in zip(queries, results):
            with self.subTest(query=query):
                expected = routes[query["path"]](query.get("params", {}))
                self.assertEqual(actual, expected)

    @unittest.skipUnless(NODE, "Node.js is required for JavaScript/Python contract parity")
    def test_numeric_designators_are_hidden_by_both_apis_without_deleting_raw_tokens(self):
        designators = ["1", "2", "1차", "2차", "제3차", "101동", "4단지", "５차", "6블록", "(101동)", "(101동,102동)", "(432-921)", "（１０１동）"]
        names = ["파크" + value for value in designators] + ["1", "e편한세상2차", "브랜드2"]
        self.rows = [{**self.rows[0], "id": f"numeric-{index}", "name": name, "approval_year": 2000} for index, name in enumerate(names)]
        dictionary = {**self.store.dictionary, "entries": [*self.store.dictionary["entries"], {"canonical": "브랜드2", "category": "brand"}]}
        with mock.patch.object(server, "load_dictionary", return_value=dictionary):
            self.store = server.DataStore(records=self.rows)
        # Simulate a legacy snapshot misclassifying an otherwise numeric token.
        for token in self.store.records[7]["tokens"]:
            if token["canonical"] == "5차":
                token["category"] = "unclassified"
        output = self.build()
        queries = [
            {"path": "/api/analysis", "params": {"tokens": ",".join([*designators, "파크", "e편한세상", "브랜드2"])}},
            {"path": "/api/analysis", "params": {"tokens": ",".join(designators)}},
            {"path": "/api/tokens"},
            {"path": "/api/tokens", "params": {"category": "number"}},
            {"path": "/api/tokens", "params": {"token_q": "1차"}},
            {"path": "/api/complexes", "params": {"limit": "100"}},
        ]
        results = self.run_worker(output, queries)
        routes = {"/api/analysis": self.store.analysis, "/api/tokens": self.store.tokens, "/api/complexes": self.store.complexes}
        for query, actual in zip(queries, results):
            with self.subTest(query=query):
                self.assertEqual(actual, routes[query["path"]](query.get("params", {})))
        analysis, numeric_only, tokens, number_tokens, searched_numbers, complexes = results
        self.assertEqual(analysis["selected_tokens"], ["파크", "e편한세상", "브랜드2"])
        self.assertEqual({row["token"] for row in tokens["items"]}, {"파크", "e편한세상", "브랜드2"})
        self.assertEqual(analysis["unique_token_count"], 3)
        self.assertEqual(analysis["sample_count"], len(self.rows))
        self.assertEqual(analysis["series"][0]["cumulative_total"], len(self.rows))
        self.assertEqual(analysis["series"][0]["token_counts"], {"파크": len(designators), "e편한세상": 1, "브랜드2": 1})
        self.assertEqual(analysis["series"][0]["token_shares"]["파크"], len(designators) / len(self.rows))
        self.assertEqual(numeric_only["selected_tokens"], [])
        self.assertEqual(numeric_only["series"][0]["token_counts"], {})
        self.assertEqual(number_tokens["items"], [])
        self.assertEqual(searched_numbers["items"], [])
        self.assertEqual([row["name"] for row in complexes["items"]], names)
        for row in complexes["items"]:
            self.assertEqual("".join(token["surface"] for token in row["tokens"]), server.normalize_name(row["name"]))
        fullwidth = complexes["items"][7]
        self.assertEqual(fullwidth["name"], "파크５차")
        self.assertTrue(any(token["surface"] == "5차" for token in fullwidth["tokens"]))
        core = json.loads((output / "data/core.json").read_text(encoding="utf-8"))
        self.assertTrue(any(core["categories"][token[1]] == "number" for token in core["token_catalog"]))

    @unittest.skipUnless(NODE, "Node.js is required for JavaScript/Python contract parity")
    def test_cleaned_names_survive_detail_shards_without_merging_records(self):
        names = ["그린(101동)", "그린(102동)", "(432-921)", "그린(101동,임대)"]
        self.rows = [{**self.rows[0], "id": f"clean-{index}", "name": name, "approval_year": 2000} for index, name in enumerate(names)]
        self.store = server.DataStore(records=self.rows)
        output = self.build()
        queries = [
            {"path": "/api/complexes", "params": {"limit": "100"}},
            {"path": "/api/analysis", "params": {"tokens": "그린,101동"}},
        ]
        complexes, analysis = self.run_worker(output, queries)
        self.assertEqual(complexes, self.store.complexes({"limit": "100"}))
        self.assertEqual(analysis, self.store.analysis({"tokens": "그린,101동"}))
        self.assertEqual([row["name"] for row in complexes["items"]], names)
        self.assertEqual([row["name_clean"] for row in complexes["items"]], ["그린", "그린", "", "그린(임대)"])
        self.assertEqual(len({row["id"] for row in complexes["items"]}), 4)
        self.assertEqual(analysis["sample_count"], 4)
        self.assertEqual(analysis["series"][0]["token_counts"], {"그린": 3})
        self.assertEqual(analysis["series"][0]["token_shares"]["그린"], 0.75)
        self.assertTrue(any(token["surface"] == "101동" for token in complexes["items"][0]["tokens"]))
        core = json.loads((output / "data/core.json").read_text(encoding="utf-8"))
        self.assertIn("name_clean", core["detail_fields"])

    @unittest.skipUnless(NODE, "Node.js is required for JavaScript/Python contract parity")
    def test_gis_recomputes_filtered_statistics_and_never_reuses_p_values(self):
        output = self.build()
        queries = [
            {"path": "/api/gis", "params": {"feature": "park", "token": "파크", "threshold_m": "100"}},
            {"path": "/api/gis", "params": {"feature": "park", "token": "파크", "year_from": "2002"}},
            {"path": "/api/gis", "params": {"feature": "water", "token": "리버"}},
        ]
        for query, result in zip(queries, self.run_worker(output, queries)):
            with self.subTest(query=query):
                expected = self.store.gis(query["params"])
                self.assertEqual(result["coverage"], expected["coverage"])
                self.assertEqual(result["status"], expected["status"])
                for key in result["summary"]:
                    if key != "permutation_p_value":
                        self.assertEqual(result["summary"][key], expected["summary"][key], key)
                self.assertIsNone(result["summary"]["permutation_p_value"])
                self.assertTrue(any("순열검정 p값을 제공하지 않습니다" in warning for warning in result["warnings"]))


if __name__ == "__main__":
    unittest.main()
