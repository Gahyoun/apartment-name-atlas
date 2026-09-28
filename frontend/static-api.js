/* Promise-based facade: the existing frontend keeps its /api route contracts. */
let worker;
let setupPromise;
let sequence = 0;
const pending = new Map();

async function initialize() {
  if (setupPromise) return setupPromise;
  setupPromise = (async () => {
    const configURL = new URL('./runtime-config.json', import.meta.url);
    const response = await fetch(configURL, { cache: 'no-cache' });
    if (!response.ok) throw new Error('정적 사이트 설정을 불러오지 못했습니다.');
    const config = await response.json();
    if (config.mode !== 'static' || config.schema_version !== 1) throw new Error('지원하지 않는 정적 데이터 버전입니다.');
    worker = new Worker(new URL('./static-worker.js', import.meta.url), { type: 'module' });
    worker.onmessage = ({ data }) => {
      const waiting = pending.get(data.id);
      if (!waiting) return;
      pending.delete(data.id);
      clearTimeout(waiting.timer);
      if (data.error) waiting.reject(new Error(data.error));
      else waiting.resolve(data.result);
    };
    worker.onerror = () => {
      for (const waiting of pending.values()) {
        clearTimeout(waiting.timer);
        waiting.reject(new Error('정적 분석 Worker를 실행하지 못했습니다. 페이지를 새로고침해 주세요.'));
      }
      pending.clear();
    };
    await post('initialize', { config, baseURL: configURL.href });
  })();
  return setupPromise;
}

function post(path, params) {
  return new Promise((resolve, reject) => {
    const id = ++sequence;
    const timer = setTimeout(() => {
      pending.delete(id);
      reject(new Error('정적 데이터 읽기 시간이 초과되었습니다. 연결을 확인하고 다시 시도해 주세요.'));
    }, 90000);
    pending.set(id, { resolve, reject, timer });
    worker.postMessage({ id, path, params });
  });
}

export async function staticApi(path, values = {}) {
  await initialize();
  return post(path, values);
}
