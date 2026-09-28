# 로컬 웹 API

Python 3.10 이상, 표준 라이브러리만 사용한다. 프로젝트 루트에서 실행한다.

```bash
python3 backend/server.py --host 127.0.0.1 --port 0
```

`--port 0`은 비어 있는 포트를 자동 배정한다. 출력된 주소로 접속한다. 특정 포트를 쓰려면 `--port 8765`처럼 지정하되, 기존 앱의 포트를 점유하거나 종료하지 않는다. 기본 호스트는 로컬 전용 `127.0.0.1`이다. 인증 없는 로컬 탐색 서버이며 인터넷 공개 배포용 구성이 아니다.

프런트엔드와 API를 같은 서버가 제공한다. `/`는 `frontend/index.html`, `/vendor/`는 로컬 지도 라이브러리를 제공한다. 데이터 디렉터리와 프로젝트 문서는 웹 정적 파일로 공개하지 않는다.

## 자료 로딩

기본 입력 디렉터리는 `data/processed/`이며 `--data-dir`로 바꿀 수 있다.

| 파일 | 역할 |
| --- | --- |
| `complexes.jsonl` | 단지별 현재 명칭·지역·사용승인연도·좌표·출처 |
| `dataset_meta.json` | 자료원·수집일·포함 범위·검수 정보 |
| `gis_distances.jsonl` | 단지 ID별 공원·물·숲·학교·철도역 거리 |
| `gis_features.geojson` | 지도에 겹쳐 그릴 지형·시설; `properties.feature`로 분류 |

입력 파일과 `dictionaries/tokens.v1.json`의 변경 시간·크기가 달라지면 다음 API 요청에서 다시 읽는다. Python 코드 변경 후에는 서버를 재시작한다. `backend/gis.py`는 GIS 요청 때 읽는다.

웹 입력 행의 주요 필드는 `id`, `name`, `sido`, `sigungu`, `dong`, `dong_code`, `approval_year`, `lat`, `lon`, `source_url`, `is_demo`다. ID는 고유 문자열이다. 실자료 서버는 `is_demo=false`가 명시된 행만 허용한다. 기타 원문·출처 필드는 보존한다. 사용승인연도와 좌표는 `null`일 수 있다. 좌표가 유효하지 않으면 위도·경도를 모두 `null`로 처리하며 `(0,0)`을 단지 위치로 쓰지 않는다.

자료가 없으면 빈 결과와 `dataset.status="empty"`를 반환한다. 데모로 채우지 않는다. 중복 ID·데모 혼입·잘못된 자료는 오류로 알린다.

## 공통 필터와 분모

`sido`, `sigungu`, `dong`은 정확한 문자열 일치이며 모두 동시에 적용한다. `year_from`, `year_to`는 양 끝을 포함한 사용승인연도 범위다.

- 연도 범위가 없으면 연도 미상 단지도 표본·목록·토큰 순위에 남지만 시계열에서 제외한다.
- 연도 범위를 지정하면 연도 미상 단지는 표본에서도 제외한다.
- 그래프의 `tokens`는 비교할 토큰 선택이다. 선택 토큰을 포함한 단지만 남기는 모집단 필터가 아니다.
- 한 단지 안에서 같은 토큰이 여러 번 나와도 해당 토큰의 단지 수는 한 번 센다.
- 누적 수는 선택한 연도 범위 안에서만 누적한다.
- 연도별 단지가 0개인 해의 `token_shares`는 `null`이다. 자료가 전혀 없는 범위는 빈 `series`를 반환한다.
- 현재 이름을 사용승인연도와 비교하는 API다. 명명연도나 이름 변경 이력을 복원한 결과가 아니다.

## 엔드포인트

### `GET /api/map-config`

`{provider,kakao_js_key,fallback_provider}`를 반환한다. 지도용 공개 JavaScript 키가 있으면 `provider="kakao"`, 없으면 `provider="osm"`이며 대체 제공자는 OSM이다. `KAKAO_MAP_JS_KEY` 환경변수가 우선하고, 정의되어 있지 않으면 프로젝트의 `.env.local`에서 같은 항목만 읽는다. 명시적으로 빈 환경변수는 Kakao 사용을 해제한다. 파일을 셸 명령으로 실행하지 않으며 다른 환경변수나 설정은 응답에 포함하지 않는다. JavaScript 키는 브라우저 SDK에서 쓰이는 공개용 키다.

### `GET /api/meta`

`dataset`, `taxonomy`, `dictionary_version`, `geo_status`, `geo_feature_count`, `demo=false`를 반환한다. `dataset`에는 `name`, `status`(`real`/`empty`), `source_url`, `downloaded_at`, `record_count`, `coordinate_count`, `year_min`, `year_max`, `coverage_note`와 자료원 메타데이터가 담긴다.

`geo_status`는 거리 자료가 있으면 `ready`, 좌표만 있으면 `coordinates_only`, 둘 다 없으면 `missing_coordinates`다. 전체 연결 상태이며 특정 지역·토큰에 충분한 비교 표본이 있다는 뜻은 아니다.

### `GET /api/regions?level=sido|sigungu|dong&sido=&sigungu=`

`{level, options:[{value,label,count}]}`를 반환한다. 시군구 목록에는 시도 필터, 동 목록에는 시도·시군구 필터가 적용된다. `dong`은 원자료 주소의 법정동·읍면리 문자열이며 행정동과 동일하다고 가정하지 않는다.

### `GET /api/analysis?sido=&sigungu=&dong=&year_from=&year_to=&tokens=리버,파크`

| 필드 | 의미 |
| --- | --- |
| `sample_count` | 지역·연도 필터를 모두 적용한 단지 수 |
| `total_count` | 지역 필터 후, 연도 필터 적용 전 단지 수 |
| `dataset_count` | 전체 연결 단지 수 |
| `valid_year_count` | 현재 표본 중 사용승인연도가 유효한 단지 수 |
| `mapped_count` | 현재 표본 중 유효한 좌표가 있는 단지 수 |
| `brand_count` | 현재 표본 중 브랜드 토큰을 하나 이상 가진 단지 수 |
| `unique_token_count` | 현재 표본의 서로 다른 canonical 토큰 수 |
| `place_name_count` | 지명 후보 토큰이 하나 이상 있는 단지 수 |
| `place_local_match_count` | 주소명 문자열과 일치하는 지명 후보가 하나 이상 있는 단지 수 |
| `year_min`, `year_max` | 현재 표본의 유효 사용승인연도 최솟값·최댓값; 없으면 `null` |

`series`의 각 행은 `{year,total_count,cumulative_total,token_counts,token_cumulative,token_shares}`다. 여기서 행의 `total_count`는 그 해의 유효 단지 수다. 토큰별 객체의 키는 canonical 문자열이다. 토큰 선택은 최대 30개다.

`top_tokens`는 최대 100개이며 항목은 `{token,category,count,share,provisional}`다. 이 목록의 `share` 분모는 `sample_count`이고, 시계열의 분모와 다를 수 있다. `category`, `token_q`를 추가하면 **순위를 100개로 자르기 전에** 목록만 필터링한다. 시계열과 표본은 그대로 유지한다. `ranked_token_count`는 해당 순위 검색에 일치한 전체 토큰 종류 수다.

### `GET /api/tokens?...&category=unclassified&token_q=한빛`

같은 지역·연도 필터로 토큰을 검색한다. `{total,items,sample_count,truncated,demo}`를 반환하며 `items`는 분석 API의 토큰 순위 항목과 같다. 최대 100개다. 낮은 순위의 토큰도 검색어·범주로 찾을 수 있다.

### `GET /api/complexes?...&q=&limit=100&offset=0&mapped_only=0`

단지 원문과 분절을 조회한다. `q`는 정규화 이름 또는 ID 부분 검색이다. `mapped_only=1`이면 유효한 좌표가 있는 단지만 남긴다. `limit`은 1~5,000이다.

응답은 `{total,items,limit,offset,truncated,next_offset,demo}`다. `truncated`는 현재 페이지 뒤에 더 많은 결과가 있는지 표시한다. 전체 내려받기는 `next_offset`이 `null`이 될 때까지 페이지를 읽는다. 지도용 예시는 `/api/complexes?mapped_only=1&limit=5000`이다.

각 단지의 `tokens`에는 `surface`, `canonical`, `category`, `start`, `end`, `provisional`, `needs_review`, `review_reasons`가 있다. 위치는 `name_normalized`의 Unicode 글자 인덱스이며 `end`는 해당 글자를 포함하지 않는다. 원문 `name`은 보존한다.

- `롯데캐슬`은 브랜드 토큰 하나이며 `corporate_group="롯데"` 관계를 가진다. 롯데를 표면 토큰으로 다시 세지 않는다.
- 사용자 분류에 따라 `카이저`도 브랜드다. 미확인 모기업·상하위 브랜드 관계는 `null`이다.
- 브랜드 등 사전 항목을 먼저 분절한 뒤, 남은 조각 **맨 앞**의 주소명 후보만 지명으로 추가한다. `세종로` 안의 `종로`처럼 중간 부분이 우연히 일치하는 경우는 나누지 않는다.
- 지명 토큰의 `address_match`는 정규화 주소명과의 문자열 일치 여부다. 비교할 주소 후보가 없으면 `null`이다. `true`여도 해당 이름의 실제 유래나 지리적 의미가 검증되었다는 뜻은 아니다.
- `unclassified`는 개별 명칭 후보이므로 순수 구두점을 제외하고 토큰 집계에 포함한다. 검토 필요 상태는 유지한다.

### `GET /api/map.geojson?...&limit=2000&offset=0`

같은 지역·연도 필터에 해당하고 좌표가 있는 단지를 GeoJSON Point로 반환한다. `sample_count`, `mapped_count`, `limit`, `offset`, `truncated`, `next_offset`을 함께 제공한다. GeoJSON 좌표 순서는 `[경도,위도]`다.

### `GET /api/gis?...&token=파크&feature=park&threshold_m=500`

현재 표본에서 토큰 포함·미포함 단지의 입지 거리를 비교한다. `feature`는 `park`, `water`, `forest`, `school`, `metro`이며 `threshold_m`은 50~10,000m다.

반환값에는 `status`, `message`, `summary`, `coverage`, `warnings`, `method`가 있다. `summary`에는 비교군별 유효 표본 수·거리 중앙값·임계거리 이내 비율과 가능한 경우 시군구·사용승인연대를 맞춘 순열검정 결과가 있다. 미측정 거리는 0m나 비인접으로 바꾸지 않는다. 상세 해석은 반환되는 경고와 GIS 문서를 따른다.

### `GET /api/gis-features?feature=park`

해당 유형의 GeoJSON 지형·시설을 반환한다. `feature=all` 또는 생략 시 전체 유형이다. 이 경로는 수집한 지형 자료의 전체 범위를 제공하며 단지 지역 필터로 공간을 잘라내지 않는다.

## 오류와 검증

잘못된 API 필터는 HTTP 400, 없는 경로는 404, 데이터 읽기·검증 오류는 503을 반환한다. 프로젝트 바깥으로 나가는 정적 파일 경로는 403으로 차단한다. JSON 응답은 UTF-8이며 비정상 부동소수점 값을 출력하지 않는다.

```bash
python3 -m unittest discover -s tests -v
```

테스트는 분절 보존, 브랜드 중복 방지, 주소 중간문자 오분절, 데모·중복 ID 차단, 지역 필터, 연도 결측, 누적 수, 빈 분모, 페이지네이션, 잔여명칭 검색 및 GIS 비교를 확인한다.
