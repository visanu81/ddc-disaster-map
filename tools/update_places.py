# -*- coding: utf-8 -*-
"""동두천시 재난안전지도 — 마커 데이터 수집기.

이 스크립트가 data.js 의 single source of truth.
- 알려진 주소들을 네이버 지오코딩으로 좌표 변환
- 한강홍수통제소(HRFCO) API로 하천관측소 정보 수집
- 결과를 프로젝트 루트 data.js 로 출력

장소를 추가/수정하려면 아래 PLACES 딕셔너리만 수정하고 다시 실행하세요.

실행: python tools/update_places.py
"""
import json
import sys
import time
from datetime import datetime
from pathlib import Path

import requests

# Windows 콘솔 한글 출력
try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = ROOT / '.env'
OUTPUT_PATH = ROOT / 'data.js'


def load_env(path=ENV_PATH):
    env = {}
    with open(path, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            if '=' in line:
                k, v = line.split('=', 1)
                env[k.strip()] = v.strip()
    return env


ENV = load_env()
NAVER_ID = ENV.get('NAVER_MAP_CLIENT_ID', '')
NAVER_SECRET = ENV.get('NAVER_MAP_CLIENT_SECRET', '')
HRFCO_KEY = ENV.get('HRFCO_KEY', '')


# ============================================================
# 장소 정의
# ============================================================
# items 의 각 항목에 address 만 적으면 자동으로 lat/lng 가 채워집니다.
# 이미 lat/lng 가 있으면 지오코딩을 건너뜁니다 (예: 정확한 좌표를 알 때).
# 주소가 모호하면 검색 결과의 첫 번째 좌표가 잡힙니다 — 결과 검증 필요.

PLACES = {
    'fire': {
        'label': '소방서',
        'color': '#ff3b5c',
        'items': [
            {
                'name': '동두천소방서',
                'address': '경기도 동두천시 지행로 6',
                'phone': '031-830-5323',
                'type': '소방서 본서',
                'note': '경기북부소방재난본부 산하 · 불현119안전센터 포함',
            },
            {
                'name': '소요119안전센터',
                'address': '경기 동두천시 평화로2910번길 65',  # 법정동(상봉암동) 빼면 지오코딩 잘 됨
                'type': '119안전센터',
                'note': '동두천소방서 산하 · 상봉암동 소요산 인근',
            },
            {
                'name': '광암119지역대',
                'address': '경기 동두천시 삼육사로1269번길 9',
                'type': '119지역대',
                'note': '동두천소방서 산하',
            },
        ],
    },

    'hospital': {
        'label': '병원',
        'color': '#4aa8ff',
        'items': [
            {
                'name': '동두천중앙성모병원',
                'address': '경기도 동두천시 동광로 53',
                'type': '종합병원',
                'note': '응급의학과 운영',
            },
            {
                'name': '동두천제일요양병원',
                'address': '경기도 동두천시 광암로 7',
                'type': '요양병원',
            },
            {
                'name': '동두천시보건소',
                'address': '경기도 동두천시 중앙로 167',
                'phone': '031-860-3372',
                'type': '보건소',
            },
        ],
    },

    'shelter': {
        'label': '대피소',
        'color': '#00d68f',
        'items': [],  # 추후 행안부 대피소 API 연동 예정
    },

    'danger': {
        'label': '위험지역',
        'color': '#ffaa00',
        'items': [],  # 추후 산림청 산사태위험지역 / 환경부 침수예상지역 연동
    },

    'river': {
        'label': '하천 관측소',
        'color': '#b478ff',
        'items': [],  # HRFCO API 에서 자동 수집 (아래 RIVER_STATIONS)
    },
}

# HRFCO 에서 가져올 동두천 관련 수위관측소 코드 (한강홍수통제소 표준수문DB)
RIVER_STATIONS = [
    {'code': '1022668'},  # 신천 송천교 — 경기도 동두천시 송내동
]


# ============================================================
# 유틸
# ============================================================

def geocode(address):
    """주소 → (lat, lng). 실패 시 None."""
    if not address:
        return None
    if not NAVER_ID or not NAVER_SECRET:
        print('  ! NAVER_MAP_CLIENT_ID/SECRET 가 .env 에 없습니다.')
        return None
    try:
        r = requests.get(
            'https://maps.apigw.ntruss.com/map-geocode/v2/geocode',
            params={'query': address},
            headers={
                'x-ncp-apigw-api-key-id': NAVER_ID,
                'x-ncp-apigw-api-key': NAVER_SECRET,
            },
            timeout=10,
        )
        if r.status_code != 200:
            print(f'  ✗ geocode HTTP {r.status_code}: {address}')
            return None
        addrs = r.json().get('addresses', [])
        if not addrs:
            print(f'  ✗ geocode 결과 없음: {address}')
            return None
        a = addrs[0]
        return float(a['y']), float(a['x'])
    except Exception as e:
        print(f'  ✗ geocode 에러: {e}')
        return None


def dms_to_dec(dms_str):
    """'127-03-05' → 127.0514"""
    if not dms_str:
        return None
    parts = dms_str.strip().split('-')
    if len(parts) != 3:
        return None
    try:
        d, m, s = [float(p) for p in parts]
        return d + m / 60 + s / 3600
    except ValueError:
        return None


# ============================================================
# 하천 관측소 (한강홍수통제소)
# ============================================================

def fetch_river_stations():
    print('[하천 관측소]')
    if not HRFCO_KEY:
        print('  ! HRFCO_KEY 가 .env 에 없습니다.')
        return []

    sess = requests.Session()
    sess.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    })

    info_map = None
    for attempt in range(5):
        try:
            r = sess.get(
                f'http://api.hrfco.go.kr/{HRFCO_KEY}/waterlevel/info.json',
                timeout=60,
            )
            r.raise_for_status()
            stations = r.json().get('content', [])
            info_map = {s['wlobscd']: s for s in stations}
            print(f'  ✓ HRFCO 관측소 목록 {len(stations)}개 로드')
            break
        except Exception as e:
            print(f'  재시도 {attempt + 1}/5: {type(e).__name__}')
            time.sleep(3 + attempt)

    if info_map is None:
        print('  ✗ HRFCO API 호출 실패 — 알려진 좌표로 fallback')
        return [{
            'name': '신천 송천교 (동두천)',
            'lat': 37.8803, 'lng': 127.0514,
            'address': '경기도 동두천시 송내동',
            'type': '수위관측소',
            'code': '1022668',
            'note': '한강홍수통제소 관할 / 주의 3.4m · 경계 4m · 경보 5m',
        }]

    items = []
    for cfg in RIVER_STATIONS:
        s = info_map.get(cfg['code'])
        if not s:
            print(f'  ✗ 코드 {cfg["code"]} 없음')
            continue
        lat = dms_to_dec(s.get('lat', ''))
        lng = dms_to_dec(s.get('lon', ''))
        if lat is None or lng is None:
            print(f'  ✗ 좌표 변환 실패: {cfg["code"]}')
            continue
        addr = f"{s.get('addr', '').strip()} {s.get('etcaddr', '').strip()}".strip()
        note_parts = []
        if s.get('attwl'): note_parts.append(f'주의 {s["attwl"]}m')
        if s.get('wrnwl'): note_parts.append(f'경계 {s["wrnwl"]}m')
        if s.get('almwl'): note_parts.append(f'경보 {s["almwl"]}m')
        note = '한강홍수통제소 관할 / ' + ' · '.join(note_parts) if note_parts else '한강홍수통제소 관할'
        items.append({
            'name': s.get('obsnm', '').strip(),
            'lat': round(lat, 6),
            'lng': round(lng, 6),
            'address': addr,
            'type': '수위관측소',
            'code': cfg['code'],
            'note': note,
        })
        print(f'  ✓ {s["obsnm"]:20s} {lat:.4f}, {lng:.4f}')
    return items


# ============================================================
# 메인
# ============================================================

def main():
    print('=' * 60)
    print('  동두천시 재난안전지도 — 마커 데이터 갱신')
    print('=' * 60)
    print()

    # 1) 하천 관측소
    PLACES['river']['items'] = fetch_river_stations()
    print()

    # 2) 나머지 카테고리: 주소 → 좌표 변환
    for cat_key, cat in PLACES.items():
        if cat_key == 'river':
            continue
        if not cat['items']:
            continue
        print(f'[{cat["label"]}]')
        for item in cat['items']:
            if 'lat' in item and 'lng' in item:
                print(f'  · {item["name"]:20s} (수동 좌표)')
                continue
            addr = item.get('address', '')
            coord = geocode(addr)
            if coord:
                item['lat'], item['lng'] = round(coord[0], 6), round(coord[1], 6)
                print(f'  ✓ {item["name"]:20s} {coord[0]:.4f}, {coord[1]:.4f}')
            else:
                print(f'  ✗ {item["name"]}: 좌표 변환 실패 (마커 미표시)')
            time.sleep(0.2)
        print()

    # 3) data.js 출력
    out = {
        'updatedAt': datetime.now().strftime('%Y-%m-%d %H:%M'),
        'categories': {
            k: {'label': v['label'], 'color': v['color'], 'items': v['items']}
            for k, v in PLACES.items()
        },
    }
    js_text = (
        '/* 자동 생성 — tools/update_places.py 실행 시 갱신됨.\n'
        '   직접 편집하지 말고 update_places.py 의 PLACES 를 고친 뒤 재실행하세요. */\n\n'
        'window.DDC_DATA = ' +
        json.dumps(out, ensure_ascii=False, indent=2) + ';\n'
    )
    OUTPUT_PATH.write_text(js_text, encoding='utf-8')
    print(f'data.js 저장 완료 ({len(js_text):,} bytes)')


if __name__ == '__main__':
    main()
