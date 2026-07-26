# -*- coding: utf-8 -*-
"""등산로 POI 수집기 — 한국등산트레킹지원센터 공공데이터 (B553662).

산악구조용: 신고자가 "OO 갈림길", "OO 대피소" 라고 말할 때 좌표로 바로 매칭하기 위한 지점 데이터.
표지판(국가지점번호)은 공개 데이터가 없어 구글시트 손입력 유지. 이 도구는 그 '보완재'.

수집 대상(산악구조 실효성 기준으로 선별):
  SIGN(갈림길) · SHELTER(대피소) · ENTRY(등산로입구) · DANGER(위험지역)
  · PEAK(봉우리) · SPRING(샘터) · PARK(주차장)

출력: poi.js  →  window.DDC_POI = { updatedAt, categories: { '갈림길': {color, items:[...]}, ... } }
      index.html 이 DDC_DATA.categories 에 병합해서 기존 마커·칩·클러스터 시스템으로 렌더.

실행: python tools/update_poi.py
사전조건: data.go.kr 에서 아래 API '활용신청'(자동승인) 후 DATA_GO_KR_KEY 사용
  - 100대명산 숲길 POI : https://www.data.go.kr/data/15100775/openapi.do
  - 숲길 연결망 POI    : (B553662 poiInfoService)
"""
import json
import os
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
OUTPUT_PATH = ROOT / 'poi.js'


def load_env(path=ENV_PATH):
    env = {}
    if not path.exists():
        return env
    with open(path, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            k, v = line.split('=', 1)
            env[k.strip()] = v.strip()
    return env


ENV = load_env()
SERVICE_KEY = ENV.get('DATA_GO_KR_KEY') or os.environ.get('DATA_GO_KR_KEY', '')

# 수집 범위 — 경기북부 (동두천 중심, 11개 시군 커버)
BBOX = {'lat_min': 37.55, 'lat_max': 38.30, 'lng_min': 126.60, 'lng_max': 127.60}

# 장소유형코드 → 우리 카테고리명 + 색상 (산악구조 유용도순)
PLACE_TYPES = {
    'SIGN':    {'label': '갈림길',    'color': '#f59e0b'},
    'SHELTER': {'label': '산악대피소', 'color': '#10b981'},
    'ENTRY':   {'label': '등산로입구', 'color': '#3b82f6'},
    'DANGER':  {'label': '산악위험구간', 'color': '#ef4444'},
    'PEAK':    {'label': '봉우리',    'color': '#8b5cf6'},
    'SPRING':  {'label': '샘터',      'color': '#06b6d4'},
    'PARK':    {'label': '등산로주차장', 'color': '#64748b'},
}

ENDPOINTS = [
    ('100대명산 숲길POI', 'https://apis.data.go.kr/B553662/fmmtnFrtrlPoiInfoService/getFmmtnFrtrlPoiInfoList'),
    ('숲길 연결망 POI',   'https://apis.data.go.kr/B553662/poiInfoService/getPoiInfoList'),
]


def pick(d, *names):
    """응답 필드명이 문서와 다를 수 있어 여러 후보 중 먼저 있는 값 사용."""
    for n in names:
        if n in d and d[n] not in (None, '', ' '):
            return d[n]
    return None


def to_float(v):
    try:
        f = float(str(v).strip())
        return f
    except (TypeError, ValueError):
        return None


def fetch_all(url, label):
    """페이지네이션 전량 수집. 실패 시 빈 리스트 + 사유 출력."""
    rows = []
    page = 1
    per = 1000
    while page <= 40:                      # 안전 상한 (최대 4만건)
        try:
            r = requests.get(url, params={
                'serviceKey': SERVICE_KEY,
                'pageNo': page,
                'numOfRows': per,
                'type': 'json',
            }, timeout=30)
        except Exception as e:
            print(f'  ✗ {label} 요청 실패: {type(e).__name__}')
            break

        if r.status_code == 403:
            print(f'  ✗ {label}: 403 Forbidden — data.go.kr 에서 이 API "활용신청" 필요')
            break
        if r.status_code != 200:
            print(f'  ✗ {label}: HTTP {r.status_code}')
            break

        text = r.text.strip()
        if text.startswith('<'):           # XML 에러 응답
            snippet = text[:200].replace('\n', ' ')
            print(f'  ✗ {label}: XML 응답(오류 가능) {snippet}')
            break

        try:
            j = r.json()
        except Exception:
            print(f'  ✗ {label}: JSON 파싱 실패')
            break

        # 응답 구조가 서비스마다 달라 방어적으로 탐색
        items = None
        if isinstance(j, dict):
            body = j.get('response', {}).get('body') if 'response' in j else j.get('body', j)
            if isinstance(body, dict):
                it = body.get('items', body.get('item'))
                if isinstance(it, dict):
                    items = it.get('item', [])
                elif isinstance(it, list):
                    items = it
            elif isinstance(body, list):
                items = body
        if items is None:
            items = []
        if isinstance(items, dict):
            items = [items]

        if not items:
            break
        rows.extend(items)
        print(f'    · {label} p{page}: {len(items)}건 (누적 {len(rows)})')
        if len(items) < per:
            break
        page += 1
        time.sleep(0.2)
    return rows


def main():
    print('=' * 60)
    print('  등산로 POI 수집 (한국등산트레킹지원센터)')
    print('=' * 60)
    if not SERVICE_KEY:
        print('✗ DATA_GO_KR_KEY 없음 (.env 또는 환경변수)')
        sys.exit(1)

    raw = []
    for label, url in ENDPOINTS:
        print(f'[fetch] {label}')
        raw.extend(fetch_all(url, label))
    print(f'  총 원본 {len(raw)}건')

    if not raw:
        print()
        print('✗ 수집 0건 — 활용신청이 안 됐거나 서비스 응답 없음. poi.js 를 덮어쓰지 않고 종료합니다.')
        sys.exit(2)

    # 분류 + 좌표/범위 필터
    categories = {}
    kept = skipped_type = skipped_coord = skipped_bbox = 0
    for it in raw:
        if not isinstance(it, dict):
            continue
        code = (pick(it, 'placeTpeCd', 'placeTypeCd', 'plceTpCd', 'poiTpCd') or '').strip().upper()
        meta = PLACE_TYPES.get(code)
        if not meta:
            skipped_type += 1
            continue

        lat = to_float(pick(it, 'lat', 'latitude', 'yCrdnt', 'la'))
        lng = to_float(pick(it, 'lot', 'lon', 'lng', 'longitude', 'xCrdnt', 'lo'))
        if lat is None or lng is None:
            skipped_coord += 1
            continue
        if not (BBOX['lat_min'] <= lat <= BBOX['lat_max'] and BBOX['lng_min'] <= lng <= BBOX['lng_max']):
            skipped_bbox += 1
            continue

        name = (pick(it, 'placeNm', 'poiNm', 'plceNm', 'frtrlNm') or meta['label']).strip()
        item = {'name': name, 'lat': round(lat, 6), 'lng': round(lng, 6)}

        mtn = pick(it, 'frtrlNm', 'mntnNm', 'mntnAttrpNm')
        if mtn:
            item['type'] = str(mtn).strip()
        alt = to_float(pick(it, 'aslAltide', 'altide', 'alt'))
        if alt is not None:
            item['note'] = f'해발 {int(alt)}m'
        # 이정표 목적지(갈림길에서 어느 방향인지) — 산악구조 판단에 유용
        dests = [str(pick(it, f'sgnpstDstn{i}Nm') or '').strip() for i in range(1, 5)]
        dests = [d for d in dests if d]
        if dests:
            more = ' / '.join(dests)
            item['note'] = (item.get('note', '') + ' · 이정표: ' + more).strip(' ·')

        cat = meta['label']
        if cat not in categories:
            categories[cat] = {'color': meta['color'], 'items': []}
        categories[cat]['items'].append(item)
        kept += 1

    print()
    print('[분류 결과]')
    for cat, v in categories.items():
        print(f'  · {cat:12s} {len(v["items"]):4d}개')
    print(f'  채택 {kept} / 유형제외 {skipped_type} / 좌표없음 {skipped_coord} / 범위밖 {skipped_bbox}')

    if kept == 0:
        print('✗ 경기북부 범위 내 POI 0건 — poi.js 를 덮어쓰지 않고 종료합니다.')
        sys.exit(3)

    out = {
        'updatedAt': datetime.now().strftime('%Y-%m-%d %H:%M'),
        'source': '한국등산트레킹지원센터(공공데이터포털 B553662)',
        'categories': categories,
    }
    js = ('/* 자동 생성 — tools/update_poi.py. 등산로 POI(갈림길·대피소·입구 등).\n'
          '   출처: 한국등산트레킹지원센터. 국가지점번호 표지판은 공개데이터가 없어 별도(구글시트) 관리. */\n\n'
          'window.DDC_POI = ' + json.dumps(out, ensure_ascii=False, indent=2) + ';\n')
    OUTPUT_PATH.write_text(js, encoding='utf-8')
    print(f'\n[저장] poi.js ({len(js):,} bytes)')


if __name__ == '__main__':
    main()
