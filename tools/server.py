# -*- coding: utf-8 -*-
"""사내망용 미니 HTTP 서버 — 동두천시 재난안전지도.

사장님 컴퓨터에서 실행하면 같은 네트워크의 모든 컴퓨터/핸드폰에서
http://[사장님IP]:8001/  주소로 지도에 접속할 수 있다.

실행: python tools/server.py
또는: 서버시작.bat 더블클릭
"""
import http.server
import socket
import socketserver
import sys
import webbrowser
from pathlib import Path

PORT = 8001  # 기존 상황판(8000)과 겹치지 않도록 8001
ROOT = Path(__file__).resolve().parent.parent

try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, format, *args):
        msg = format % args
        if any(ext in msg for ext in ['.css', '.js HTTP', '.png', '.jpg', '.ico']):
            return
        print(f'  · {self.address_string()} → {msg}')

    def end_headers(self):
        # 캐시 비활성화: data.js 갱신되면 즉시 반영
        self.send_header('Cache-Control', 'no-store, no-cache, must-revalidate')
        self.send_header('Pragma', 'no-cache')
        self.send_header('Expires', '0')
        super().end_headers()


def get_local_ips():
    ips = set()
    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None):
            ip = info[4][0]
            if ip.startswith(('192.168.', '10.', '172.')):
                ips.add(ip)
    except Exception:
        pass
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('8.8.8.8', 80))
        ips.add(s.getsockname()[0])
        s.close()
    except Exception:
        pass
    return sorted(ips)


def main():
    import os
    os.chdir(ROOT)

    ips = get_local_ips()
    print('=' * 60)
    print('  동두천시 재난안전지도 — 사내망 서버 시작')
    print('=' * 60)
    print()
    print('  [사장님 컴퓨터에서 접속]')
    print(f'    http://localhost:{PORT}/')
    print()
    if ips:
        print('  [동료 컴퓨터·핸드폰에서 접속 — 같은 사내망]')
        for ip in ips:
            print(f'    http://{ip}:{PORT}/')
    else:
        print('  ! 사내망 IP를 자동으로 찾지 못했습니다.')
        print('    cmd 창에서 `ipconfig`를 입력해 IPv4 주소를 확인하세요.')
    print()
    print('  [종료] 이 창을 닫거나 Ctrl+C')
    print('=' * 60)
    print()

    try:
        with socketserver.ThreadingTCPServer(('', PORT), QuietHandler) as httpd:
            print(f'서버 가동 중 ... (포트 {PORT})')
            try:
                webbrowser.open(f'http://localhost:{PORT}/')
            except Exception:
                pass
            httpd.serve_forever()
    except OSError as e:
        print(f'\n[오류] 포트 {PORT}를 이미 사용 중입니다. 다른 서버가 켜져 있을 수 있어요.')
        print(f'      자세한 내용: {e}')
        input('\n아무 키나 누르면 종료...')
    except KeyboardInterrupt:
        print('\n서버 종료')


if __name__ == '__main__':
    main()
