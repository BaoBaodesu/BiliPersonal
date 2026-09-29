# 旧版 Jinja 页面（仅供对照）：需在项目根目录运行 python legacy/run_legacy.py
import os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import create_app

app = create_app()

if __name__ == "__main__":
    # app.run(debug=True, host="0.0.0.0", port=8345)
    app.run(debug=True, host="127.0.0.1", port=8345)
