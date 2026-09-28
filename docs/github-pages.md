# GitHub Pages 배포

목표 주소는 `https://gahyoun.github.io/apartment-name-atlas/`다. 카카오 JavaScript SDK에 등록할 출처는 경로를 제외한 `https://gahyoun.github.io`다.

이 프로젝트에는 두 실행 방식이 있다.

| 실행 방식 | 집계 | GIS 통계 | 실행 위치 |
|---|---|---|---|
| Python API | 선택 지역·기간에 따라 계산 | 거리 요약 + 조건부 순열검정 | `bash run.sh` |
| GitHub Pages | 같은 분절 스냅샷으로 브라우저 Worker가 계산 | 선택 조건별 거리 요약; p값은 제공하지 않음 | `site/` 내보내기 |

GitHub Pages는 Python 서버를 실행하지 않으므로 원자료를 그대로 API에 요청하는 대신, 필요한 공개 필드와 분절 결과를 정적 파일로 내보낸다. 이름 상세와 지형 도형은 필요할 때 가져온다. 사용자의 필터와 검색어는 별도 분석 서버에 저장하지 않는다. 지도 SDK와 지도 타일은 각 지도 제공자에게 요청한다.

## 스냅샷 만들기

먼저 `data/processed/`의 실제 자료를 수집하고 사전을 검수한다. 이어서 실행한다.

```bash
python3 scripts/build_report.py
python3 scripts/export_site.py --output-dir site --include-map-config
python3 -m unittest discover -s tests -v
python3 -m http.server 8080 --directory site
```

`--include-map-config`는 지정된 **브라우저용 카카오 JavaScript 키만** 내보낸다. `.env.local` 파일이나 다른 환경 변수는 복사하지 않는다. 이 옵션을 생략하면 OpenStreetMap 배경 지도로 배포할 수 있다. `.env.local`은 Git 제외 대상이다.

## 저장소와 게시 브랜치

`main`에는 소스와 `site/` 스냅샷을 저장하고, `gh-pages`에는 `site/` 내용만 배포한다. 원본 CSV, 개발 중간 파일, 로컬 환경 파일은 저장소에 포함하지 않는다. 배포 상태와 실제 카카오맵은 게시 주소에서 확인한다.

```bash
git add .
git commit -m "Update apartment name atlas"
git push origin main
git subtree split --prefix site -b pages-release
git push origin pages-release:gh-pages
git branch -D pages-release
```

이 명령은 별도 작업 중인 `pages-release` 브랜치가 없을 때 사용한다. GitHub Pages 설정은 `gh-pages` 브랜치의 `/` 폴더를 게시 원본으로 지정한다. `.nojekyll`이 일반 정적 파일 배포를 보장한다. 세부 절차는 [GitHub 공식 문서](https://docs.github.com/en/pages/getting-started-with-github-pages/creating-a-github-pages-site)를 따른다.

자료가 갱신되면 분절, GIS 거리, 보고서, 정적 스냅샷을 함께 재생성한다. 사전 버전과 자료 수집일이 다른 산출물을 섞지 않는다.
