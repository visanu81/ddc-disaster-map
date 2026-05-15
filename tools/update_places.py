# -*- coding: utf-8 -*-
"""동두천시 재난안전지도 — 마커 데이터 수집기 (구글 시트 연동판).

데이터 소스: 구글 시트 (.env 의 GOOGLE_SHEET_ID 로 지정)
시트의 첫 탭(gid=0)을 CSV 로 가져와서 좌표를 자동 변환 후 data.js 생성.

시트 양식 (헤더 행은 어디 있어도 자동 탐지):
  category | name | address | phone | type | note | code | lat | lng
  - category, name 은 필수
  - address 있고 lat/lng 비어있으면 → 네이버 지오코딩으로 자동 변환
  - lat/lng 가 직접 입력되어 있으면 그대로 사용 (지오코딩 안 함)
  - code(하천관측소만): 한강홍수통제소 코드 → 좌표 자동 수집

실행: python tools/update_places.py
"""
import csv
import io
import json
import sys
import time
from datetime import datetime
from pathlib import Path

import requests

try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = ROOT / '.env'
OUTPUT_PATH = ROOT / 'data.js'


# ============================================================
# 환경
# ============================================================

def load_env(path=ENV_PATH):
    env = {}
    if not path.exists():
        return env
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
SHEET_ID = ENV.get('GOOGLE_SHEET_ID', '')
NAVER_ID = ENV.get('NAVER_MAP_CLIENT_ID', '')
NAVER_SECRET = ENV.get('NAVER_MAP_CLIENT_SECRET', '')
HRFCO_KEY = ENV.get('HRFCO_KEY', '')


# ============================================================
# 카테고리 색상
# ============================================================
# 시트에 새 카테고리가 생겨도 자동 처리됨. 색상 지정하고 싶으면 여기 추가.

CATEGORY_COLORS = {
    '소방서':         '#ff3b5c',  # 빨강
    '병원':           '#4aa8ff',  # 파랑
    '의료기관':       '#4aa8ff',
    '대피소':         '#00d68f',  # 녹색
    '위험지역':       '#ffaa00',  # 주황
    '하천 관측소':    '#b478ff',  # 보라
    '하천관측소':     '#b478ff',
    '구급함':         '#ff8fab',  # 분홍 (구급)
    '산악위치표지판': '#22c55e',  # 진녹색 (안내)
}

# 매핑 없는 카테고리에 자동 부여할 색상 팔레트
AUTO_PALETTE = ['#00d4ff', '#9333ea', '#06b6d4', '#f59e0b', '#ec4899',
                '#84cc16', '#0ea5e9', '#d97706']


def color_for(cat, used_count):
    if cat in CATEGORY_COLORS:
        return CATEGORY_COLORS[cat]
    return AUTO_PALETTE[used_count % len(AUTO_PALETTE)]


# ============================================================
# 구글 시트 fetch
# ============================================================

def fetch_sheet_rows(sheet_id):
    """공유 링크가 있는 구글 시트의 첫 탭을 CSV 로 가져와 dict 리스트로 변환."""
    if not sheet_id:
        raise RuntimeError('GOOGLE_SHEET_ID 가 .env 에 없습니다.')

    url = f'https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv&gid=0'
    r = requests.get(url, timeout=20, allow_redirects=True)
    if r.status_code != 200:
        raise RuntimeError(f'시트 fetch 실패 (HTTP {r.status_code}). '
                           f'시트가 "링크가 있는 사람: 뷰어" 로 공유되어 있는지 확인하세요.')
    r.encoding = 'utf-8'

    rows = list(csv.reader(io.StringIO(r.text)))
    if not rows:
        return []

    # 헤더 행 자동 탐지 (category 단어가 있는 행)
    header_idx = None
    for i, row in enumerate(rows):
        cells = [c.strip().lower() for c in row]
        if 'category' in cells:
            header_idx = i
            break
    if header_idx is None:
        raise RuntimeError('시트에서 "category" 헤더를 찾을 수 없습니다.')

    header = [c.strip() for c in rows[header_idx]]
    items = []
    for row in rows[header_idx + 1:]:
        if not any(c.strip() for c in row):
            continue
        d = {}
        for i, h in enumerate(header):
            if not h:
                continue
            d[h] = row[i].strip() if i < len(row) else ''
        if not d.get('category') or not d.get('name'):
            continue
        items.append(d)
    return items


# ============================================================
# 네이버 지오코딩
# ============================================================

def geocode(address):
    if not address or not NAVER_ID or not NAVER_SECRET:
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
            return None
        addrs = r.json().get('addresses', [])
        if not addrs:
            return None
        a = addrs[0]
        return float(a['y']), float(a['x'])
    except Exception:
        return None


# ============================================================
# 한강홍수통제소 — 시트에 'code' 컬럼이 채워진 행만 좌표 자동 수집
# ============================================================

def dms_to_dec(dms_str):
    parts = (dms_str or '').strip().split('-')
    if len(parts) != 3:
        return None
    try:
        d, m, s = [float(p) for p in parts]
        return d + m / 60 + s / 3600
    except ValueError:
        return None


_river_cache = None

def fetch_hrfco_station(code):
    """HRFCO 관측소 코드 → (lat, lng, info_dict) 또는 None."""
    global _river_cache
    if not code or not HRFCO_KEY:
        return None

    if _river_cache is None:
        sess = requests.Session()
        sess.headers.update({'User-Agent': 'Mozilla/5.0'})
        for attempt in range(5):
            try:
                r = sess.get(f'http://api.hrfco.go.kr/{HRFCO_KEY}/waterlevel/info.json', timeout=60)
                r.raise_for_status()
                stations = r.json().get('content', [])
                _river_cache = {s['wlobscd']: s for s in stations}
                print(f'  ✓ HRFCO 관측소 목록 {len(stations)}개 로드')
                break
            except Exception as e:
                print(f'  HRFCO 재시도 {attempt+1}/5: {type(e).__name__}')
                time.sleep(3 + attempt)
        else:
            print('  ✗ HRFCO 호출 실패 — code 컬럼 행은 마커 미표시')
            _river_cache = {}

    s = _river_cache.get(code)
    if not s:
        return None
    lat = dms_to_dec(s.get('lat'))
    lng = dms_to_dec(s.get('lon'))
    if lat is None or lng is None:
        return None
    return lat, lng, s


# ============================================================
# 메인 처리
# ============================================================

def normalize_name(name):
    """셀 안 줄바꿈을 ' / ' 로 단순화."""
    return ' / '.join(part.strip() for part in name.replace('\r', '').split('\n') if part.strip())


def main():
    print('=' * 60)
    print('  동두천시 재난안전지도 — 시트 → data.js 동기화')
    print('=' * 60)
    print()

    print(f'[1] 구글 시트 fetch ({SHEET_ID[:10]}...)')
    rows = fetch_sheet_rows(SHEET_ID)
    print(f'  ✓ {len(rows)} 행 로드')
    print()

    # 카테고리별로 분류 (시트 순서 유지)
    categories = {}  # 한글 키 → {'color': ..., 'items': [...]}
    cat_order = []   # 카테고리 출현 순서
    for row in rows:
        cat = row['category']
        if cat not in categories:
            color = color_for(cat, len(cat_order))
            categories[cat] = {'color': color, 'items': []}
            cat_order.append(cat)

    # 행별로 좌표 처리
    skipped = []
    for row in rows:
        cat = row['category']
        name = normalize_name(row['name'])
        item = {'name': name}

        # 선택 필드들
        for k in ('address', 'phone', 'type', 'note', 'code', 'region'):
            v = row.get(k, '').strip()
            if v:
                item[k] = v

        # 좌표 결정 (우선순위: 직접 입력 > HRFCO 코드 > 주소 지오코딩)
        lat = row.get('lat', '').strip()
        lng = row.get('lng', '').strip()
        coord = None
        if lat and lng:
            try:
                coord = (float(lat), float(lng))
                source = 'sheet'
            except ValueError:
                pass

        if coord is None and item.get('code'):
            res = fetch_hrfco_station(item['code'])
            if res:
                coord = (res[0], res[1])
                source = 'HRFCO'
                # 관측소 부가정보 자동 첨부
                s = res[2]
                addr = f"{s.get('addr','').strip()} {s.get('etcaddr','').strip()}".strip()
                if addr and 'address' not in item:
                    item['address'] = addr
                note_parts = []
                if s.get('attwl'): note_parts.append(f'주의 {s["attwl"]}m')
                if s.get('wrnwl'): note_parts.append(f'경계 {s["wrnwl"]}m')
                if s.get('almwl'): note_parts.append(f'경보 {s["almwl"]}m')
                if note_parts:
                    extra = '한강홍수통제소 / ' + ' · '.join(note_parts)
                    item['note'] = (item.get('note', '') + ' · ' + extra).strip(' ·') if item.get('note') else extra

        if coord is None and item.get('address'):
            geo = geocode(item['address'])
            if geo:
                coord = geo
                source = 'geocode'
                time.sleep(0.15)

        if coord is None:
            skipped.append((cat, name))
            continue

        item['lat'] = round(coord[0], 6)
        item['lng'] = round(coord[1], 6)
        categories[cat]['items'].append(item)

    # 출력
    print('[2] 카테고리별 결과')
    total = 0
    for cat in cat_order:
        n = len(categories[cat]['items'])
        total += n
        print(f'  · {cat:14s} {n:3d}개 (색 {categories[cat]["color"]})')
    print(f'  총 마커: {total}개')
    if skipped:
        print(f'  마커 미표시: {len(skipped)}개 (주소/좌표 둘 다 없음)')
        for cat, name in skipped:
            print(f'      - [{cat}] {name}')
    print()

    # JSON 출력
    out = {
        'updatedAt': datetime.now().strftime('%Y-%m-%d %H:%M'),
        'sheetId': SHEET_ID,
        'categories': {cat: categories[cat] for cat in cat_order},
    }
    js_text = (
        '/* 자동 생성 — tools/update_places.py 실행 시 갱신됨.\n'
        '   데이터를 추가/수정하려면 구글 시트(.env GOOGLE_SHEET_ID)를 편집하세요. */\n\n'
        'window.DDC_DATA = ' + json.dumps(out, ensure_ascii=False, indent=2) + ';\n'
    )
    OUTPUT_PATH.write_text(js_text, encoding='utf-8')
    print(f'[3] data.js 저장 완료 ({len(js_text):,} bytes)')


if __name__ == '__main__':
    main()
