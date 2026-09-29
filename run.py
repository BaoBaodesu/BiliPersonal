from backend.app import create_app
from backend.config import HOST, PORT

if __name__ == "__main__":
    # 后台线程持有模型与候选池状态，关闭 reloader 避免启动两份进程
    app = create_app()
    app.run(host=HOST, port=PORT, debug=False, threaded=True, use_reloader=False)
