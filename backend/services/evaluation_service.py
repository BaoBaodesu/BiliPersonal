"""冻结的前向评价及配对非劣检验；不足时等待，不制造负例。"""
import json
import time
import uuid

import numpy as np
from sklearn.metrics import average_precision_score, roc_auc_score

from backend.services.model_registry import registry, atomic_json
from backend.services.training_data import PROTOCOL, build_samples
from backend.storage.database import connect

SEED = 20260930
BOOTSTRAPS = 10000


def metrics(labels, predictions, weights):
    y, p, w = map(lambda x: np.asarray(x, dtype=float), (labels, predictions, weights))
    if not len(y) or len(y) != len(p) or len(w) != len(y) or not np.isfinite(p).all() or not np.isfinite(w).all() or (w <= 0).any() or (p < 0).any() or (p > 1).any():
        raise ValueError("无效预测或证据权重")
    q = np.clip(p, 1e-7, 1 - 1e-7)
    order = np.argsort(-p, kind="stable")[:10]
    return {"loss": float(np.average(-(y * np.log(q) + (1-y) * np.log(1-q)), weights=w)),
            "AP": float(average_precision_score(y, p)) if len(np.unique(y)) == 2 else None,
            "AUC": float(roc_auc_score(y, p)) if len(np.unique(y)) == 2 else None,
            "Precision@10": float(y[order].mean()),
            "Recall@10": float(y[order].sum()/y.sum()) if y.sum() else None}


def compare(samples, versions, candidate, current, anchor):
    y = np.array([s["label"] for s in samples])
    weights = [s["weight"] for s in samples]
    if len(samples) < 100 or min(int(y.sum()), int((1-y).sum())) < 20:
        return {"status": "waiting", "samples": len(samples), "positives": int(y.sum()), "negatives": int((1-y).sum())}
    if len({s["bvid"] for s in samples}) != len(samples):
        raise ValueError("评价视频重复")
    predictions = {v: np.array([s["predictions"][v] for s in samples]) for v in versions}
    report = {v: metrics(y, p, weights) for v, p in predictions.items()}
    positives, negatives = np.flatnonzero(y == 1), np.flatnonzero(y == 0)
    rng = np.random.default_rng(SEED)
    delta = {v: [] for v in {current, anchor} if v}
    for _ in range(BOOTSTRAPS):
        indices = np.concatenate([rng.choice(positives, len(positives)), rng.choice(negatives, len(negatives))])
        candidate_ap = average_precision_score(y[indices], predictions[candidate][indices])
        for v in delta:
            delta[v].append(candidate_ap - average_precision_score(y[indices], predictions[v][indices]))
    bounds = {v: float(np.quantile(values, .05)) for v, values in delta.items()}
    passed = (report[candidate]["AP"] >= report[current]["AP"] and all(b >= -.01 for b in bounds.values())
              and report[candidate]["loss"] <= 1.02 * report[current]["loss"])
    sources = {}
    for source in sorted({s["evidence"] for s in samples}):
        subset = [s for s in samples if s["evidence"] == source]
        sources[source] = {v: metrics([s["label"] for s in subset], [s["predictions"][v] for s in subset], [s["weight"] for s in subset]) for v in versions}
    return {"status": "passed" if passed else "rejected", "metrics": report, "delta_AP_lower_95_one_sided": bounds,
            "samples": len(samples), "seed": SEED, "resamples": BOOTSTRAPS, "protocol": PROTOCOL,
            "sources": sources, "interpretation": "非劣通过不证明推荐提升"}


class EvaluationService:
    def reject_error(self, pair, reason):
        path = registry.root / "evaluations" / pair["id"]
        report = {"status": "rejected", "reason": str(reason), "protocol": pair["protocol"]}
        with registry._lock:
            state = registry.state()
            current = state.get("evaluation")
            if not current or current["id"] != pair["id"]:
                atomic_json(path / "error.json", report)
                return report
            atomic_json(path / ("report.json" if current["status"] == "waiting" else "error.json"), report)
            registry.update(evaluation={**current,"status":"rejected","sealed_at":time.time(),"path":str(path)}, auto_paused=True)
        with connect() as conn:
            conn.execute("UPDATE model_runs SET status='rejected' WHERE model_version=?", (pair["candidate"],))
        if registry.state().get("trial"):
            from backend.services.experiment_service import experiments
            experiments.stop("模型工件或预测错误")
        return report

    def start(self, candidate):
        state = registry.state()
        if state.get("evaluation"):
            with connect() as conn:
                if state.get("pending"):
                    conn.execute("UPDATE model_runs SET status='superseded' WHERE model_version=? AND status='queued'", (state["pending"],))
                conn.execute("UPDATE model_runs SET status='queued' WHERE model_version=?", (candidate,))
            registry.update(pending=candidate)
            return
        current, anchor = state.get("active"), state.get("anchor")
        versions = list(dict.fromkeys(v for v in (candidate, current, anchor) if v))
        if not current or current == candidate:
            return
        metadata = {v: registry.metadata(v) for v in versions}
        with connect() as conn:
            conn.execute("UPDATE model_runs SET status='evaluating' WHERE model_version=?", (candidate,))
        registry.update(evaluation={"id": uuid.uuid4().hex, "candidate": candidate, "current": current,
                                   "anchor": anchor, "versions": versions, "frozen_at": time.time(),
                                   "cutoff": max(m.get("data_cutoff_at") or 0 for m in metadata.values()),
                                   "excluded": sorted(set(state.get("consumed_bvids", [])) | {b for m in metadata.values() for b in m.get("train_bvids", []) + m.get("validation_bvids", [])}),
                                   "status": "waiting", "protocol": PROTOCOL}, pending=None)

    def predict(self, videos):
        pair = registry.state().get("evaluation")
        if not pair or pair["status"] != "waiting":
            return {}
        predictions = {}
        for version in pair["versions"]:
            try:
                scored = registry.load(version).score(videos)
                if any(not np.isfinite(value) or not 0 <= value <= 1 for _,value,_ in scored):
                    raise ValueError("无效冻结预测")
            except Exception as error:
                self.reject_error(pair, error)
                return {}
            for video, value, _ in scored:
                predictions.setdefault(video["bvid"], {})[version] = value
        return predictions

    def collect(self, pair):
        boundary = max(pair["cutoff"], pair["frozen_at"])
        samples = {s["bvid"]: s for s in build_samples() if s["bvid"] not in pair["excluded"]
                   and s.get("event_at") and s["event_at"] > boundary}
        # 标签统一来自证据协议，避免点击重新覆盖最新可靠观看结局。
        with connect() as conn:
            rows = conn.execute("SELECT bvid,video_snapshot,predictions,served_at FROM recommendation_history WHERE served_at>? ORDER BY served_at,id", (boundary,)).fetchall()
        result, seen = [], set()
        for row in rows:
            bvid = row["bvid"]
            prediction = json.loads(row["predictions"] or "{}")
            if bvid in seen or bvid not in samples or not all(v in prediction for v in pair["versions"]) or row["served_at"] >= samples[bvid]["event_at"]:
                continue
            seen.add(bvid)
            result.append({**samples[bvid], "video": json.loads(row["video_snapshot"]), "predictions": prediction})
        return result

    def check(self):
        state = registry.state()
        pair = state.get("evaluation")
        if not pair or pair["status"] != "waiting":
            return pair
        try:
            for version in pair["versions"]:
                registry.metadata(version)
        except Exception as error:
            return self.reject_error(pair, error)
        samples = self.collect(pair)
        try:
            report = compare(samples, pair["versions"], pair["candidate"], pair["current"], pair["anchor"])
        except (ValueError, KeyError) as error:
            return self.reject_error(pair, error)
        with connect() as conn:
            predictions = conn.execute("SELECT bvid,predictions FROM recommendation_history WHERE served_at>?", (pair["frozen_at"],)).fetchall()
        total = len({r["bvid"] for r in predictions})
        report["coverage"] = {v: {"scored_videos":len({r["bvid"] for r in predictions if v in json.loads(r["predictions"] or "{}")}), "generated_videos":total} for v in pair["versions"]}
        if report["status"] == "waiting":
            return report
        # 重采样不占用注册表锁；封存前确认同一轮仍在等待，避免覆盖并发工件错误。
        with registry._lock:
            latest = registry.state()
            current = latest.get("evaluation")
            if not current or current["id"] != pair["id"] or current["status"] != "waiting":
                return current
            path = registry.root / "evaluations" / pair["id"]
            # 一次封存后不回写标签，也不复用作下一轮评价。
            atomic_json(path / "snapshot.json", samples)
            atomic_json(path / "report.json", report)
            pair = {**pair, "status": report["status"], "sealed_at": time.time(), "path": str(path)}
            with connect() as conn:
                conn.execute("UPDATE model_runs SET status=? WHERE model_version=?", ("validated" if report["status"] == "passed" else "rejected", pair["candidate"]))
            registry.update(evaluation=pair, consumed_bvids=sorted(set(latest.get("consumed_bvids", [])) | {s["bvid"] for s in samples}),
                            auto_paused=latest.get("auto_paused", False) or report["status"] == "rejected")
        return report

    def finish(self):
        state = registry.state()
        pair = state.get("evaluation")
        if state.get("trial"):
            raise ValueError("在线试用尚未结束，不能替换冻结模型对")
        ended = state.get("last_trial")
        if pair and pair["status"] == "waiting" and ended and ended["evaluation_id"] == pair["id"] and ended["status"] == "stopped":
            samples = self.collect(pair)
            path = registry.root / "evaluations" / pair["id"]
            atomic_json(path / "snapshot.json", samples)
            atomic_json(path / "report.json", {"status":"stopped","reason":"人工提前停止，未作发布判定","samples":len(samples)})
            pair = {**pair,"status":"stopped","sealed_at":time.time(),"path":str(path)}
            registry.update(evaluation=pair, consumed_bvids=sorted(set(state.get("consumed_bvids", [])) | {s["bvid"] for s in samples}))
        if not pair or pair["status"] == "waiting":
            raise ValueError("当前前向评价尚未封存")
        registry.update(evaluation=None, pending=None)
        if state.get("pending"):
            self.start(state["pending"])


evaluation = EvaluationService()
