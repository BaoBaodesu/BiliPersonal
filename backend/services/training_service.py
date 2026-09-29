"""
模型生命周期：
- 启动 / 登录后在后台线程准备模型：saved_model 与 history_hash 匹配时直接加载，否则训练。
- 历史数据超过 HISTORY_TTL 才重新抓取；数据指纹变化才重训；用户可手动重训。
- 训练期间继续使用旧模型提供推荐，训练完成后原子替换。
"""

import hashlib
import json
import os
import threading
import time
import traceback

from backend.config import FAV_MAX, HISTORY_LEN, HISTORY_PATH, HISTORY_TTL, MODEL_DIR
from backend.services.bilibili_service import BiliError, LoginExpired, bili

META_PATH = os.path.join(MODEL_DIR, "meta.json")
WEIGHTS_PATH = os.path.join(MODEL_DIR, "best_model.weights.h5")
PROCESSOR_PATH = os.path.join(MODEL_DIR, "feature_processor.pkl")


def history_hash(history):
    """数据指纹：BVID + progress + isliked + isfaved"""
    keys = sorted(
        f"{v.get('bvid')}|{v.get('progress')}|{v.get('isliked')}|{v.get('isfaved')}" for v in history
    )
    return hashlib.sha256("\n".join(keys).encode("utf-8")).hexdigest()[:16]


def read_meta():
    if not os.path.exists(META_PATH):
        return {}
    with open(META_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


class TrainingService:
    def __init__(self):
        self.recommender = None
        self.model_version = None
        # none / collecting / training / ready / error
        self.stage = "none"
        self.progress = {"epoch": 0, "total": 0}
        self.error = None
        self.history_samples = 0
        self._lock = threading.Lock()
        self._thread = None
        self._listeners = []

    def on_model_changed(self, callback):
        self._listeners.append(callback)

    @property
    def busy(self):
        return self._thread is not None and self._thread.is_alive()

    def status(self):
        meta = read_meta()
        return {
            "model": "ready" if self.recommender else self.stage,
            "stage": self.stage,
            "training": self.stage in ("collecting", "training"),
            "progress": self.progress,
            "error": self.error,
            "model_version": self.model_version,
            "trained_at": meta.get("trained_at"),
            "history_hash": meta.get("history_hash"),
            "history_samples": self.history_samples,
            "history_updated_at": os.path.getmtime(HISTORY_PATH) if os.path.exists(HISTORY_PATH) else None,
            "summary": meta.get("summary"),
        }

    def reset(self):
        """退出登录时调用：清空内存中的模型"""
        with self._lock:
            self.recommender = None
            self.model_version = None
            self.stage = "none"
            self.progress = {"epoch": 0, "total": 0}
            self.error = None

    def ensure_started(self, force_refresh=False, force_train=False):
        """幂等：已有后台任务在跑时直接返回"""
        with self._lock:
            if self.busy:
                return False
            self._thread = threading.Thread(
                target=self._run, args=(force_refresh, force_train), name="model-lifecycle", daemon=True
            )
            self._thread.start()
            return True

    # ---------------- 后台流程 ----------------

    def _run(self, force_refresh, force_train):
        try:
            from backend.recommender.recommender import Recommender, read_history

            history = read_history()
            meta = read_meta()

            # 1) 先用已保存的模型快速就绪（指纹匹配时）
            if self.recommender is None and history and self._model_files_exist():
                if meta.get("history_hash") == history_hash(history):
                    self._load(Recommender, history, meta)

            # 2) 历史数据过期或不存在时重新抓取
            stale = not history or time.time() - os.path.getmtime(HISTORY_PATH) > HISTORY_TTL
            if force_refresh or stale:
                self.stage = "collecting"
                try:
                    history = bili.collect_training_history(HISTORY_LEN, FAV_MAX)
                except LoginExpired:
                    raise
                except BiliError as e:
                    print(f"历史数据抓取失败，继续使用本地数据：{e}")
                    history = read_history()
            self.history_samples = len(history)
            if not history:
                raise RuntimeError("没有可用的历史数据（观看历史与收藏均为空）")

            # 3) 指纹变化或手动要求时重训，否则直接加载
            new_hash = history_hash(history)
            if not force_train and meta.get("history_hash") == new_hash and self._model_files_exist():
                if self.recommender is None:
                    self._load(Recommender, history, meta)
                self.stage = "ready"
                return

            self.stage = "training"
            self.progress = {"epoch": 0, "total": 30}

            def on_epoch(epoch, total, loss, metrics):
                self.progress = {"epoch": epoch, "total": total, "loss": loss}

            recommender, summary = Recommender.train(progress_callback=on_epoch)
            meta = {
                "history_hash": new_hash,
                "max_tags": recommender.max_tags,
                "trained_at": time.time(),
                "summary": summary,
            }
            with open(META_PATH, "w", encoding="utf-8") as f:
                json.dump(meta, f, ensure_ascii=False, indent=2)
            self._set_recommender(recommender, meta)
            self.stage = "ready"
        except LoginExpired:
            self.stage = "error"
            self.error = "login_expired"
        except Exception as e:
            traceback.print_exc()
            self.stage = "ready" if self.recommender else "error"
            self.error = str(e)

    def _model_files_exist(self):
        return os.path.exists(WEIGHTS_PATH) and os.path.exists(PROCESSOR_PATH)

    def _load(self, Recommender, history, meta):
        max_tags = meta.get("max_tags") or max(len(v["tag"]) for v in history)
        try:
            recommender = Recommender.load(max_tags)
        except Exception as e:
            print(f"加载已保存模型失败，将重新训练：{e}")
            return
        self.history_samples = len(history)
        self._set_recommender(recommender, meta)
        self.stage = "ready"
        print("已从 saved_model 直接加载模型")

    def _set_recommender(self, recommender, meta):
        self.recommender = recommender
        self.model_version = time.strftime("%Y%m%d-%H%M%S", time.localtime(meta.get("trained_at") or time.time()))
        self.error = None
        for callback in self._listeners:
            try:
                callback()
            except Exception:
                traceback.print_exc()


training = TrainingService()
