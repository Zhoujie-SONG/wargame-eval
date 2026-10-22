"""One-command local startup; never auto-runs paid models."""
import argparse
import uvicorn


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='兵棋多模态竞赛测评系统')
    parser.add_argument('--port',type=int,default=8000)
    args = parser.parse_args()
    uvicorn.run('app.main:app',host='127.0.0.1',port=args.port,workers=1,access_log=False)
