import importlib.util
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("apartment_server", ROOT / "backend" / "server.py")
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)


class BackendTests(unittest.TestCase):
    def setUp(self):
        common = {"is_demo": False, "source_url": "https://example.org/test-fixtures", "dong_code": "fixture", "lat": None, "lon": None}
        self.rows = [
            {**common, "id": "a", "name": "파크파크아파트", "sido": "서울특별시", "sigungu": "강남구", "dong": "역삼동", "approval_year": 2000, "lat": 37.5, "lon": 127.0},
            {**common, "id": "b", "name": "리버아파트", "sido": "서울특별시", "sigungu": "강남구", "dong": "역삼동", "approval_year": 2002},
            {**common, "id": "c", "name": "파크아파트", "sido": "서울특별시", "sigungu": "강남구", "dong": "삼성동", "approval_year": None},
            {**common, "id": "d", "name": "화명카이저롯데캐슬", "sido": "부산광역시", "sigungu": "북구", "dong": "화명동", "approval_year": 2000},
        ]
        self.store = server.DataStore(records=self.rows)

    def test_region_cascade_and_shared_filters(self):
        regions = self.store.regions({"level": "dong", "sido": "서울특별시", "sigungu": "강남구"})
        self.assertEqual({entry["value"]: entry["count"] for entry in regions["options"]}, {"역삼동": 2, "삼성동": 1})
        query = {"sido": "서울특별시", "sigungu": "강남구", "dong": "역삼동", "year_from": "2001"}
        self.assertEqual(self.store.analysis(query)["sample_count"], 1)
        self.assertEqual([row["id"] for row in self.store.complexes(query)["items"]], ["b"])
        self.assertEqual(self.store.map_geojson(query)["sample_count"], 1)

    def test_tokens_do_not_filter_denominator_and_repetition_counts_once(self):
        result = self.store.analysis({"sido": "서울특별시", "tokens": "파크"})
        self.assertEqual(result["sample_count"], 3)
        self.assertEqual(result["valid_year_count"], 2)
        self.assertEqual(result["series"][0]["token_counts"]["파크"], 1)
        self.assertEqual(result["series"][-1]["total_count"], 1)
        self.assertEqual(result["series"][-1]["token_counts"]["파크"], 0)
        self.assertEqual(result["series"][-1]["token_shares"]["파크"], 0)
        self.assertEqual(result["series"][-1]["cumulative_total"], 2)

    def test_zero_denominator_is_null_and_cumulative_is_monotonic(self):
        result = self.store.analysis({"sido": "서울특별시", "tokens": "파크,리버"})
        middle = next(row for row in result["series"] if row["year"] == 2001)
        self.assertEqual(middle["total_count"], 0)
        self.assertIsNone(middle["token_shares"]["파크"])
        for token in ("파크", "리버"):
            values = [row["token_cumulative"][token] for row in result["series"]]
            self.assertEqual(values, sorted(values))
        self.assertEqual(result["series"][-1]["token_cumulative"], {"파크": 1, "리버": 1})

    def test_missing_years_and_empty_data_never_fabricate_temporal_stats(self):
        result = self.store.analysis({"dong": "삼성동"})
        self.assertEqual(result["sample_count"], 1)
        self.assertEqual(result["series"], [])
        self.assertIsNone(result["year_min"])
        ranged = self.store.analysis({"dong": "삼성동", "year_from": "2000"})
        self.assertEqual(ranged["sample_count"], 0)
        self.assertEqual(ranged["total_count"], 1)
        empty = server.DataStore(records=[])
        self.assertEqual(empty.meta()["dataset"]["status"], "empty")
        self.assertEqual(empty.analysis({})["series"], [])

    def test_pagination_is_explicit_and_query_does_not_change_original_names(self):
        first = self.store.complexes({"limit": "2"})
        second = self.store.complexes({"limit": "2", "offset": str(first["next_offset"])})
        self.assertTrue(first["truncated"])
        self.assertFalse(second["truncated"])
        self.assertEqual(first["total"], 4)
        self.assertEqual({row["id"] for row in first["items"] + second["items"]}, {"a", "b", "c", "d"})
        self.assertEqual(self.store.complexes({"q": "리버"})["items"][0]["name"], "리버아파트")
        self.assertFalse(any(key.startswith("_") for key in first["items"][0]))
        with self.assertRaises(server.RequestError):
            self.store.complexes({"limit": "5001"})
        mapped = self.store.complexes({"mapped_only": "1", "limit": "5000"})
        self.assertEqual(mapped["total"], 1)
        self.assertEqual(mapped["items"][0]["id"], "a")

    def test_real_server_rejects_demo_and_duplicate_id(self):
        with self.assertRaisesRegex(ValueError, "is_demo=false"):
            server.DataStore(records=[{**self.rows[0], "is_demo": True}])
        with self.assertRaisesRegex(ValueError, "중복"):
            server.DataStore(records=[self.rows[0], self.rows[0]])

    def test_address_candidates_preserve_brand_and_name_reconstruction(self):
        record = {**self.rows[0], "name": "역삼롯데캐슬", "dong": "역삼동"}
        normalized, tokens = server.tokenize_with_address(record, self.store.dictionary)
        self.assertEqual("".join(token["surface"] for token in tokens), normalized)
        self.assertEqual([token["canonical"] for token in tokens], ["역삼", "롯데캐슬"])
        self.assertEqual(tokens[0]["category"], "place")
        self.assertTrue(tokens[0]["needs_review"])
        self.assertTrue(tokens[0]["address_match"])
        self.assertEqual(tokens[1]["corporate_group"], "롯데")
        record["dong"] = "롯데캐슬동"
        _, tokens = server.tokenize_with_address(record, self.store.dictionary)
        self.assertEqual(tokens[-1]["category"], "brand")
        record["name"] = "화명롯데캐슬"
        _, tokens = server.tokenize_with_address(record, self.store.dictionary)
        self.assertFalse(tokens[0]["address_match"])

    def test_metrics_coordinates_and_filter_errors_are_explicit(self):
        result = self.store.analysis({})
        self.assertEqual(result["brand_count"], 1)
        self.assertEqual(result["mapped_count"], 1)
        self.assertGreater(result["unique_token_count"], 0)
        geojson = self.store.map_geojson({})
        self.assertEqual(geojson["mapped_count"], 1)
        self.assertEqual(geojson["features"][0]["geometry"]["coordinates"], [127.0, 37.5])
        with self.assertRaises(server.RequestError):
            self.store.analysis({"year_from": "2020", "year_to": "2000"})
        with self.assertRaises(server.RequestError):
            self.store.regions({"level": "invalid"})

    def test_residual_name_candidates_are_searchable_before_top_100_cutoff(self):
        rows = [{**self.rows[0], "id": f"unknown-{index}", "name": "고유명" + chr(0xAC00 + index) + "!"} for index in range(105)]
        store = server.DataStore(records=rows)
        result = store.tokens({"category": "unclassified", "token_q": chr(0xAC00 + 104)})
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["items"][0]["count"], 1)
        token = result["items"][0]["token"]
        trend = store.analysis({"tokens": token})
        self.assertEqual(trend["series"][0]["token_counts"][token], 1)
        self.assertTrue(result["items"][0]["provisional"])
        self.assertFalse(server.countable_token({"category": "unclassified", "canonical": "·-()"}))

    def test_address_inner_substring_is_not_a_place_but_prefix_still_works(self):
        record = {**self.rows[0], "name": "세종로대우", "sigungu": "종로구", "dong": "내수동"}
        normalized, tokens = server.tokenize_with_address(record, self.store.dictionary)
        self.assertEqual([token["surface"] for token in tokens], ["세종로", "대우"])
        self.assertEqual(tokens[0]["category"], "unclassified")
        self.assertEqual("".join(token["surface"] for token in tokens), normalized)
        for name in ("종로롯데캐슬", "롯데캐슬종로"):
            with self.subTest(name=name):
                record["name"] = name
                normalized, tokens = server.tokenize_with_address(record, self.store.dictionary)
                place = next(token for token in tokens if token["surface"] == "종로")
                self.assertEqual(place["category"], "place")
                self.assertTrue(place["address_match"])
                self.assertEqual(sum(token["category"] == "brand" for token in tokens), 1)
                self.assertEqual("".join(token["surface"] for token in tokens), normalized)

    def test_map_config_exposes_only_public_key_with_environment_priority(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env.local"
            missing = server.map_config(path, environ={})
            self.assertEqual(missing, {"provider": "osm", "kakao_js_key": None, "fallback_provider": "osm"})
            path.write_text('UNRELATED_PRIVATE_SETTING=do-not-return\nKAKAO_MAP_JS_KEY="public-test-key"\n', encoding="utf-8")
            configured = server.map_config(path, environ={})
            self.assertEqual(set(configured), {"provider", "kakao_js_key", "fallback_provider"})
            self.assertEqual(configured["kakao_js_key"], "public-test-key")
            self.assertEqual(server.map_config(path, environ={"KAKAO_MAP_JS_KEY": "environment-test-key"})["kakao_js_key"], "environment-test-key")
            self.assertEqual(server.map_config(path, environ={"KAKAO_MAP_JS_KEY": ""})["provider"], "osm")


if __name__ == "__main__":
    unittest.main()
