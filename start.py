"""One-command local startup; never auto-runs paid models."""
import argparse
import threading
import time
import urllib.request
import webbrowser
import uvicorn


def open_when_ready(port):
    url = f'http://127.0.0.1:{port}'
    client = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for _ in range(100):
        try:
            with client.open(url + '/api/health', timeout=1) as response:
                if response.status == 200:
                    webbrowser.open(url)
                    return
        except OSError:
            pass
        time.sleep(0.1)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='兵棋多模态竞赛测评系统')
    parser.add_argument('--port',type=int,default=8000)
    parser.add_argument('--open-browser', action='store_true', help='服务就绪后打开浏览器')
    args = parser.parse_args()
    if args.open_browser:
        threading.Thread(target=open_when_ready, args=(args.port,), daemon=True).start()
    uvicorn.run('app.main:app',host='127.0.0.1',port=args.port,workers=1,access_log=False)
