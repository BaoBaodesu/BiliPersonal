"""不可变模型工件及原子活动指针，不覆盖 v0.2 文件。"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import threading
import uuid

from backend.config import MODEL_DIR, HISTORY_PATH
from backend.services import feed_performance as perf


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temporary.open("w", encoding="utf-8") as file:
            json.dump(value, file, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def digest(path):
    with Path(path).open("rb") as file:
        return hashlib.file_digest(file, "sha256").hexdigest()


class ModelRegistry:
    def __init__(self, root=MODEL_DIR):
        self.root = Path(root)
        self._loaded = {}
        self._lock = threading.RLock()

    def directory(self, version):
        if not version or not all(c.isalnum() or c in "-_" for c in version):
            raise ValueError("非法模型版本")
        return self.root / "versions" / version

    def state(self):
        path = self.root / "active.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}

    def update(self, **changes):
        with self._lock:
            state = {**self.state(), **changes}
            atomic_json(self.root / "active.json", state)
            return state

    def capture_baseline(self):
        files = ("best_model.weights.h5", "feature_processor.pkl", "meta.json")
        if not all((self.root / name).exists() for name in files):
            return None
        version = "baseline-v02-" + hashlib.sha256("".join(digest(self.root / name) for name in files).encode()).hexdigest()[:16]
        target = self.directory(version)
        if not target.exists():
            temporary = target.with_name(target.name + "-staging-" + uuid.uuid4().hex)
            temporary.mkdir(parents=True)
            for name in files:
                shutil.copy2(self.root / name, temporary / name)
            if Path(HISTORY_PATH).exists():
                shutil.copy2(HISTORY_PATH, temporary / "history.json")
            # 无法证明旧 checkpoint 的训练截止时间；旧快照中出现的视频全部隔离。
            history = json.loads((temporary / "history.json").read_text(encoding="utf-8")) if (temporary / "history.json").exists() else []
            atomic_json(temporary / "bundle.json", {"version": version, "kind": "legacy",
                        "checksums": {name: digest(temporary / name) for name in files}, "policy": "legacy",
                        "train_bvids": [v["bvid"] for v in history], "data_cutoff_at": None})
            try:
                os.replace(temporary, target)
            except FileExistsError:
                shutil.rmtree(temporary)
        if not self.state().get("active"):
            self.update(active=version, baseline=version, previous=None, anchor=None,
                        pending=None, trial=None, auto_paused=False)
        return version

    def metadata(self, version):
        directory = self.directory(version)
        metadata = json.loads((directory / "bundle.json").read_text(encoding="utf-8"))
        if metadata["version"] != version:
            raise ValueError("工件版本不一致")
        for name, checksum in metadata["checksums"].items():
            if Path(name).name != name or digest(directory / name) != checksum:
                raise ValueError("模型工件校验失败")
        return metadata

    @perf.measured("model_load_ms")
    def load(self, version):
        if version == "fallback-v03":
            return None
        with self._lock:
            if version in self._loaded:
                return self._loaded[version]
            metadata = self.metadata(version)
            if metadata["kind"] == "legacy":
                from backend.recommender.model import load_model_and_processor
                from backend.recommender.recommender import Recommender
                directory = self.directory(version)
                model, processor = load_model_and_processor(str(directory))
                meta = json.loads((directory / "meta.json").read_text(encoding="utf-8"))
                bundle = Recommender(model, processor, meta["max_tags"])
            else:
                from backend.recommender.ranker_v03 import Ranker
                bundle = Ranker.load(self.directory(version))
            import numpy as np
            if not all(np.isfinite(w.numpy()).all() for w in bundle.model.weights):
                raise ValueError("模型权重包含非有限值")
            bundle.model_version = version
            bundle.policy = metadata.get("policy", "base")
            self._loaded[version] = bundle
            return bundle

    def activate(self, version, anchor=False):
        self.load(version)
        state = self.state()
        self.update(active=version, previous=state.get("active"), trial=None,
                    anchor=version if anchor else state.get("anchor"))


registry = ModelRegistry()
