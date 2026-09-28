#!/usr/bin/env python3
"""Cache a bounded public OSM extract; derive distances only at known coordinates.

Requires shapely and pyproj for this offline collection step. The web server
uses only Python's standard library. Never called automatically by the UI.
"""
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess

ROOT=Path(__file__).resolve().parents[1]
RAW=ROOT/'data'/'raw'
OUT=ROOT/'data'/'processed'
BBOX=(37.35,126.70,37.75,127.25)
ENDPOINT='https://overpass-api.de/api/interpreter'


def download():
    bbox=','.join(map(str,BBOX))
    selectors=['nwr["leisure"="park"]','nwr["natural"="water"]','way["waterway"="river"]',
        'nwr["landuse"="forest"]','nwr["natural"="wood"]','nwr["amenity"="school"]',
        'nwr["railway"="station"]["station"="subway"]','node["railway"="subway_entrance"]']
    query='[out:json][timeout:120];('+''.join(s+'('+bbox+');' for s in selectors)+');out geom;'
    RAW.mkdir(parents=True,exist_ok=True)
    q=RAW/'osm_seoul_features.query.txt'
    q.write_text(query,encoding='utf-8')
    target=RAW/'osm_seoul_features.json'
    if target.exists():
        print('Cached OSM extract retained:',target)
        return
    subprocess.run(['/usr/bin/curl','--fail','--silent','--show-error','--max-time','180',
        '--user-agent','ApartmentNameAtlas/0.1 (local academic exploration)',
        '--data-urlencode','data@'+str(q),ENDPOINT,'-o',str(target)+'.tmp'],check=True)
    data=json.loads(Path(str(target)+'.tmp').read_text())
    if data.get('remark') or not data.get('elements'):
        raise RuntimeError('Incomplete OSM response; do not use: '+str(data.get('remark')))
    Path(str(target)+'.tmp').replace(target)
    print('Downloaded OSM elements:',len(data['elements']))


def features():
    from shapely.geometry import Point,LineString,Polygon
    from shapely.ops import unary_union,polygonize
    data=json.loads((RAW/'osm_seoul_features.json').read_text())
    result=defaultdict(list)
    for e in data['elements']:
        t=e.get('tags',{})
        categories=[]
        if t.get('leisure')=='park': categories.append('park')
        if t.get('natural')=='water' or t.get('waterway')=='river': categories.append('water')
        if t.get('natural')=='wood' or t.get('landuse')=='forest': categories.append('forest')
        if t.get('amenity')=='school': categories.append('school')
        if t.get('railway')=='subway_entrance' or (t.get('railway')=='station' and t.get('station')=='subway'): categories.append('metro')
        if not categories: continue
        g=None
        if e['type']=='node':
            g=Point(e['lon'],e['lat'])
        elif e['type']=='way' and e.get('geometry'):
            xy=[(p['lon'],p['lat']) for p in e['geometry'] if 'lon' in p]
            if len(xy)<2: continue
            g=Polygon(xy) if len(xy)>=4 and xy[0]==xy[-1] and t.get('waterway')!='river' else LineString(xy)
        elif e['type']=='relation':
            rings=defaultdict(list)
            for m in e.get('members',[]):
                xy=[(p['lon'],p['lat']) for p in m.get('geometry',[]) if 'lon' in p]
                if len(xy)>=2: rings[m.get('role','outer') or 'outer'].append(LineString(xy))
            if rings['outer']:
                outer=list(polygonize(unary_union(rings['outer'])))
                if outer:
                    g=unary_union(outer)
                    inner=list(polygonize(unary_union(rings['inner']))) if rings['inner'] else []
                    if inner: g=g.difference(unary_union(inner))
        if g is None or g.is_empty: continue
        if not g.is_valid: g=g.buffer(0)
        if g.is_empty: continue
        for c in categories: result[c].append((e,g))
    return data,result


def measure():
    from shapely.geometry import Point,box,mapping
    from shapely.ops import transform
    from shapely.strtree import STRtree
    from pyproj import Transformer
    data,groups=features()
    project=Transformer.from_crs('EPSG:4326','EPSG:5179',always_xy=True).transform
    boundary=transform(project,box(BBOX[1],BBOX[0],BBOX[3],BBOX[2])).boundary
    source_bounds=box(BBOX[1],BBOX[0],BBOX[3],BBOX[2])
    trees={c:STRtree([transform(project,g) for e,g in vals]) for c,vals in groups.items()}
    records=[json.loads(line) for line in (OUT/'complexes.jsonl').read_text().splitlines() if line.strip()]
    results=[]
    for r in records:
        lat,lon=r.get('lat'),r.get('lon')
        if lat is None or lon is None: continue
        point=Point(lon,lat)
        if not source_bounds.contains(point): continue
        point=transform(project,point)
        safe_limit=point.distance(boundary)
        for c,tree in trees.items():
            index=tree.nearest(point)
            distance=point.distance(tree.geometries[index])
            if distance>=safe_limit: continue
            e=groups[c][index][0]
            results.append({'id':r['id'],'feature':c,'distance_m':round(distance,2),
                'feature_name':e.get('tags',{}).get('name'),
                'feature_source_url':f"https://www.openstreetmap.org/{e['type']}/{e['id']}",
                'source_url':ENDPOINT,'osm_timestamp':data.get('osm3s',{}).get('timestamp_osm_base'),
                'method':'EPSG:5179 representative apartment point to nearest recorded geometry; within polygon=0',
                'coordinate_source':r.get('coordinate_source_url')})
    OUT.mkdir(exist_ok=True)
    (OUT/'gis_distances.jsonl').write_text(''.join(json.dumps(x,ensure_ascii=False)+'\n' for x in results),encoding='utf-8')
    # The original geometry remains visible and inspectable on the map.
    geo={'type':'FeatureCollection','features':[
        {'type':'Feature','geometry':mapping(g),'properties':{'feature':c,'name':e.get('tags',{}).get('name',''),
         'source_url':f"https://www.openstreetmap.org/{e['type']}/{e['id']}"}}
        for c,vals in groups.items() for e,g in vals]}
    (OUT/'gis_features.geojson').write_text(json.dumps(geo,ensure_ascii=False),encoding='utf-8')
    report={'bbox':BBOX,'downloaded_at':datetime.now(timezone.utc).isoformat(),
        'osm_timestamp':data.get('osm3s',{}).get('timestamp_osm_base'),
        'feature_counts':{c:len(v) for c,v in groups.items()},
        'measured_complexes':len({r['id'] for r in results}),
        'distance_counts':dict(Counter(r['feature'] for r in results)),
        'source_url':ENDPOINT,'license':'OpenStreetMap contributors, ODbL',
        'limitations':['서울권 지형 추출 범위 내 좌표 매칭 표본만 측정','현재 OSM에 기록된 시설·지형; 완전한 전수목록 아님',
          'water는 호수·연못·강 수면과 강 중심선을 포함; 리버 전용 강변 지표가 아님',
          '직선거리이며 접근로·출입구·조망·명명 당시 환경을 나타내지 않음',
          '추출 범위 경계보다 가까운 지형이 발견된 경우만 측정치를 보존']}
    (OUT/'gis_meta.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--download',action='store_true')
    parser.add_argument('--measure',action='store_true')
    args=parser.parse_args()
    if args.download: download()
    if args.measure: measure()
