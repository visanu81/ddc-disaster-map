# -*- coding: utf-8 -*-
"""경기북부 11개 시군 기상·산림·하천 위험도 수집기 → weather.js

데이터 소스 (.env / GitHub Actions Secrets):
- 기상청 초단기실황 / 단기예보: 시군별 기상격자(nx, ny)
- 산림청 V2 산불위험: 시군별 등급
- 한강홍수통제소: 시군별 수위관측소(있는 시군만)

지원 시군:
- 동두천, 의정부, 양주, 포천, 연천, 가평, 남양주, 구리, 파주, 고양, 일산(=고양 alias)

출력 구조 (weather.js):
  window.DDC_WEATHER = {
    updatedAt: '...',
    regions: {
      dongducheon: { current, forecast, fire, river },
      uijeongbu:   { ... },
      ...
      ilsan: <고양 alias>
    }
  }

실행: python tools/update_weather.py
"""
import json
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import requests

try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = ROOT / '.env'
OUTPUT_PATH = ROOT / 'weather.js'


def load_env(p=ENV_PATH):
    env = {}
    if not p.exists():
        return env
    with open(p, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            if '=' in line:
                k, v = line.split('=', 1)
                env[k.strip()] = v.strip()
    return env


ENV = load_env()
DATA_KEY = ENV.get('DATA_GO_KR_KEY') or os.environ.get('DATA_GO_KR_KEY', '')
HRFCO_KEY = ENV.get('HRFCO_KEY') or os.environ.get('HRFCO_KEY', '')


# 시군 매핑 (기상격자·산불시군구명·하천관측소 코드 리스트)
REGIONS = {
    'dongducheon': {'name': '동두천시', 'nx': 61, 'ny': 134, 'sigun': '동두천시', 'rivers': ['1022668']},
    'uijeongbu':   {'name': '의정부시', 'nx': 61, 'ny': 130, 'sigun': '의정부시', 'rivers': ['1018665']},
    'yangju':      {'name': '양주시',   'nx': 61, 'ny': 131, 'sigun': '양주시',   'rivers': []},
    'pocheon':     {'name': '포천시',   'nx': 64, 'ny': 133, 'sigun': '포천시',   'rivers': ['1022640']},
    'yeoncheon':   {'name': '연천군',   'nx': 61, 'ny': 138, 'sigun': '연천군',   'rivers': ['1022670', '1021680']},
    'gapyeong':    {'name': '가평군',   'nx': 73, 'ny': 133, 'sigun': '가평군',   'rivers': []},
    'namyangju':   {'name': '남양주시', 'nx': 64, 'ny': 128, 'sigun': '남양주시', 'rivers': ['1018638']},
    'guri':        {'name': '구리시',   'nx': 62, 'ny': 127, 'sigun': '구리시',   'rivers': []},
    'paju':        {'name': '파주시',   'nx': 56, 'ny': 131, 'sigun': '파주시',   'rivers': []},
    'goyang':      {'name': '고양시',   'nx': 57, 'ny': 128, 'sigun': '고양시',   'rivers': []},
    # 일산은 고양시의 일부 — main 처리 후 결과 복사
}

# 하천 관측소 임계값 (코드 → 정보)
RIVER_META = {
    '1022668': {'name': '신천 송천교 (동두천)', 'attwl': 3.4,  'wrnwl': 4.0,  'almwl': 5.0},
    '1022670': {'name': '신천 (연천)',         'attwl': 3.5,  'wrnwl': 4.8,  'almwl': 5.5},
    '1021680': {'name': '임진강 임진교 (연천)', 'attwl': 5.9,  'wrnwl': 8.0,  'almwl': 10.8},
    '1022640': {'name': '한탄강 용담교 (포천)', 'attwl': 9.5,  'wrnwl': 15.0, 'almwl': 18.0},
    '1018638': {'name': '왕숙천 왕숙교 (남양주)', 'attwl': 4.9, 'wrnwl': 6.5, 'almwl': 8.0},
    '1018665': {'name': '중랑천 신곡교 (의정부)', 'attwl': 2.6, 'wrnwl': 4.0, 'almwl': 6.0},
}

PTY_TXT = {
    '0': '없음', '1': '비', '2': '비/눈', '3': '눈',
    '4': '소나기', '5': '빗방울', '6': '진눈깨비', '7': '눈날림',
}
PTY_ICON = {
    '0': '🌤', '1': '🌧', '2': '🌨', '3': '❄',
    '4': '⛈', '5': '🌦', '6': '🌨', '7': '🌨',
}


def fcst_base_time():
    """단기예보 발표시각: 02/05/08/11/14/17/20/23"""
    now = datetime.now()
    issue = [23, 20, 17, 14, 11, 8, 5, 2]
    target = now - timedelta(minutes=15)
    for h in issue:
        cand = target.replace(hour=h, minute=0, second=0, microsecond=0)
        if cand <= target:
            return cand.strftime('%Y%m%d'), f'{h:02d}00'
    prev = (target - timedelta(days=1)).replace(hour=23, minute=0, second=0)
    return prev.strftime('%Y%m%d'), '2300'


def parse_pcp(val):
    if not val or val in ('강수없음', '-', 'null'):
        return 0
    val = str(val).replace('mm', '').strip()
    if val.startswith('30.0~50.0'): return 40
    if val.startswith('50.0'): return 60
    if val == '1mm 미만': return 0.5
    try:
        return float(val)
    except ValueError:
        return 0


# ============================================================
# 1. 현재 실황 (시군별)
# ============================================================

def fetch_current(nx, ny):
    if not DATA_KEY:
        return None
    base = datetime.now() - timedelta(minutes=40)
    url = 'https://apis.data.go.kr/1360000/VilageFcstInfoService_2.0/getUltraSrtNcst'
    params = {
        'serviceKey': DATA_KEY,
        'pageNo': 1, 'numOfRows': 30, 'dataType': 'JSON',
        'base_date': base.strftime('%Y%m%d'),
        'base_time': base.strftime('%H00'),
        'nx': nx, 'ny': ny,
    }
    try:
        r = requests.get(url, params=params, timeout=15)
        r.raise_for_status()
        items = r.json()['response']['body']['items']['item']
    except Exception as e:
        return None

    obs = {}
    for it in items:
        cat, val = it['category'], it['obsrValue']
        if cat == 'T1H':
            obs['temp'] = round(float(val), 1)
        elif cat == 'RN1':
            try:    obs['rain'] = float(val)
            except: obs['rain'] = 0
        elif cat == 'REH':
            obs['humid'] = int(float(val))
        elif cat == 'WSD':
            obs['wind'] = round(float(val), 1)
        elif cat == 'PTY':
            obs['pty'] = val
            obs['ptyText'] = PTY_TXT.get(val, '')
            obs['icon'] = PTY_ICON.get(val, '🌤')
    return obs if obs else None


# ============================================================
# 2. 단기예보 12시간 (시군별)
# ============================================================

def fetch_forecast(nx, ny):
    if not DATA_KEY:
        return []
    base_date, base_time = fcst_base_time()
    url = 'https://apis.data.go.kr/1360000/VilageFcstInfoService_2.0/getVilageFcst'
    params = {
        'serviceKey': DATA_KEY,
        'pageNo': 1, 'numOfRows': 800, 'dataType': 'JSON',
        'base_date': base_date, 'base_time': base_time,
        'nx': nx, 'ny': ny,
    }
    try:
        r = requests.get(url, params=params, timeout=15)
        r.raise_for_status()
        items = r.json()['response']['body']['items']['item']
    except Exception:
        return []

    by_time = {}
    for it in items:
        key = (it['fcstDate'], it['fcstTime'])
        by_time.setdefault(key, {})[it['category']] = it['fcstValue']

    now = datetime.now().replace(minute=0, second=0, microsecond=0)
    result = []
    for (d, t) in sorted(by_time.keys()):
        dt = datetime.strptime(d + t, '%Y%m%d%H%M')
        if dt < now:
            continue
        if len(result) >= 12:
            break
        cats = by_time[(d, t)]
        result.append({
            'hour': dt.hour,
            'pop': int(float(cats.get('POP', 0))) if cats.get('POP') else 0,
            'pcp': parse_pcp(cats.get('PCP', '0')),
            'pty': cats.get('PTY', '0'),
            'temp': round(float(cats.get('TMP', 0)), 1) if cats.get('TMP') else None,
        })
    return result


# ============================================================
# 3. 산불위험 V2 — 한 번 호출로 전체 시군 매핑
# ============================================================

def fetch_all_fire():
    print('[산불위험 (전체 시군)]')
    if not DATA_KEY:
        return {}
    url = 'https://apis.data.go.kr/1400377/forestPointV2/forestPointListSigunguSearchV2'
    try:
        r = requests.get(url, params={
            'serviceKey': DATA_KEY,
            'pageNo': 1, 'numOfRows': 300, '_type': 'json',
        }, timeout=15)
        r.raise_for_status()
        body = r.json()['response']['body']
        items_obj = body.get('items', {}) or {}
        items = items_obj.get('item', []) if isinstance(items_obj, dict) else []
        if isinstance(items, dict):
            items = [items]
    except Exception as e:
        print(f'  ✗ {e}')
        return {}

    # 시군구명 → 등급 매핑 (고양시는 3개 구로 나뉠 수 있으므로 최대값)
    name_max = {}
    sigun_aliases = {
        '고양시덕양구': '고양시',
        '고양시일산동구': '고양시',
        '고양시일산서구': '고양시',
    }
    for it in items:
        if str(it.get('doname', '')) != '경기도':
            continue
        sigun = str(it.get('sigun', '')).strip()
        sigun = sigun_aliases.get(sigun, sigun)
        d2 = float(it.get('d2', 0) or 0)
        d3 = float(it.get('d3', 0) or 0)
        d4 = float(it.get('d4', 0) or 0)
        if d4 > 0:   lv = 4
        elif d3 > 0: lv = 3
        elif d2 > 0: lv = 2
        else:        lv = 1
        name_max[sigun] = max(name_max.get(sigun, 0), lv)

    txt_map = {1: '낮음', 2: '보통', 3: '높음', 4: '매우높음'}
    result = {}
    for rkey, info in REGIONS.items():
        lv = name_max.get(info['sigun'])
        if lv:
            result[rkey] = {'level': lv, 'levelText': txt_map[lv]}
            print(f'  ✓ {info["sigun"]}: {txt_map[lv]}')
        else:
            print(f'  · {info["sigun"]}: 데이터 없음')
    return result


# ============================================================
# 4. 하천 수위 (시군별 매핑 — 관측소 있는 시군만)
# ============================================================

_river_cache = None

def fetch_river_data(code):
    """HRFCO 한 관측소의 최신 수위 + 상태."""
    if not HRFCO_KEY:
        return None
    meta = RIVER_META.get(code)
    if not meta:
        return None
    now = datetime.now()
    sdt = (now - timedelta(hours=3)).strftime('%Y%m%d%H')
    edt = now.strftime('%Y%m%d%H')

    sess = requests.Session()
    sess.headers.update({'User-Agent': 'Mozilla/5.0'})
    for attempt in range(4):
        try:
            url = f'http://api.hrfco.go.kr/{HRFCO_KEY}/waterlevel/list/1H/{code}/{sdt}/{edt}.json'
            r = sess.get(url, timeout=30)
            r.raise_for_status()
            data = r.json().get('content', [])
            if not data:
                return None
            latest = data[-1]
            wl = float(latest.get('wl', 0))
            attwl, wrnwl, almwl = meta['attwl'], meta['wrnwl'], meta['almwl']
            if wl >= almwl:    status, lv = '경보', 3
            elif wl >= wrnwl:  status, lv = '경계', 2
            elif wl >= attwl:  status, lv = '주의', 1
            else:              status, lv = '정상', 0
            return {
                'name': meta['name'],
                'wl': wl,
                'status': status,
                'level': lv,
                'attwl': attwl, 'wrnwl': wrnwl, 'almwl': almwl,
            }
        except Exception as e:
            time.sleep(1 + attempt)
    return None


# ============================================================
# 메인
# ============================================================

def main():
    print('=' * 60)
    print('  경기북부 시군별 기상·산림·하천 위험도 수집')
    print('=' * 60)
    print()

    fire_by_region = fetch_all_fire()
    print()

    regions_data = {}
    for rkey, info in REGIONS.items():
        print(f'[{info["name"]}]')
        current = fetch_current(info['nx'], info['ny'])
        if current:
            print(f'  ✓ 실황: {current.get("temp")}°C, 강수 {current.get("rain", 0)}mm/h')
        forecast = fetch_forecast(info['nx'], info['ny'])
        if forecast:
            max_pop = max((f['pop'] for f in forecast), default=0)
            rain_h = sum(1 for f in forecast if f['pcp'] > 0)
            print(f'  ✓ 12h 예보: 비 {rain_h}h, 최대 강수확률 {max_pop}%')
        fire = fire_by_region.get(rkey)
        if fire:
            print(f'  ✓ 산불: {fire["levelText"]}')

        # 하천 — 시군별 첫 관측소 (또는 가장 위험한 관측소)
        river = None
        for code in info.get('rivers', []):
            r = fetch_river_data(code)
            if r and (river is None or r['level'] > river['level']):
                river = r
        if river:
            print(f'  ✓ 하천: {river["name"]} {river["wl"]}m ({river["status"]})')

        regions_data[rkey] = {
            'current': current,
            'forecast': forecast,
            'fire': fire,
            'river': river,
        }
        print()

    # 일산 = 고양 alias
    if 'goyang' in regions_data:
        regions_data['ilsan'] = regions_data['goyang']
        print('일산: 고양시 데이터 alias')

    out = {
        'updatedAt': datetime.now().strftime('%Y-%m-%d %H:%M'),
        'regions': regions_data,
    }
    js_text = (
        '/* 자동 생성 — tools/update_weather.py 실행시 갱신.\n'
        '   경기북부 시군별 실시간 기상·산림·하천 위험도. */\n\n'
        'window.DDC_WEATHER = ' +
        json.dumps(out, ensure_ascii=False, indent=2) + ';\n'
    )
    OUTPUT_PATH.write_text(js_text, encoding='utf-8')
    print()
    print(f'weather.js 저장 완료 ({len(js_text):,} bytes)')


if __name__ == '__main__':
    main()
