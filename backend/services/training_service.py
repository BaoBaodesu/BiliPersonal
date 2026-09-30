"""后台收集和训练；活动模型与候选模型独立。"""
import json
import os
import threading
import time
import traceback
import uuid
from backend.config import HISTORY_PATH, MODEL_DIR
from backend.services.bilibili_service import bili, LoginExpired, RateLimited
from backend.services.model_registry import registry
from backend.services.training_data import PROTOCOL, build_samples, fingerprint
from backend.storage.database import connect, get_state


def read_meta():
    try:
        with open(os.path.join(MODEL_DIR, "meta.json"), encoding="utf-8") as file:
            return json.load(file)
    except (OSError, ValueError):
        return {}


class TrainingService:
    def __init__(self):
        self.recommender = None
        self.model_version = None
        self.stage = "none"
        self.progress = {"epoch": 0, "total": 0}
        self.error = None
        self.history_samples = 0
        self._lock = threading.RLock()
        self._thread = None
        self._listeners = []
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._force_refresh = False
        self._force_train = False

    def on_model_changed(self, callback):
        self._listeners.append(callback)

    @property
    def busy(self):
        return self.stage in ("collecting", "training")

    def status(self):
        meta = read_meta()
        state = registry.state()
        if self.model_version and not self.model_version.startswith("baseline-"):
            try:
                bundle = registry.metadata(self.model_version)
                meta = {"trained_at":bundle["created_at"], "history_hash":bundle["data_hash"],
                        "summary":{"samples":bundle["samples"],"positives":bundle["positives"],
                                   "best_loss":bundle["metrics"]["loss"],"final_metrics":bundle["metrics"],
                                   "num_tags":len(self.recommender.processor.tag2idx),"num_authors":len(self.recommender.processor.author2idx)}}
            except (OSError, ValueError, KeyError) as error:
                self.error = str(error)
        return {"model": "ready" if self.recommender else self.stage, "stage": self.stage,
                "training": self.busy, "progress": self.progress, "error": self.error,
                "model_version": self.model_version, "trained_at": meta.get("trained_at"),
                "history_hash": meta.get("history_hash"), "history_samples": self.history_samples,
                "history_updated_at": os.path.getmtime(HISTORY_PATH) if os.path.exists(HISTORY_PATH) else None,
                "summary": meta.get("summary"), "v03":state}

    def reset(self):
        self._stop.set()
        self._wake.set()
        with self._lock:
            self.recommender = None
            self.model_version = None
            self.stage = "none"

    def ensure_started(self, force_refresh=False, force_train=False):
        with self._lock:
            self._force_refresh |= force_refresh
            self._force_train |= force_train
            self._wake.set()
            if self._thread and self._thread.is_alive():
                if self._stop.is_set():
                    worker = self._thread
                    def resume():
                        worker.join()
                        self.ensure_started()
                    threading.Thread(target=resume,name="model-resume",daemon=True).start()
                    return True
                return False
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, name="model-lifecycle", daemon=True)
            self._thread.start()
            return True

    def _load_active(self):
        registry.capture_baseline()
        state = registry.state()
        failures = []
        for version in dict.fromkeys(v for v in (state.get("active"), state.get("previous"), state.get("baseline")) if v):
            try:
                bundle = registry.load(version)
                if bundle is None:
                    continue
                if self._stop.is_set():
                    return
                if version != state.get("active"):
                    registry.update(active=version, auto_paused=True, load_error="; ".join(failures))
                with self._lock:
                    self.recommender, self.model_version = bundle, version
                self.stage = "ready"
                self.history_samples = registry.metadata(version).get("samples") or read_meta().get("summary", {}).get("samples", 0)
                self.error = "; ".join(failures) or None
                for callback in self._listeners:
                    try:
                        callback()
                    except Exception:
                        traceback.print_exc()
                return
            except Exception as error:
                failures.append(f"{version}: {error}")
        self.stage = "error"
        self.error = "; ".join(failures) or "没有可验证的旧模型，使用候选原始顺序"
        registry.update(active="fallback-v03", previous=state.get("active"), auto_paused=True, load_error=self.error)

    def _cycle(self, force_refresh=False, force_train=False):
        from backend.services.history_sync import sync
        from backend.services.evaluation_service import evaluation
        from backend.recommender.ranker_v03 import Ranker
        self.stage = "collecting"
        sync.run(force=force_refresh, page_budget=2, stop=self._stop)
        if self._stop.is_set():
            return
        cutoff = time.time()
        samples = build_samples(cutoff)
        self.history_samples = len(samples)
        signature = fingerprint(samples)
        with connect() as conn:
            previous = conn.execute("SELECT data_hash FROM model_runs WHERE finished_at IS NOT NULL ORDER BY finished_at DESC LIMIT 1").fetchone()
        if (force_train or get_state("v03_history_sync", {}).get("complete")) and (force_train or not previous or previous[0] != signature) and len(samples) >= 25 and len({s["label"] for s in samples[:int(len(samples)*.8)]}) == 2:
            self.stage = "training"
            def progress(epoch, total, loss, result):
                if self._stop.is_set():
                    raise RuntimeError("训练已取消")
                self.progress = {"epoch": epoch, "total": total, "loss": loss}
            version = "ranker-v03-" + uuid.uuid4().hex[:16]
            with connect() as conn:
                conn.execute("INSERT INTO model_runs(model_version,protocol,status,data_cutoff_at,started_at,finished_at,data_hash,artifact_path,metrics,config) VALUES(?,?,?,?,?,?,?,?,?,?)",
                             (version, PROTOCOL, "training", cutoff, cutoff, None, signature,
                              str(registry.directory(version)), None, json.dumps({"samples":len(samples),"positives":sum(s["label"] for s in samples)})))
            try:
                _, metadata = Ranker.train(samples, registry, cutoff, callback=progress, version=version)
            except Exception as error:
                with connect() as conn:
                    conn.execute("UPDATE model_runs SET status='failed',finished_at=?,metrics=? WHERE model_version=?", (time.time(),json.dumps({"error":str(error)}),version))
                raise
            with connect() as conn:
                conn.execute("UPDATE model_runs SET status='candidate',finished_at=?,metrics=?,config=? WHERE model_version=?", (time.time(),json.dumps(metadata["metrics"]),json.dumps(metadata),version))
            evaluation.start(metadata["version"])
        evaluation.check()
        from backend.services.experiment_service import experiments
        experiments.check()
        self.stage = "ready" if self.recommender else "error"

    def _run(self):
        with connect() as conn:
            conn.execute("UPDATE model_runs SET status='failed',finished_at=?,metrics=? WHERE status='training'", (time.time(),json.dumps({"error":"服务退出导致训练中断"}),))
        try:
            self._load_active()
        except Exception as error:
            self.stage = "error"
            self.error = str(error)
            return
        while not self._stop.is_set():
            with self._lock:
                force_refresh, force_train = self._force_refresh, self._force_train
                self._force_refresh = self._force_train = False
                self._wake.clear()
            try:
                self._cycle(force_refresh, force_train)
            except LoginExpired:
                self.error = "login_expired"
                self.stage = "ready" if self.recommender else "error"
                return
            except Exception as error:
                if self._stop.is_set():
                    return
                traceback.print_exc()
                self.error = str(error)
                self.stage = "ready" if self.recommender else "error"
            self._wake.wait(max(60, bili.rate_limited_until - time.time()))


training = TrainingService()
