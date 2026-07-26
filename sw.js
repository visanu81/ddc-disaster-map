/* 동두천시 재난안전지도 — Service Worker
   목적: 산속에서 데이터 연결 끊겨도 페이지·마커·지도 일부 동작.
   - 우리 정적 자산(HTML/JS): 캐시 우선, 백그라운드 갱신
   - 네이버맵 타일: 한 번 본 영역은 캐시에서 (오프라인에서도 표시)
   - 외부 폰트·CDN: 캐시 우선, 네트워크 fallback
*/

const VERSION = 'ddc-v18-sos-msglog';   // 버전 올리면 이전 캐시 자동 무효화
const STATIC_CACHE = 'ddc-static-' + VERSION;
const TILE_CACHE = 'ddc-tiles';   // 타일은 version 무관 (계속 누적)

// 빌드 시 캐시할 파일 목록 (data.js·weather.js 는 항상 최신을 받도록 stale-while-revalidate)
const STATIC_FILES = [
  './',
  './index.html',
  './manifest.json',
  './icon.svg',
  './icon-192.png',
  './icon-512.png',
  './icon-512-maskable.png',
];

self.addEventListener('install', e => {
  e.waitUntil(
    caches.open(STATIC_CACHE)
      .then(cache => cache.addAll(STATIC_FILES).catch(() => null))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys().then(keys =>
      Promise.all(
        keys
          .filter(k => k.startsWith('ddc-static-') && k !== STATIC_CACHE)
          .map(k => caches.delete(k))
      )
    ).then(() => self.clients.claim())
  );
});

// 조난자 알림(showNotification) 클릭 → 열린 앱 창에 포커스, 없으면 새로 엶
self.addEventListener('notificationclick', e => {
  e.notification.close();
  e.waitUntil(
    self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then(list => {
      const c = list.find(w => 'focus' in w);
      if (c) return c.focus();
      if (self.clients.openWindow) return self.clients.openWindow('./');
    })
  );
});

self.addEventListener('fetch', e => {
  const req = e.request;
  if (req.method !== 'GET') return;

  let url;
  try { url = new URL(req.url); } catch (_) { return; }

  // 브이월드 타일(api.vworld.kr): 캐시 안 함(network-only).
  // 도메인 미등록 시의 인증 실패가 캐시돼 등록 후에도 실패로 남는 함정 방지.
  if (url.hostname.includes('vworld.kr')) {
    return;   // SW 개입 안 함 → 브라우저가 직접 네트워크로
  }

  // 네이버맵 타일/SDK: cache-first (한 번 본 영역 오프라인 유지)
  if (
    url.hostname.includes('map.naver.com') ||
    url.hostname.includes('maps.naver.com') ||
    url.hostname.includes('oapi.map.naver.com') ||
    url.hostname.includes('nrbe.map.naver.com') ||
    url.hostname.includes('naveropenapi') ||
    url.hostname.includes('ntruss')
  ) {
    e.respondWith(cacheFirst(req, TILE_CACHE));
    return;
  }

  // 같은 출처(우리 사이트): stale-while-revalidate
  if (url.origin === self.location.origin) {
    e.respondWith(staleWhileRevalidate(req, STATIC_CACHE));
    return;
  }

  // 그 외 외부 자원(폰트 등): cache-first
  e.respondWith(cacheFirst(req, STATIC_CACHE));
});

async function staleWhileRevalidate(request, cacheName) {
  const cache = await caches.open(cacheName);
  const cached = await cache.match(request);
  const network = fetch(request).then(resp => {
    if (resp && resp.ok && resp.status === 200 && resp.type !== 'opaque') {
      cache.put(request, resp.clone()).catch(() => {});
    }
    return resp;
  }).catch(() => null);
  return cached || (await network) || new Response('offline', { status: 503 });
}

async function cacheFirst(request, cacheName) {
  const cache = await caches.open(cacheName);
  const cached = await cache.match(request);
  if (cached) return cached;
  try {
    const resp = await fetch(request);
    if (resp && resp.ok && (resp.status === 200 || resp.type === 'opaque')) {
      cache.put(request, resp.clone()).catch(() => {});
    }
    return resp;
  } catch (e) {
    return cached || new Response('offline', { status: 503 });
  }
}
