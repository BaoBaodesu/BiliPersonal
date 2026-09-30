"""首页全部的等量试用及固定每日结算；人工首次准入与自动护栏分开。"""
import hashlib
import json
import time
import uuid

import numpy as np

from backend.services.model_registry import registry, atomic_json
from backend.services.evaluation_service import evaluation, SEED, BOOTSTRAPS
from backend.services.training_data import PROTOCOL
from backend.storage.database import connect


def product_counts(rows):
    return {arm:{"views":len({r["view_id"] for r in rows if r["arm"] == arm}),
                 "opportunities":sum(r["arm"] == arm for r in rows),
                 "videos":len({r["bvid"] for r in rows if r["arm"] == arm})} for arm in ("current","candidate")}


def product_report(rows):
    groups = {}
    for arm in ("current", "candidate"):
        group = [r for r in rows if r["arm"] == arm]
        views = sorted({r["view_id"] for r in group})
        groups[arm] = {"views": len(views), "opportunities": len(group), "videos": len({r["bvid"] for r in group})}
        for action in ("not_interested", "block_up"):
            groups[arm][action] = sum(r[action] for r in group)/len(group) if group else None
    sufficient = all(g["views"] >= 20 and g["opportunities"] >= 500 and g["videos"] >= 100 for g in groups.values())
    if not sufficient:
        return {"status": "waiting", "groups": groups}
    rng = np.random.default_rng(SEED)
    intervals = {}
    for action in ("not_interested", "block_up"):
        rates = {}
        for arm in groups:
            view_ids = sorted({r["view_id"] for r in rows if r["arm"] == arm})
            clusters = np.array([[sum(r[action] for r in rows if r["arm"] == arm and r["view_id"] == v),
                                  sum(1 for r in rows if r["arm"] == arm and r["view_id"] == v)] for v in view_ids])
            picked = rng.integers(0, len(clusters), (BOOTSTRAPS, len(clusters)))
            totals = clusters[picked].sum(1)
            rates[arm] = totals[:,0]/totals[:,1]
        values = rates["candidate"] - rates["current"]
        interval = np.quantile(values, [.025,.975]).tolist()
        intervals[action] = {"difference_95_interval": interval,
                             "evidence": "不足：零事件或退化区间" if interval[0] == interval[1] or not any(r[action] for r in rows) else "按浏览批次聚类重采样"}
    passed = all(groups["candidate"][a] <= groups["current"][a] for a in ("not_interested", "block_up"))
    return {"status": "passed" if passed else "rejected", "groups": groups, "intervals": intervals,
            "seed": SEED, "resamples": BOOTSTRAPS, "limitations": "单用户共享去重存在组间影响；零反馈不证明风险为零"}


class ExperimentService:
    def start(self, blind_report=None, automatic=False):
        state = registry.state()
        pair = state.get("evaluation")
        if not pair or state.get("trial"):
            raise ValueError("没有待试用的冻结模型对或已有试用")
        if state.get("auto_paused") and automatic:
            raise ValueError("自动晋升已停止，需要人工判断")
        if not state.get("anchor") or state.get("approved_protocol", PROTOCOL) != PROTOCOL:
            if not blind_report or blind_report.get("candidate") != pair["candidate"] or blind_report.get("batches") != 5 or blind_report.get("complete") is not True:
                raise ValueError("首次试用需完成五批盲评并由用户确认")
        elif automatic and pair["status"] != "passed":
            raise ValueError("前向评价尚未通过")
        for version in pair["versions"]:
            registry.load(version)
        started = time.time()
        trial = {"id": uuid.uuid4().hex, "current": pair["current"], "candidate": pair["candidate"],
                 "evaluation_id": pair["id"], "started_at": started, "next_check_at": (int(started//86400)+1)*86400,
                 "status": "running", "protocol": PROTOCOL, "manual_confirmed": not automatic}
        registry.update(trial=trial)
        return trial

    def context(self, feed_type, category, view_id):
        state = registry.state()
        trial = state.get("trial")
        if trial and trial["status"] == "running" and feed_type == "for_you" and category == "all":
            # 每两个新批次分成一对，哈希决定先后，保证数量相等（奇数时差一）。
            with connect() as conn:
                count = conn.execute("SELECT COUNT(*) FROM feed_streams WHERE experiment_id=?", (trial["id"],)).fetchone()[0]
            first = int(hashlib.sha256(f'{trial["id"]}:{count//2}'.encode()).hexdigest(),16)%2
            arm = "candidate" if first ^ (count%2) else "current"
            return {"view_id": view_id, "model_version": trial[arm], "experiment_id": trial["id"], "arm": arm}
        return {"view_id": view_id, "model_version": state.get("active") or "fallback-v03", "experiment_id": None, "arm": None}

    def rows(self, trial):
        with connect() as conn:
            rows = conn.execute("SELECT e.id,e.view_id,r.bvid,r.arm,MAX(CASE WHEN f.action='not_interested' THEN 1 ELSE 0 END) not_interested,MAX(CASE WHEN f.action='block_up' THEN 1 ELSE 0 END) block_up FROM recommendation_exposures e JOIN recommendation_history r ON r.id=e.recommendation_id LEFT JOIN feedback f ON f.exposure_id=e.id WHERE r.experiment_id=? AND (e.visible_at IS NOT NULL OR e.acted_at IS NOT NULL) GROUP BY e.id", (trial["id"],)).fetchall()
            unattributed = conn.execute("SELECT COUNT(*) FROM feedback WHERE created_at>=? AND recommendation_id IS NULL AND action IN ('not_interested','block_up')", (trial["started_at"],)).fetchone()[0]
        return [dict(r) for r in rows], unattributed

    def stop(self, reason="人工提前停止"):
        trial = registry.state().get("trial")
        if not trial or trial["status"] != "running":
            raise ValueError("没有运行中的试用")
        trial = {**trial, "status": "stopped", "ended_at": time.time(), "reason": reason}
        atomic_json(registry.root / "experiments" / trial["id"] / "trial.json", trial)
        registry.update(trial=None, last_trial=trial, auto_paused=True)
        return trial

    def check(self, clock=None):
        clock = time.time() if clock is None else clock
        state = registry.state()
        trial, pair = state.get("trial"), state.get("evaluation")
        if not trial or trial["status"] != "running":
            if state.get("anchor") and pair and pair["status"] == "passed" and not state.get("auto_paused"):
                return self.start(automatic=True)
            return None
        if pair and pair["status"] == "rejected":
            return self.stop("前向评价未通过")
        if clock < trial["next_check_at"]:
            return None
        trial["next_check_at"] = (int(clock//86400)+1)*86400
        rows, unattributed = self.rows(trial)
        counts = product_counts(rows)
        enough = all(g["views"] >= 20 and g["opportunities"] >= 500 and g["videos"] >= 100 for g in counts.values())
        if clock-trial["started_at"] < 7*86400 or not enough or not pair or pair["id"] != trial["evaluation_id"] or pair["status"] != "passed":
            registry.update(trial=trial)
            return {"status": "waiting", "counts": counts}
        report = product_report(rows)
        with registry._lock:
            latest = registry.state()
            if not latest.get("trial") or latest["trial"]["id"] != trial["id"] or latest["trial"]["status"] != "running" or not latest.get("evaluation") or latest["evaluation"]["id"] != pair["id"] or latest["evaluation"]["status"] != "passed":
                return {"status":"stopped","reason":"结算期间试用或评价状态改变"}
            try:
                for version in pair["versions"]:
                    registry.metadata(version)
            except Exception as error:
                return evaluation.reject_error(pair, error)
            report["unattributed_feedback"] = unattributed
            report["sealed_at"] = clock
            trial.update(status=report["status"], ended_at=clock)
            path = registry.root / "experiments" / trial["id"]
            atomic_json(path / "opportunities.json", rows)
            atomic_json(path / "report.json", report)
            atomic_json(path / "trial.json", trial)
            registry.update(trial=None, last_trial=trial, auto_paused=report["status"] != "passed")
            if report["status"] == "passed":
                registry.activate(trial["candidate"], anchor=not state.get("anchor") or state.get("approved_protocol", PROTOCOL) != PROTOCOL)
                registry.update(approved_protocol=PROTOCOL)
                with connect() as conn:
                    conn.execute("UPDATE model_runs SET status='approved' WHERE model_version=?", (trial["candidate"],))
                from backend.services.training_service import training
                training._load_active()
                evaluation.finish()
        return report


experiments = ExperimentService()
