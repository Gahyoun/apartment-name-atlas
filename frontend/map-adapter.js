// Display providers share the same verified coordinates and GIS geometry.
// Kakao's browser JavaScript key is delivered by /api/map-config at runtime.
const KAKAO_TIMEOUT_MS = 8000;
let kakaoSDKPromise;

const tokenOf = (token) => token.canonical || token.surface || token.token || '';
const isHighlighted = (item, selected) => (item.tokens || []).some((token) => selected.includes(tokenOf(token)));
const popupContent = (item) => {
  const content = document.createElement('div');
  content.className = 'map-info-window';
  const title = document.createElement('strong');
  title.textContent = item.name || '';
  const description = document.createElement('p');
  description.textContent = [item.sigungu, item.dong, item.approval_year ? `${item.approval_year}년` : ''].filter(Boolean).join(' · ');
  content.append(title, description);
  return content;
};

function loadKakaoSDK(key) {
  if (window.kakao?.maps?.Map && window.kakao?.maps?.MarkerClusterer) return Promise.resolve(window.kakao.maps);
  if (kakaoSDKPromise) return kakaoSDKPromise;
  kakaoSDKPromise = new Promise((resolve, reject) => {
    const script = document.createElement('script');
    let settled = false;
    const finish = (success) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      script.onload = null;
      script.onerror = null;
      if (success) resolve(window.kakao.maps);
      else {
        script.remove();
        reject(new Error('Kakao SDK unavailable'));
      }
    };
    const timer = setTimeout(() => finish(false), KAKAO_TIMEOUT_MS);
    const url = new URL('https://dapi.kakao.com/v2/maps/sdk.js');
    url.searchParams.set('appkey', key);
    url.searchParams.set('autoload', 'false');
    url.searchParams.set('libraries', 'clusterer');
    script.src = url.href;
    script.async = true;
    script.dataset.mapSdk = 'kakao';
    script.onerror = () => finish(false);
    script.onload = () => {
      if (!window.kakao?.maps?.load) { finish(false); return; }
      try {
        window.kakao.maps.load(() => finish(Boolean(window.kakao?.maps?.Map && window.kakao?.maps?.MarkerClusterer)));
      } catch { finish(false); }
    };
    document.head.append(script);
  });
  return kakaoSDKPromise;
}

// Preserve every polygon ring: GeoJSON uses [longitude, latitude], whereas
// Kakao takes LatLng(latitude, longitude). A polygon's inner rings are holes.
export function geometryToKakaoOverlays(geometry, maps, style = {}) {
  if (!geometry) return [];
  const point = (coordinates) => {
    if (!Array.isArray(coordinates) || !Number.isFinite(coordinates[0]) || !Number.isFinite(coordinates[1])) return null;
    if (Math.abs(coordinates[0]) > 180 || Math.abs(coordinates[1]) > 90) return null;
    return new maps.LatLng(coordinates[1], coordinates[0]);
  };
  const path = (coordinates) => (coordinates || []).map(point).filter(Boolean);
  const polygon = (coordinates) => {
    const rings = (coordinates || []).map(path).filter((ring) => ring.length >= 3);
    return rings.length ? [new maps.Polygon({ ...style, path: rings })] : [];
  };
  const line = (coordinates) => {
    const points = path(coordinates);
    return points.length >= 2 ? [new maps.Polyline({ ...style, path: points })] : [];
  };
  const circle = (coordinates) => {
    const center = point(coordinates);
    return center ? [new maps.Circle({ ...style, center, radius: 16 })] : [];
  };
  switch (geometry.type) {
    case 'Point': return circle(geometry.coordinates);
    case 'MultiPoint': return (geometry.coordinates || []).flatMap(circle);
    case 'LineString': return line(geometry.coordinates);
    case 'MultiLineString': return (geometry.coordinates || []).flatMap(line);
    case 'Polygon': return polygon(geometry.coordinates);
    case 'MultiPolygon': return (geometry.coordinates || []).flatMap(polygon);
    case 'GeometryCollection': return (geometry.geometries || []).flatMap((child) => geometryToKakaoOverlays(child, maps, style));
    default: return [];
  }
}

class KakaoMapAdapter {
  constructor(container, maps, onSelect) {
    this.provider = 'kakao';
    this.maps = maps;
    this.container = container;
    this.onSelect = onSelect;
    this.items = [];
    this.markers = new Map();
    this.features = [];
    this.pointImages = new Map();
    container.replaceChildren();
    this.map = new maps.Map(container, { center: new maps.LatLng(37.5665, 126.9780), level: 8 });
    this.map.addControl(new maps.ZoomControl(), maps.ControlPosition.RIGHT);
    this.map.setKeyboardShortcuts(true);
    this.map.setZoomable(false);
    this.onFocus = () => this.map.setZoomable(true);
    this.onBlur = () => this.map.setZoomable(false);
    container.addEventListener('focus', this.onFocus);
    container.addEventListener('blur', this.onBlur);
    this.clusterer = new maps.MarkerClusterer({
      map: this.map,
      averageCenter: true,
      minLevel: 6,
      gridSize: 52,
      minClusterSize: 2,
      disableClickZoom: false,
      styles: [{ width: '42px', height: '42px', background: '#ecf2fe', border: '1px solid #346fb2', borderRadius: '50%', color: '#063a74', textAlign: 'center', lineHeight: '40px', fontWeight: '700', fontSize: '13px' }],
    });
    this.infoWindow = new maps.InfoWindow({ removable: true, zIndex: 10 });
    this.featureLabel = new maps.CustomOverlay({ yAnchor: 1.35, zIndex: 20 });
  }

  image(highlighted) {
    if (this.pointImages.has(highlighted)) return this.pointImages.get(highlighted);
    const color = highlighted ? '#1c589c' : '#58616a';
    const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 18 18"><circle cx="9" cy="9" r="${highlighted ? 7 : 5}" fill="${color}" fill-opacity="${highlighted ? '.94' : '.76'}" stroke="white" stroke-width="1.5"/></svg>`;
    const image = new this.maps.MarkerImage(`data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`, new this.maps.Size(18, 18), { offset: new this.maps.Point(9, 9) });
    this.pointImages.set(highlighted, image);
    return image;
  }

  setPoints(items, selected) {
    this.infoWindow.close();
    this.clusterer.clear();
    this.markers.clear();
    this.items = items;
    const markers = items.map((item) => {
      const marker = new this.maps.Marker({
        position: new this.maps.LatLng(item.lat, item.lon),
        title: item.name,
        image: this.image(isHighlighted(item, selected)),
      });
      this.maps.event.addListener(marker, 'click', () => {
        this.onSelect(item);
        this.openInfo(item, marker);
      });
      this.markers.set(item.id, marker);
      return marker;
    });
    this.clusterer.addMarkers(markers);
  }

  updateSelection(selected) {
    this.items.forEach((item) => this.markers.get(item.id)?.setImage(this.image(isHighlighted(item, selected))));
  }

  openInfo(item, marker) {
    this.infoWindow.setContent(popupContent(item));
    this.infoWindow.open(this.map, marker);
  }

  showItem(item) {
    this.map.setLevel(3);
    this.map.setCenter(new this.maps.LatLng(item.lat, item.lon));
    const marker = this.markers.get(item.id);
    if (marker) this.openInfo(item, marker);
  }

  fitPoints() {
    if (!this.items.length) return;
    if (this.items.length === 1) {
      this.map.setLevel(3);
      this.map.setCenter(new this.maps.LatLng(this.items[0].lat, this.items[0].lon));
      return;
    }
    const bounds = new this.maps.LatLngBounds();
    this.items.forEach((item) => bounds.extend(new this.maps.LatLng(item.lat, item.lon)));
    this.map.setBounds(bounds, 30, 30, 30, 30);
    if (this.map.getLevel() < 3) this.map.setLevel(3);
  }

  setFeatures(data, feature) {
    this.features.forEach((overlay) => overlay.setMap(null));
    this.features = [];
    this.featureLabel.setMap(null);
    const style = { strokeColor: feature === 'water' ? '#2683a2' : '#518369', strokeWeight: 1, strokeOpacity: 0.75, fillColor: feature === 'water' ? '#7ec2d4' : '#a8ccb6', fillOpacity: 0.2, zIndex: 0 };
    (data.features || []).forEach((record) => {
      const overlays = geometryToKakaoOverlays(record.geometry, this.maps, style);
      overlays.forEach((overlay) => {
        overlay.setMap(this.map);
        if (record.properties?.name) {
          this.maps.event.addListener(overlay, 'mouseover', (event) => {
            const label = document.createElement('span');
            label.className = 'map-feature-label';
            label.textContent = record.properties.name;
            this.featureLabel.setContent(label);
            this.featureLabel.setPosition(event.latLng);
            this.featureLabel.setMap(this.map);
          });
          this.maps.event.addListener(overlay, 'mouseout', () => this.featureLabel.setMap(null));
        }
      });
      this.features.push(...overlays);
    });
  }

  resize() { this.map.relayout(); }

  destroy() {
    this.clusterer.clear();
    this.infoWindow.close();
    this.featureLabel.setMap(null);
    this.features.forEach((overlay) => overlay.setMap(null));
    this.container.removeEventListener('focus', this.onFocus);
    this.container.removeEventListener('blur', this.onBlur);
    this.container.replaceChildren();
  }
}

class LeafletMapAdapter {
  constructor(container, onSelect) {
    const L = window.L;
    if (!L?.map) throw new Error('Leaflet unavailable');
    this.provider = 'osm';
    this.items = [];
    this.markersById = new Map();
    container.replaceChildren();
    this.map = L.map(container, { preferCanvas: true, scrollWheelZoom: false }).setView([36.3, 127.8], 7);
    L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', { maxZoom: 19, attribution: '&copy; <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener noreferrer">OpenStreetMap</a> contributors' }).addTo(this.map);
    this.markers = L.layerGroup().addTo(this.map);
    this.map.on('focus', () => this.map.scrollWheelZoom.enable());
    this.map.on('blur', () => this.map.scrollWheelZoom.disable());
    this.onSelect = onSelect;
  }

  pointStyle(item, selected) {
    const highlighted = isHighlighted(item, selected);
    return { radius: highlighted ? 5 : 3, weight: 1, color: highlighted ? '#1c589c' : '#58616a', fillColor: highlighted ? '#346fb2' : '#8a949e', fillOpacity: highlighted ? 0.78 : 0.45 };
  }

  setPoints(items, selected) {
    this.items = items;
    this.markers.clearLayers();
    this.markersById.clear();
    items.forEach((item) => {
      const marker = window.L.circleMarker([item.lat, item.lon], this.pointStyle(item, selected)).bindPopup(popupContent(item)).on('click', () => this.onSelect(item));
      marker.addTo(this.markers);
      this.markersById.set(item.id, marker);
    });
  }

  updateSelection(selected) {
    this.items.forEach((item) => this.markersById.get(item.id)?.setStyle(this.pointStyle(item, selected)));
  }

  showItem(item) {
    this.map.setView([item.lat, item.lon], 15);
    this.markersById.get(item.id)?.openPopup();
  }

  fitPoints() {
    if (this.items.length) this.map.fitBounds(this.items.map((item) => [item.lat, item.lon]), { padding: [25, 25], maxZoom: 15 });
  }

  setFeatures(data, feature) {
    const L = window.L;
    if (this.features) this.map.removeLayer(this.features);
    this.features = L.geoJSON(data, {
      style: { color: feature === 'water' ? '#2683a2' : '#518369', weight: 1, fillColor: feature === 'water' ? '#7ec2d4' : '#a8ccb6', fillOpacity: 0.18 },
      pointToLayer: (_, latlng) => L.circleMarker(latlng, { radius: 3, color: '#518369', weight: 1 }),
      onEachFeature: (record, layer) => {
        if (record.properties?.name) {
          const label = document.createElement('span');
          label.textContent = record.properties.name;
          layer.bindTooltip(label);
        }
      },
    }).addTo(this.map);
    this.features.bringToBack?.();
  }

  resize() { this.map.invalidateSize(); }
  destroy() { this.map.remove(); }
}

export async function createMapAdapter(container, config, onSelect, onStatus) {
  const wantsKakao = config?.provider === 'kakao';
  if (wantsKakao && config.kakao_js_key) {
    onStatus('loading', '카카오맵을 연결하고 있습니다.');
    try {
      const maps = await loadKakaoSDK(config.kakao_js_key);
      const adapter = new KakaoMapAdapter(container, maps, onSelect);
      onStatus('kakao', '기본 지도: 카카오맵 · 단지 좌표와 분석용 입지 자료는 별도 출처입니다.');
      return adapter;
    } catch {
      // Never display SDK URLs, keys, or the raw provider error.
      onStatus('fallback', '카카오맵 연결 실패: SDK 도메인·API 권한과 네트워크를 확인해 주세요. 현재 OpenStreetMap으로 표시합니다.');
    }
  } else if (wantsKakao) {
    onStatus('fallback', '카카오맵 JavaScript 키가 연결되지 않아 현재 OpenStreetMap으로 표시합니다.');
  } else {
    onStatus(config?.config_error ? 'fallback' : 'osm', config?.config_error ? '지도 설정을 불러오지 못해 현재 OpenStreetMap으로 표시합니다.' : '기본 지도: OpenStreetMap · 검증된 단지 좌표만 표시합니다.');
  }
  return new LeafletMapAdapter(container, onSelect);
}
