"""
后端统一配置：所有路径都基于项目根目录的绝对路径，避免依赖启动时的工作目录。
"""

import os

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 与原版保持一致的数据文件位置（推荐模型的输入契约）
COOKIE_PATH = os.path.join(ROOT_DIR, "user_data", "cookie.txt")
HISTORY_PATH = os.path.join(ROOT_DIR, "historyVideo.json")
MODEL_DIR = os.path.join(ROOT_DIR, "saved_model")

# v0.2 新增：SQLite 本地数据库
DATA_DIR = os.path.join(ROOT_DIR, "backend", "data")
DB_PATH = os.path.join(DATA_DIR, "app.db")

# React 构建产物
FRONTEND_DIST = os.path.join(ROOT_DIR, "frontend", "dist")

HOST = "127.0.0.1"
PORT = 8345

# 原版训练参数：历史记录条数（收藏另取最多 20 条）
HISTORY_LEN = 10
FAV_MAX = 20

# 历史记录多久重新抓取一次（秒）
HISTORY_TTL = 6 * 3600
# 候选池 TTL（秒）
POOL_TTL = 20 * 60
# 候选池条目保留时间（秒），超过则清理
POOL_KEEP = 12 * 3600
# 已展示视频在该时间窗口内不再重复推荐（秒）
SERVED_WINDOW = 24 * 3600
# 手动刷新候选池的最小间隔（秒），避免频繁请求 Bilibili
POOL_MIN_REFRESH_INTERVAL = 5 * 60
# 触发 -352 后的退避时间（秒）
RATE_LIMIT_BACKOFF = 90
