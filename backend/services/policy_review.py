"""策略专用 Top12 盲评，独立于模型晋升与旧 Top10 评价。"""
import json
import random
import time
import copy
import hashlib
import logging
import math
import uuid
from pathlib import Path
from collections import Counter

from backend.services.model_registry import registry, atomic_json
from backend.services import recommendation_policy as policy
from backend.storage.database import connect, read_snapshot, get_state, set_state

PROTOCOL = "policy-blind-v2"
ACTIVE = ("preparing", "batch_ready", "rating", "failed")
TOPK, BATCHES, INTERVAL = 12, 5, 3600
EXECUTION = {"ranking": "rules-v033.1", "filters": "filters-v034.1", "candidates": "candidates-v032", "controls": "controls-v033.1"}


class ReviewError(ValueError):
    def __init__(self, message, status=409, code="review_conflict"):
        super().__init__(message)
        self.status, self.code = status, code


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False, default=lambda v: sorted(v) if isinstance(v, set) else str(v))


def fingerprint(value):
    return hashlib.sha256(encode(value).encode()).hexdigest()


def owner():
    from backend.services.bilibili_service import bili
    return str(bili._nav_cache["mid"]) if bili.has_cookie and bili._nav_cache and bili._nav_cache.get("mid") else None


def require_owner(value=None):
    value = value or owner()
    if not value:
        raise ReviewError("登录身份尚未准备好，请刷新登录状态", 401, "not_logged_in")
    return str(value)


def _request_id(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 128:
        raise ReviewError("需要有效请求标识", 400, "bad_request")
    return value


def _experiment(conn, experiment_id, account):
    row = conn.execute("SELECT * FROM policy_reviews WHERE id=? AND owner=?", (experiment_id, account)).fetchone()
    if not row:
        raise ReviewError("实验不存在", 404, "not_found")
    return dict(row)


def _affinity(value):
    return {**value, **{key: set(value[key]) for key in ("followings", "watched", "favorites")}}


def create(candidate, request_id, account=None):
    account = require_owner(account)
    _request_id(request_id)
    if not isinstance(candidate, dict) or set(candidate) != set(policy.DEFAULTS) or any(not isinstance(v, str) for v in candidate.values()) or candidate["interest"] not in policy.STRENGTH or candidate["exploration"] not in policy.EXPLORATION or candidate["archive"] not in policy.ARCHIVE:
        raise ReviewError("待评参数不合法", 400, "bad_request")
    with read_snapshot() as conn:
        existing = conn.execute("SELECT * FROM policy_reviews WHERE owner=? AND request_id=?", (account, request_id)).fetchone()
        if existing:
            if json.loads(existing["snapshot"])["candidate"] != candidate:
                raise ReviewError("请求标识不能用于不同配置")
            return detail(existing["id"], account)
        from backend.services.affinity import affinity
        frozen = policy.freeze(True)
        frozen["settings"] = {**frozen["settings"], **candidate}
        snapshot = {"protocol": PROTOCOL, "execution": {**EXECUTION, "ranking": policy.VERSION}, "model_version": registry.state().get("active") or "fallback-v03",
                    "policy": frozen, "candidate": candidate, "affinity": affinity.snapshot(), "seed": uuid.uuid4().hex,
                    "scoring": [-1, 0, 1, 2, 3], "topk": TOPK, "batches": BATCHES, "interval": INTERVAL, "novelty": .5}
        snapshot["model_artifact"] = _artifact(snapshot["model_version"])
        snapshot["signature"] = fingerprint(snapshot)
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        existing = conn.execute("SELECT id,snapshot FROM policy_reviews WHERE owner=? AND request_id=?", (account, request_id)).fetchone()
        if existing:
            if json.loads(existing["snapshot"])["candidate"] != candidate:
                raise ReviewError("请求标识不能用于不同配置")
            experiment_id = existing["id"]
        else:
            if conn.execute("SELECT 1 FROM policy_reviews WHERE owner=? AND status IN ('preparing','batch_ready','rating','failed')", (account,)).fetchone():
                raise ReviewError("请先完成或放弃当前盲评")
            if get_state("policy:trial", {}).get("status") == "running":
                raise ReviewError("请先结束当前策略试用")
            experiment_id = uuid.uuid4().hex
            conn.execute("INSERT INTO policy_reviews(id,owner,request_id,status,snapshot,created_at,updated_at) VALUES(?,?,?,'preparing',?,?,?)", (experiment_id, account, request_id, encode(snapshot), time.time(), time.time()))
    request_prepare(experiment_id, account)
    return detail(experiment_id, account)


def _artifact(version):
    path = registry.root / "versions" / version / "bundle.json"
    return fingerprint(json.loads(path.read_text(encoding="utf-8"))) if path.exists() else version


def _summary(conn, row):
    snapshot = json.loads(row["snapshot"])
    batches = [dict(v) for v in conn.execute("SELECT id,number,status,frozen_at,submitted_at,invalid_reason FROM policy_review_batches WHERE experiment_id=? ORDER BY frozen_at,id", (row["id"],))]
    for batch in batches:
        counts = conn.execute("SELECT COUNT(*),COUNT(score) FROM policy_review_ratings WHERE batch_id=?", (batch["id"],)).fetchone()
        batch.update(total=counts[0], rated=counts[1])
    return {"id": row["id"], "status": row["status"], "revision": row["revision"], "protocol": snapshot["protocol"],
            "created_at": row["created_at"], "updated_at": row["updated_at"], "abort_reason": row["abort_reason"],
            "config": {"candidate": snapshot["candidate"], "sources": snapshot["policy"]["sources"], "controls": snapshot["policy"]["controls"]["settings"], "model_version": snapshot["model_version"], "profile_version": snapshot["policy"]["profile"]["version"], "dictionary_version": snapshot["policy"]["controls"]["dictionary"]["version"]},
            "preparation": json.loads(row["preparation"]), "batches": batches, "completed_batches": sum(v["status"] == "sealed" for v in batches)}


def list_reviews(account=None):
    account = require_owner(account)
    with connect() as conn:
        rows = conn.execute("SELECT * FROM policy_reviews WHERE owner=? ORDER BY created_at DESC", (account,)).fetchall()
        result = [_summary(conn, row) for row in rows]
    legacy = []
    for path in sorted((registry.root / "policy-blind").glob("*/private.json")):
        if path.parent.name in {v["id"] for v in result}:
            continue
        # 旧文件没有账号归属；只暴露协议及目录摘要，不导入为当前账号准入。
        legacy.append({"id": path.parent.name, "protocol": "policy-blind-v1", "status": "legacy", "read_only": True})
    return {"current": next((v for v in result if v["status"] in ACTIVE), None), "experiments": result, "legacy": legacy}


def detail(experiment_id, account=None):
    account = require_owner(account)
    with connect() as conn:
        return _summary(conn, _experiment(conn, experiment_id, account))


def request_prepare(experiment_id, account=None):
    account = require_owner(account)
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = _experiment(conn, experiment_id, account)
        if row["status"] in ("preparing", "failed"):
            conn.execute("UPDATE policy_reviews SET status='preparing',preparation=?,revision=revision+1,updated_at=? WHERE id=?", (encode({"status": "checking", "checked_at": time.time(), "next_check_at": time.time()}), time.time(), experiment_id))
    from backend.services.source_scheduler import scheduler
    scheduler._wake.set()
    scheduler.start()
    return detail(experiment_id, account)


def abort(experiment_id, reason, account=None):
    account = require_owner(account)
    if not isinstance(reason, str) or not 1 <= len(reason.strip()) <= 500:
        raise ReviewError("请填写取消原因（1至500字）", 400, "bad_request")
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = _experiment(conn, experiment_id, account)
        if row["status"] == "completed":
            raise ReviewError("已完成实验不能取消")
        if row["status"] != "aborted":
            conn.execute("UPDATE policy_reviews SET status='aborted',abort_reason=?,revision=revision+1,updated_at=? WHERE id=?", (reason.strip(), time.time(), experiment_id))
    return detail(experiment_id, account)


def _blocked(conn, batch_id):
    from backend.services.filter_service import filters
    videos = [json.loads(v[0]) for v in conn.execute("SELECT video FROM policy_review_ratings WHERE batch_id=?", (batch_id,))]
    hidden = {v[0] for v in conn.execute("SELECT DISTINCT bvid FROM feedback WHERE revoked_at IS NULL AND action IN ('not_interested','block_up')")}
    kept, _ = filters.filter_candidates(videos, rules=filters.load_rules(conn=conn), penalties=[])
    return len(kept) != len(videos) or any(v["bvid"] in hidden for v in videos)


def _invalidate(conn, experiment_id, batch):
    if batch["status"] != "sealed" and _blocked(conn, batch["id"]):
        conn.execute("UPDATE policy_review_batches SET status='invalidated',invalid_reason='新增屏蔽命中本批，需整批重新评分' WHERE id=?", (batch["id"],))
        conn.execute("UPDATE policy_reviews SET status='preparing',revision=revision+1,preparation=?,updated_at=? WHERE id=?", (encode({"status": "checking", "reason": "新增屏蔽命中本批，旧评分草稿保留，正在重新准备", "next_check_at": time.time()}), time.time(), experiment_id))
        return True
    return False


def get_batch(experiment_id, batch_id, account=None):
    account = require_owner(account)
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        experiment = _experiment(conn, experiment_id, account)
        batch = conn.execute("SELECT * FROM policy_review_batches WHERE id=? AND experiment_id=?", (batch_id, experiment_id)).fetchone()
        if not batch:
            raise ReviewError("批次不存在", 404, "not_found")
        invalid = experiment["status"] in ACTIVE and _invalidate(conn, experiment_id, batch)
        if invalid or batch["status"] == "invalidated":
            return {"id": batch_id, "status": "invalidated", "number": batch["number"], "items": []}
        # 白名单构建；私有排序、来源及模型证据绝不合并到响应。
        items = [{"blind_item_id": row["id"], "video": json.loads(row["video"]), "score": row["score"], "revision": row["revision"]} for row in conn.execute("SELECT * FROM policy_review_ratings WHERE batch_id=? ORDER BY ordinal", (batch_id,))]
        for item in items:
            item["video"].pop("tag", None)
            item["video"].pop("tname", None)
            item["video"].pop("mid", None)
        return {"id": batch_id, "status": "archived" if experiment["status"] == "aborted" and batch["status"] != "sealed" else batch["status"], "number": batch["number"], "items": items}


def rate(experiment_id, batch_id, item_id, score, revision, request_id, account=None):
    account = require_owner(account)
    _request_id(request_id)
    if type(score) is not int or score not in (-1, 0, 1, 2, 3) or type(revision) is not int or revision < 0:
        raise ReviewError("评分或版本不合法", 400, "bad_request")
    invalid = False
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        experiment = _experiment(conn, experiment_id, account)
        batch = conn.execute("SELECT * FROM policy_review_batches WHERE id=? AND experiment_id=?", (batch_id, experiment_id)).fetchone()
        if not batch:
            raise ReviewError("批次不存在", 404, "not_found")
        if experiment["status"] not in ("batch_ready", "rating") or batch["status"] not in ("ready", "rating"):
            raise ReviewError("本批已经锁定或作废")
        invalid = _invalidate(conn, experiment_id, batch)
        if not invalid:
            row = conn.execute("SELECT * FROM policy_review_ratings WHERE id=? AND batch_id=?", (item_id, batch_id)).fetchone()
            if not row:
                raise ReviewError("条目不存在", 404, "not_found")
            if row["request_id"] == request_id:
                if row["score"] != score:
                    raise ReviewError("请求标识不能用于不同评分")
                return {"blind_item_id": item_id, "score": row["score"], "revision": row["revision"]}
            if row["revision"] != revision:
                raise ReviewError("评分已在其他页面修改，请重新读取")
            conn.execute("UPDATE policy_review_ratings SET score=?,revision=revision+1,request_id=?,updated_at=? WHERE id=?", (score, request_id, time.time(), item_id))
            conn.execute("UPDATE policy_review_batches SET status='rating' WHERE id=?", (batch_id,))
            conn.execute("UPDATE policy_reviews SET status='rating',updated_at=? WHERE id=?", (time.time(), experiment_id))
    if invalid:
        raise ReviewError("新增屏蔽使本批作废，请返回重新准备")
    return {"blind_item_id": item_id, "score": score, "revision": revision+1}


def submit(experiment_id, batch_id, account=None):
    account = require_owner(account)
    invalid = False
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        experiment = _experiment(conn, experiment_id, account)
        batch = conn.execute("SELECT * FROM policy_review_batches WHERE id=? AND experiment_id=?", (batch_id, experiment_id)).fetchone()
        if not batch:
            raise ReviewError("批次不存在", 404, "not_found")
        if batch["status"] == "sealed":
            return _summary(conn, experiment)
        if experiment["status"] not in ("batch_ready", "rating") or batch["status"] not in ("ready", "rating"):
            raise ReviewError("本批已经作废或实验已结束")
        invalid = _invalidate(conn, experiment_id, batch)
        if not invalid:
            ratings = conn.execute("SELECT * FROM policy_review_ratings WHERE batch_id=?", (batch_id,)).fetchall()
            if not ratings or any(v["score"] is None for v in ratings):
                raise ReviewError("请完成本批所有视频评分并等待保存成功")
            conn.execute("UPDATE policy_review_batches SET status='sealed',submitted_at=? WHERE id=?", (time.time(), batch_id))
            count = conn.execute("SELECT COUNT(*) FROM policy_review_batches WHERE experiment_id=? AND status='sealed'", (experiment_id,)).fetchone()[0]
            report_value = _calculate(conn, experiment_id) if count == BATCHES else None
            conn.execute("UPDATE policy_reviews SET status=?,report=?,revision=revision+1,preparation=?,updated_at=? WHERE id=?", ("completed" if report_value else "preparing", encode(report_value) if report_value else None, encode({"status": "checking", "next_check_at": time.time()}), time.time(), experiment_id))
    if invalid:
        raise ReviewError("新增屏蔽使本批作废，请返回重新准备")
    from backend.services.source_scheduler import scheduler
    scheduler._wake.set()
    return detail(experiment_id, account)


def _metrics(batches, name):
    values = [batch["scores"][bv] for batch in batches for bv in batch["positions"][name]]
    result = {"average": sum(values)/len(values) if values else None}
    for size in (12, 4):
        selected = [batch["scores"][bv] for batch in batches for bv in batch["positions"][name][:size]]
        result[f"top{size}"] = {"samples": len(selected), "wanted": sum(v >= 2 for v in selected)/len(selected) if selected else None, "repelled": sum(v == -1 for v in selected)/len(selected) if selected else None}
    result["long_coverage"] = sum(e["L"] > 0 for batch in batches for e in batch["evidence"][name])/len(values) if values else None
    return result


def _calculate(conn, experiment_id):
    batches = []
    for row in conn.execute("SELECT * FROM policy_review_batches WHERE experiment_id=? AND status='sealed' ORDER BY number", (experiment_id,)):
        private = json.loads(row["private"])
        private["scores"] = {v["bvid"]: v["score"] for v in conn.execute("SELECT bvid,score FROM policy_review_ratings WHERE batch_id=?", (row["id"],))}
        if any(len(private["positions"][name]) != TOPK or len(set(private["positions"][name])) != TOPK or len(private["evidence"][name]) != TOPK for name in ("baseline", "strategy")) or set(private["scores"]) != {bv for ids in private["positions"].values() for bv in ids} or any(v is None for v in private["scores"].values()):
            raise RuntimeError("已封存盲评批次数据不完整")
        batches.append(private)
    groups = {name: _metrics(batches, name) for name in ("baseline", "strategy")}
    guards = guard_report(groups)
    return {"protocol": PROTOCOL, "complete": len(batches) == BATCHES, "passed": len(batches) == BATCHES and all(v["passed"] for v in guards), "groups": groups, "guards": guards,
            "batches": [{"number": i+1, "groups": {name: _metrics([batch], name) for name in groups}, "change": batch["change"]} for i, batch in enumerate(batches)],
            "rated_occurrences": sum(len(b["scores"]) for b in batches), "unique_bvids": len({bv for b in batches for bv in b["scores"]}), "limitations": "五批分时盲评为小样本描述性准入护栏；位置及重复评分不等于独立样本，不证明新策略更好。零事件不证明风险为零。"}


def guard_report(groups):
    base, new = groups["baseline"], groups["strategy"]
    guards = []
    for metric, label, threshold, relation in (("top12.wanted", "Top12想看率", -.05, "下降不超过5个百分点"), ("top4.wanted", "Top4想看率", 0, "不得下降"), ("top12.repelled", "Top12强烈排斥率", 0, "不得增加"), ("top4.repelled", "Top4强烈排斥率", 0, "不得增加"), ("long_coverage", "长期兴趣覆盖", 0, "必须提高")):
        def value(group):
            return group[metric] if "." not in metric else group[metric.split(".")[0]][metric.split(".")[1]]
        difference = value(new)-value(base)
        passed = difference <= 0 if metric.endswith("repelled") else difference > 0 if metric == "long_coverage" else difference >= threshold-1e-12
        guards.append({"metric": metric, "label": label, "baseline": value(base), "strategy": value(new), "difference": difference, "requirement": relation, "passed": passed})
    return guards


def report(experiment_id=None, account=None):
    if experiment_id is None:
        if owner():
            active = list_reviews()["experiments"]
            if active:
                experiment_id = active[0]["id"]
        if experiment_id is None:
            return _legacy_report()
    account = require_owner(account)
    with connect() as conn:
        row = _experiment(conn, experiment_id, account)
        return json.loads(row["report"]) if row["report"] else {"complete": False, "passed": False, "status": "证据不足，五批提交前不揭盲", "progress": _summary(conn, row)["completed_batches"]}


def _context(snapshot):
    from backend.services.affinity import affinity
    from backend.services.cache_service import pool
    from backend.services.filter_service import filters
    from backend.services import interest_profile
    from backend.services.recommendation_service import recommendation
    from backend.services.recommendation_controls import window
    clock = time.time()
    with read_snapshot() as conn:
        recent = affinity.snapshot(clock)
        frozen_affinity = _affinity(snapshot["affinity"])
        frozen_affinity.update(watched=recent["watched"], favorites=recent["favorites"])
        replay = interest_profile.replay_eligible(frozen_affinity, clock)
        hard = {v[0] for v in conn.execute("SELECT DISTINCT bvid FROM feedback WHERE revoked_at IS NULL AND action IN ('not_interested','block_up')")}
        context = {"clock": clock, "rules": filters.load_rules(conn=conn), "penalties": affinity.penalties(clock), "replay": replay,
                   "hidden": (recommendation._hidden_bvids()-set(replay)) | hard, "pages": window(conn, clock), "cooled": recommendation._cooled_bvids(clock), "affinity": frozen_affinity}
        # 固定来源快照；不用动态 _mixed_sources 判断已变动的设置或实体。
        sources = ["follow", "related", "up_archive"]+[s for s in ("rcmd", "hot", "vertical_search") if snapshot["policy"]["sources"].get(s) != "off"]
        if snapshot["policy"]["controls"]["settings"]["third_party"] != "off" and "vertical_search" not in sources:
            sources.append("vertical_search")
        return pool.all(sources, clock), context


def prepare(experiment_id, account=None):
    """后台本地计算；预期等待返回结构化状态，程序故障保留日志。"""
    account = require_owner(account)
    with connect() as conn:
        row = _experiment(conn, experiment_id, account)
        batches = [dict(v) for v in conn.execute("SELECT * FROM policy_review_batches WHERE experiment_id=? AND status='sealed' ORDER BY number", (experiment_id,))]
    if row["status"] not in ("preparing", "failed"):
        return detail(experiment_id, account)
    snapshot = json.loads(row["snapshot"])
    from backend.services.bilibili_service import bili
    state = {"status": "checking", "checked_at": time.time(), "next_check_at": time.time()+60}
    private = None
    try:
        if snapshot["execution"] != {**EXECUTION, "ranking": policy.VERSION}:
            state.update(status="paused", reason="执行协议已改变，请放弃并重新开始")
        elif batches and time.time() < batches[-1]["frozen_at"]+INTERVAL:
            state.update(status="waiting_change", reason="距离上一批冻结尚未满一小时", next_check_at=batches[-1]["frozen_at"]+INTERVAL)
        else:
            from backend.services.recommendation_service import recommendation
            from backend.services.source_mixer import mix
            if _artifact(snapshot["model_version"]) != snapshot["model_artifact"]:
                raise RuntimeError("冻结模型工件发生变化")
            videos, context = _context(snapshot)
            bundle = registry.load(snapshot["model_version"])
            positions, evidence, chosen = {}, {}, {}
            frozen = copy.deepcopy(snapshot["policy"])
            frozen["frequency"] = dict(Counter(key[4:] for page in context["pages"] for key in page if key.startswith("mid:")))
            for name, enabled in (("baseline", False), ("strategy", True)):
                current = {**copy.deepcopy(frozen), "enabled": enabled}
                ranked = recommendation.rank_sources(copy.deepcopy(videos), "for_you", "all", set(), bundle, snapshot=context["affinity"], record_hits=False, policy=current, cooled=context["cooled"], context=context)
                if name == "baseline":
                    qualified = {v["bvid"] for values in ranked.values() for v in values}
                    state.update(qualified=len(qualified), sources={source: len({v["bvid"] for v in values}) for source, values in ranked.items()})
                items = mix(ranked, TOPK, {**frozen["sources"], "_policy": current, "_exposure_pages": context["pages"]})
                positions[name] = [v["bvid"] for v in items]
                evidence[name] = [policy.annotate(v, current, context["affinity"])["_policy"] for v in items]
                chosen.update({v["bvid"]: v["_snapshot"] for v in items})
            state["groups"] = {name: len(ids) for name, ids in positions.items()}
            union = set(chosen)
            previous = {bv for b in batches for ids in json.loads(b["private"])["positions"].values() for bv in ids}
            latest = {bv for ids in json.loads(batches[-1]["private"])["positions"].values() for bv in ids} if batches else set()
            change = {"new_bvids": len(union-previous), "required": math.ceil(len(union)*.5), "union": len(union), "jaccard": len(union & latest)/len(union | latest) if union | latest else None}
            if any(len(ids) != TOPK for ids in positions.values()):
                state.update(status="waiting_candidates", reason="需要两组各组成完整Top12；后台准备候选", next_check_at=max(time.time()+60, bili.rate_limited_until))
            elif batches and change["new_bvids"] < change["required"]:
                state.update(status="waiting_change", reason=f"本批新增{change['new_bvids']}条，需至少{change['required']}条本轮未出现视频")
            else:
                private = {"positions": positions, "evidence": evidence, "change": change, "context": context, "candidates": videos, "chosen": chosen}
                state.update(status="ready", reason="新批次已准备好")
            state["change"] = change
    except Exception:
        logging.exception("策略盲评后台准备失败 experiment=%s", experiment_id)
        state.update(status="paused", reason="后台准备出现内部错误，可刷新重试；详情已记录日志")
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        current = _experiment(conn, experiment_id, account)
        if current["revision"] != row["revision"] or current["status"] not in ("preparing", "failed"):
            return _summary(conn, current)
        if private is not None:
            from backend.services.filter_service import filters
            kept, _ = filters.filter_candidates(list(private["chosen"].values()), rules=filters.load_rules(conn=conn), penalties=[])
            hard = {v[0] for v in conn.execute("SELECT DISTINCT bvid FROM feedback WHERE revoked_at IS NULL AND action IN ('not_interested','block_up')")}
            if len(kept) != len(private["chosen"]) or hard & set(private["chosen"]):
                state.update(status="checking", reason="冻结提交前屏蔽发生变化，下轮重检")
                private = None
        if private is not None:
            batch_id = uuid.uuid4().hex
            conn.execute("INSERT INTO policy_review_batches(id,experiment_id,number,status,frozen_at,private) VALUES(?,?,?,'ready',?,?)", (batch_id, experiment_id, len(batches)+1, private["context"]["clock"], encode(private)))
            ids = list(private["chosen"])
            random.Random(snapshot["seed"]+batch_id).shuffle(ids)
            for index, bv in enumerate(ids):
                video = private["chosen"][bv]
                public = {key: video.get(key) for key in ("bvid", "title", "author", "mid", "pic", "duration", "pubdate", "tag", "tname")}
                public["url"] = f"https://www.bilibili.com/video/{bv}"
                conn.execute("INSERT INTO policy_review_ratings(id,batch_id,bvid,ordinal,video) VALUES(?,?,?,?,?)", (uuid.uuid4().hex, batch_id, bv, index, encode(public)))
        conn.execute("UPDATE policy_reviews SET status=?,preparation=?,revision=revision+1,updated_at=? WHERE id=?", ("batch_ready" if private else "failed" if state.get("reason", "").startswith("后台准备出现内部错误") else "preparing", encode(state), time.time(), experiment_id))
    return detail(experiment_id, account)


def background():
    account = owner()
    if not account:
        return
    with connect() as conn:
        rows = conn.execute("SELECT * FROM policy_reviews WHERE owner=? AND status IN ('preparing','batch_ready','rating')", (account,)).fetchall()
    for row in rows:
        if row["status"] in ("batch_ready", "rating"):
            with connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                batch = conn.execute("SELECT * FROM policy_review_batches WHERE experiment_id=? AND status IN ('ready','rating')", (row["id"],)).fetchone()
                if batch:
                    _invalidate(conn, row["id"], batch)
        elif json.loads(row["preparation"]).get("next_check_at", 0) <= time.time():
            prepare(row["id"], account)


def pending():
    account = owner()
    if not account:
        return False
    with connect() as conn:
        return conn.execute("SELECT 1 FROM policy_reviews WHERE owner=? AND status='preparing'", (account,)).fetchone() is not None


def pause(reason):
    """退出或登录失效只暂停采集；保留当前评分与账号归属。"""
    account = owner()
    if not account:
        return
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        for row in conn.execute("SELECT id,preparation FROM policy_reviews WHERE owner=? AND status IN ('preparing','batch_ready','rating','failed')", (account,)).fetchall():
            state = {**json.loads(row["preparation"]), "status": "paused", "reason": reason, "checked_at": time.time(), "next_check_at": time.time()+300}
            conn.execute("UPDATE policy_reviews SET preparation=?,revision=revision+1,updated_at=? WHERE id=?", (encode(state), time.time(), row["id"]))


def export(experiment_id, account=None):
    account = require_owner(account)
    path = registry.root / "policy-blind" / experiment_id
    with connect() as conn:
        row = _experiment(conn, experiment_id, account)
        batches = [dict(v) for v in conn.execute("SELECT * FROM policy_review_batches WHERE experiment_id=? ORDER BY frozen_at", (experiment_id,))]
        ratings = [dict(v) for v in conn.execute("SELECT r.* FROM policy_review_ratings r JOIN policy_review_batches b ON b.id=r.batch_id WHERE b.experiment_id=? ORDER BY b.number,r.ordinal", (experiment_id,))]
    atomic_json(path / "private.json", {"protocol": PROTOCOL, "experiment": row, "batches": batches})
    atomic_json(path / "rating.json", ratings)
    atomic_json(path / "report.json", report(experiment_id, account))
    return str(path / "rating.json")


def import_ratings(experiment_id, path, account=None):
    account = require_owner(account)
    ratings = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(ratings, list):
        raise ReviewError("评分导入格式不合法", 400)
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        experiment = _experiment(conn, experiment_id, account)
        if experiment["status"] not in ("batch_ready", "rating"):
            raise ReviewError("只能导入当前未封存批次")
        for item in ratings:
            if not isinstance(item, dict):
                raise ReviewError("评分条目格式不合法", 400)
            row = conn.execute("SELECT r.*,b.experiment_id,b.status FROM policy_review_ratings r JOIN policy_review_batches b ON b.id=r.batch_id WHERE r.id=?", (item.get("id"),)).fetchone()
            if row and row["experiment_id"] == experiment_id and row["score"] == item.get("score") and row["revision"] == item.get("revision"):
                continue
            if not row or row["experiment_id"] != experiment_id or row["status"] not in ("ready", "rating") or type(item.get("score")) is not int or item["score"] not in (-1, 0, 1, 2, 3) or row["revision"] != item.get("revision"):
                raise ReviewError("导入包含锁定条目、非法评分或版本冲突")
            conn.execute("UPDATE policy_review_ratings SET score=?,revision=revision+1,request_id=NULL,updated_at=? WHERE id=?", (item["score"], time.time(), item["id"]))
    return detail(experiment_id, account)


def capture(experiment_id=None, account=None):
    account = require_owner(account)
    if experiment_id is None:
        current = list_reviews(account)["current"]
        experiment_id = current["id"] if current else create({k: policy.settings()[k] for k in policy.DEFAULTS}, uuid.uuid4().hex, account)["id"]
    result = prepare(experiment_id, account)
    return {**result, "rating_path": export(experiment_id, account)}


def guard_change(conn=None):
    """试用参数不隐式分段；设置入口与开发工具共同执行。"""
    if conn is None:
        with connect() as connection:
            return guard_change(connection)
    row = conn.execute("SELECT value FROM app_state WHERE key='policy:trial'").fetchone()
    if row and json.loads(row[0]).get("status") == "running":
        if json.loads(row[0]).get("owner") and json.loads(row[0])["owner"] != owner():
            raise ReviewError("另一个账号的策略试用仍在进行，请返回原账号结束试用")
        raise ReviewError("试用配置已锁定；请取消修改继续试用，或先恢复旧策略再修改", 409, "trial_locked")


def _state(conn, key, default):
    row = conn.execute("SELECT value FROM app_state WHERE key=?", (key,)).fetchone()
    return json.loads(row[0]) if row else default


def _write_state(conn, key, value):
    conn.execute("INSERT INTO app_state VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at", (key, encode(value), time.time()))


def start_trial(experiment_id, request_id, confirm_restore=False, account=None):
    from backend.services import entity_aliases
    from backend.services.source_mixer import settings as sources_settings
    from backend.services.recommendation_controls import invalidate
    account = require_owner(account)
    _request_id(request_id)
    with registry._lock, connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = _experiment(conn, experiment_id, account)
        snapshot = json.loads(row["snapshot"])
        prior = _state(conn, "policy:trial", {})
        receipt = _state(conn, "policy:trial:request:"+request_id, None)
        if receipt:
            if receipt["experiment_id"] != experiment_id or receipt["owner"] != account:
                raise ReviewError("请求标识冲突")
            return receipt
        if row["status"] != "completed" or not row["report"] or not json.loads(row["report"]).get("passed"):
            raise ReviewError("需要先完成并通过五批策略盲评")
        if snapshot["execution"] != {**EXECUTION, "ranking": policy.VERSION} or snapshot["model_version"] != (registry.state().get("active") or "fallback-v03") or _artifact(snapshot["model_version"]) != snapshot["model_artifact"]:
            raise ReviewError("模型或执行协议已改变，请重新盲评")
        if _state(conn, "entities:dictionary", {"version": 0, "entities": []}) != snapshot["policy"]["controls"]["dictionary"]:
            raise ReviewError("实体词典已改变，旧报告保留；请按新词典重新盲评")
        if registry.state().get("trial") or prior.get("status") == "running":
            raise ReviewError("已有模型或策略试用，请先结束")
        current_settings, current_sources = policy.settings(), sources_settings()
        target_sources = {**snapshot["policy"]["sources"], "classic": False}
        target_settings = snapshot["policy"]["settings"]
        if (current_settings != target_settings or current_sources != target_sources) and confirm_restore is not True:
            raise ReviewError("配置发生变化，请确认恢复实验来源与控制参数后开始试用", 409, "restore_confirmation")
        _write_state(conn, "policy:settings", target_settings)
        _write_state(conn, "sources:settings", target_sources)
        trial = {"id": uuid.uuid4().hex, "experiment_id": experiment_id, "owner": account, "status": "running", "started_at": time.time(), "request_id": request_id,
                 "signature": policy.signature(target_settings, target_sources), "model_version": snapshot["model_version"], "protocol": PROTOCOL,
                 "settings": target_settings, "sources": target_sources, "dictionary": snapshot["policy"]["controls"]["dictionary"],
                 "execution": snapshot["execution"], "rollback": {"settings": current_settings, "sources": current_sources, "trial": prior},
                 "request_start": _state(conn, "requests:candidate_totals", {"attempts": 0, "search": 0})}
        if prior:
            _write_state(conn, "policy:trial:history:"+str(prior.get("id", prior.get("started_at", time.time()))), prior)
        _write_state(conn, "policy:trial", trial)
        _write_state(conn, "policy:trial:request:"+request_id, trial)
        invalidate(conn)
    return policy.trial_status()


def finish_trial(action):
    from backend.services.recommendation_controls import invalidate
    account = require_owner()
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        trial = _state(conn, "policy:trial", {})
        if trial.get("owner") != account:
            raise ReviewError("当前账号没有这次试用")
        if action == "keep":
            from backend.services.source_mixer import settings as sources_settings
            if trial.get("status") != "running" or time.time()-trial["started_at"] < 7*86400 or trial.get("model_version") != (registry.state().get("active") or "fallback-v03") or trial["signature"] != policy.signature(policy.settings()) or trial["dictionary"] != _state(conn, "entities:dictionary", {"version": 0, "entities": []}) or trial["settings"] != policy.settings() or trial["sources"] != sources_settings() or trial["execution"] != {**EXECUTION, "ranking": policy.VERSION}:
                raise ReviewError("须试用至少七个完整24小时，且配置和模型保持一致")
            trial.update(status="kept", decided_at=time.time())
            _write_state(conn, "policy:trial", trial)
        elif action == "stop":
            if trial.get("status") not in ("running", "kept"):
                return policy.trial_status()
            saved = trial["rollback"]
            trial.update(status="stopped", ended_at=time.time())
            _write_state(conn, "policy:trial:history:"+trial["id"], trial)
            _write_state(conn, "policy:settings", saved["settings"])
            _write_state(conn, "sources:settings", saved["sources"])
            _write_state(conn, "policy:trial", saved["trial"])
        else:
            raise ReviewError("非法试用操作", 400)
        invalidate(conn)
    return policy.trial_status()


def location():
    version = registry.state().get("active") or "fallback-v03"
    return registry.root / "policy-blind" / (policy.signature(policy.settings())+"-"+version)


def _legacy_report():
    path = location()
    if not (path / "private.json").exists():
        return {"passed": False, "status": "需要捕获五批策略盲评"}
    document = json.loads((path / "private.json").read_text(encoding="utf-8"))
    ratings = json.loads((path / "rating.json").read_text(encoding="utf-8"))
    expected = {bv for batch in document["batches"] for ids in batch["positions"].values() for bv in ids}
    complete = len(document["batches"]) == 5 and len({b["at"] for b in document["batches"]}) == 5 and len(ratings) == len(expected) and {r["bvid"] for r in ratings} == expected and all(type(r.get("score")) is int and r["score"] in (-1, 0, 1, 2, 3) for r in ratings)
    complete = complete and all(len(batch["positions"][name]) == 12 and len(set(batch["positions"][name])) == 12 and len(batch["evidence"][name]) == 12 for batch in document["batches"] for name in ("baseline", "strategy"))
    if not complete:
        return {"passed": False, "status": "需要五批完整 Top12 和所有 -1/0/1/2/3 人工评分", "batches": len(document["batches"])}
    scores = {v["bvid"]: v["score"] for v in ratings}
    result = {"batches": 5, "complete": True, "signature": document["signature"], "controls": document.get("controls"), "model_version": document["model_version"], "limitations": "小样本描述性护栏，不构成统计证明", "groups": {}}
    for name in ("baseline", "strategy"):
        group = {}
        for size in (12, 4):
            values = [scores[bv] for batch in document["batches"] for bv in batch["positions"][name][:size]]
            group[f"top{size}"] = {"samples": len(values), "wanted": sum(v >= 2 for v in values)/len(values), "repelled": sum(v == -1 for v in values)/len(values)}
        group["long_coverage"] = sum(e["L"] > 0 for batch in document["batches"] for e in batch["evidence"][name])/60
        result["groups"][name] = group
    result["passed"] = all(v["passed"] for v in guard_report(result["groups"]))
    return result
