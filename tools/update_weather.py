# -*- coding: utf-8 -*-
"""동두천시 기상·산림·하천 위험도 수집기 → weather.js

데이터 소스 (모두 공공데이터포털 .env의 DATA_GO_KR_KEY / HRFCO_KEY 사용):
- 기상청 초단기실황: 현재 기온·강수·풍속·습도·강수형태
- 기상청 단기예보: 다음 12시간 강수확률·강수량
- 산림청 V2: 동두천시 산불위험 등급
- 한강홍수통제소: 신천 송천교 수위 + 상태

출력: 프로젝트 루트의 weather.js
실행: python tools/update_weather.py
"""
import json
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
DATA_KEY = ENV.get('DATA_GO_KR_KEY', '')
HRFCO_KEY = ENV.get('HRFCO_KEY', '')

# 동두천 기상청 격자 좌표 + 산불 시군구명 + HRFCO 관측소 코드
DDC_NX, DDC_NY = 61, 134
DDC_SIGUN = '동두천시'
RIVER_CODE = '1022668'      # 신천 송천교

PTY_TXT = {
    '0': '없음', '1': '비', '2': '비/눈', '3': '눈',
    '4': '소나기', '5': '빗방울', '6': '진눈깨비', '7': '눈날림',
}
PTY_ICON = {
    '0': '🌤', '1': '🌧', '2': '🌨', '3': '❄',
    '4': '⛈', '5': '🌦', '6': '🌨', '7': '🌨',
}


def fcst_base_time():
    """단기예보 발표시각: 02/05/08/11/14/17/20/23. 발표 직후엔 직전 회차."""
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
# 1. 현재 실황 (초단기실황)
# ============================================================

def fetch_current():
    print('[현재 실황]')
    if not DATA_KEY:
        print('  ! DATA_GO_KR_KEY 없음')
        return None
    base = datetime.now() - timedelta(minutes=40)
    url = 'https://apis.data.go.kr/1360000/VilageFcstInfoService_2.0/getUltraSrtNcst'
    params = {
        'serviceKey': DATA_KEY,
        'pageNo': 1, 'numOfRows': 30, 'dataType': 'JSON',
        'base_date': base.strftime('%Y%m%d'),
        'base_time': base.strftime('%H00'),
        'nx': DDC_NX, 'ny': DDC_NY,
    }
    try:
        r = requests.get(url, params=params, timeout=15)
        r.raise_for_status()
        items = r.json()['response']['body']['items']['item']
    except Exception as e:
        print(f'  ✗ {e}')
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

    print(f'  ✓ {obs.get("temp")}°C, 강수 {obs.get("rain", 0)}mm/h, 습도 {obs.get("humid")}%, 풍속 {obs.get("wind")}m/s')
    return obs


# ============================================================
# 2. 단기예보 12시간
# ============================================================

def fetch_forecast():
    print('[12시간 예보]')
    if not DATA_KEY:
        return []
    base_date, base_time = fcst_base_time()
    url = 'https://apis.data.go.kr/1360000/VilageFcstInfoService_2.0/getVilageFcst'
    params = {
        'serviceKey': DATA_KEY,
        'pageNo': 1, 'numOfRows': 800, 'dataType': 'JSON',
        'base_date': base_date, 'base_time': base_time,
        'nx': DDC_NX, 'ny': DDC_NY,
    }
    try:
        r = requests.get(url, params=params, timeout=15)
        r.raise_for_status()
        items = r.json()['response']['body']['items']['item']
    except Exception as e:
        print(f'  ✗ {e}')
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
    rain_hours = sum(1 for r in result if r['pcp'] > 0)
    max_pop = max((r['pop'] for r in result), default=0)
    print(f'  ✓ {len(result)}시간 — 비 예상 {rain_hours}h, 최대 강수확률 {max_pop}%')
    return result


# ============================================================
# 3. 산불위험 V2 — 동두천시
# ============================================================

def fetch_fire():
    print('[산불위험]')
    if not DATA_KEY:
        return None
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
        return None

    for it in items:
        if str(it.get('doname', '')) != '경기도':
            continue
        if str(it.get('sigun', '')).strip() != DDC_SIGUN:
            continue
        d2 = float(it.get('d2', 0) or 0)
        d3 = float(it.get('d3', 0) or 0)
        d4 = float(it.get('d4', 0) or 0)
        if d4 > 0:   lv, txt = 4, '매우높음'
        elif d3 > 0: lv, txt = 3, '높음'
        elif d2 > 0: lv, txt = 2, '보통'
        else:        lv, txt = 1, '낮음'
        print(f'  ✓ {DDC_SIGUN}: {txt} (Lv {lv})')
        return {'level': lv, 'levelText': txt}

    print('  ✗ 동두천시 항목 없음')
    return None


# ============================================================
# 4. 신천 송천교 수위 (HRFCO)
# ============================================================

def fetch_river():
    print('[하천 수위 — 신천 송천교]')
    if not HRFCO_KEY:
        return None
    now = datetime.now()
    sdt = (now - timedelta(hours=3)).strftime('%Y%m%d%H')
    edt = now.strftime('%Y%m%d%H')

    sess = requests.Session()
    sess.headers.update({'User-Agent': 'Mozilla/5.0'})
    for attempt in range(4):
        try:
            url = f'http://api.hrfco.go.kr/{HRFCO_KEY}/waterlevel/list/1H/{RIVER_CODE}/{sdt}/{edt}.json'
            r = sess.get(url, timeout=30)
            r.raise_for_status()
            data = r.json().get('content', [])
            if not data:
                print('  ✗ 데이터 없음')
                return None
            latest = data[-1]
            wl = float(latest.get('wl', 0))
            # 송천교 임계값: 주의 3.4 / 경계 4.0 / 경보 5.0
            if wl >= 5.0:   status, lv = '경보', 3
            elif wl >= 4.0: status, lv = '경계', 2
            elif wl >= 3.4: status, lv = '주의', 1
            else:           status, lv = '정상', 0
            print(f'  ✓ {wl}m ({status})')
            return {
                'name': '신천 송천교',
                'wl': wl,
                'status': status,
                'level': lv,
                'attwl': 3.4, 'wrnwl': 4.0, 'almwl': 5.0,
            }
        except Exception as e:
            print(f'  재시도 {attempt + 1}: {type(e).__name__}')
            time.sleep(2 + attempt)
    return None


# ============================================================
# 메인
# ============================================================

def main():
    print('=' * 60)
    print('  동두천시 기상·산림·하천 위험도 수집')
    print('=' * 60)
    print()

    weather = {
        'updatedAt': datetime.now().strftime('%Y-%m-%d %H:%M'),
        'current': fetch_current(),
        'forecast': fetch_forecast(),
        'fire': fetch_fire(),
        'river': fetch_river(),
    }

    js_text = (
        '/* 자동 생성 — tools/update_weather.py 실행시 갱신.\n'
        '   동두천시 실시간 기상·산림·하천 위험도. */\n\n'
        'window.DDC_WEATHER = ' +
        json.dumps(weather, ensure_ascii=False, indent=2) + ';\n'
    )
    OUTPUT_PATH.write_text(js_text, encoding='utf-8')
    print()
    print(f'weather.js 저장 완료 ({len(js_text):,} bytes)')


if __name__ == '__main__':
    main()
