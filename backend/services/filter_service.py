"""统一过滤规则层（v0.2.1）。

职责边界：
- 只负责「读规则 / 匹配 / 记命中 / 使缓存失效」，不含任何推荐排序逻辑。
- SQL 与匹配逻辑集中在这里，不散落到 route handler、FeedPage.tsx 或 recommender。
- React 不参与实际过滤判断。

规则模型：
- blocked_ups          → 按 mid / name 精确屏蔽某一个已知 UP（保留原有行为）
- filter_rules(uploader) → UP 名称包含关键词即屏蔽，不一定对应唯一 mid
- filter_rules(title)    → 视频标题（+ 标签）包含关键词即屏蔽
- filter_rules(tag/zone) → 标签 / 分区匹配；降权仅打标记

匹配字段：
- 标题：video["title"]（并附带 tags 一起匹配，与原 v0.2 行为一致）
- UP：优先 video["author"]；service 层负责先把 owner.name 标准化为 author

大小写：写入时 keyword_norm = keyword.strip().casefold()，匹配时对文本同样 casefold。
"""

import json
import os
import sqlite3
import threading
import time

from backend.config import DB_PATH
from backend.storage.database import get_state, now, set_state
from backend.services import feed_performance as perf

TARGET_TITLE = "title"
TARGET_UPLOADER = "uploader"
TARGET_TAG = "tag"
TARGET_ZONE = "zone"
TARGET_TYPES = (TARGET_TITLE, TARGET_UPLOADER, TARGET_TAG, TARGET_ZONE)

MATCH_CONTAINS = "contains"
MATCH_EXACT = "exact"
MATCH_REGEX = "regex"
MATCH_MODES = (MATCH_CONTAINS, MATCH_EXACT, MATCH_REGEX)

ACTION_HARD_BLOCK = "hard_block"
ACTION_DOWNRANK = "downrank"
ACTIONS = (ACTION_HARD_BLOCK, ACTION_DOWNRANK)

VERSION_KEY = "filters:version"
MAX_KEYWORD_LEN = 60


class FilterError(Exception):
    pass


def normalize(keyword):
    """归一化：trim + casefold。空字符串返回空串。"""
    return (keyword or "").strip().casefold()


def _connect():
    conn = perf.sql_connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def get_filter_version():
    return int(get_state(VERSION_KEY, 0) or 0)


def _bump_filter_version(conn):
    """规则集发生变化：版本 +1，读取旧 stream 时重新应用当前过滤。

    保留 feed_cache 的不可变模型归属，不动 candidates / served_videos /
    recommendation_history / feedback / blocked_ups。
    """
    row = conn.execute("SELECT value FROM app_state WHERE key = ?", (VERSION_KEY,)).fetchone()
    version = int(json.loads(row["value"])) + 1 if row else 1
    conn.execute(
        "INSERT INTO app_state(key, value, updated_at) VALUES(?, ?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
        (VERSION_KEY, json.dumps(version), time.time()),
    )
    # 保留已有 stream 的模型归属；每次读取缓存页仍应用最新硬过滤。
    return version


class FilterService:
    """规则加载 / 匹配 / 命中统计 / 缓存失效。"""

    def __init__(self):
        # 规则按版本缓存：版本没变则复用内存规则，变了重新从 SQLite 加载
        self._cache_version = None
        self._cache = None
        self._lock = threading.Lock()

    # ---------------- 加载 ----------------

    def load_rules(self, force=False, conn=None):
        version = get_filter_version()
        with self._lock:
            if conn is None and not force and self._cache is not None and self._cache_version == version:
                return self._cache
            owned = conn is None
            conn = conn or _connect()
            try:
                rows = [
                    dict(r)
                    for r in conn.execute(
                        "SELECT * FROM filter_rules WHERE enabled = 1 ORDER BY id",
                    ).fetchall()
                ]
                ups = [
                    dict(r)
                    for r in conn.execute("SELECT id, mid, name FROM blocked_ups").fetchall()
                ]
            finally:
                if owned:
                    conn.close()

            title_rules = []
            uploader_rules = []
            tag_rules = []
            zone_rules = []
            for r in rows:
                norm = r["keyword_norm"] or normalize(r["keyword"])
                if not norm:
                    continue
                entry = {
                    "id": r["id"],
                    "keyword": r["keyword"],
                    "keyword_norm": norm,
                    "match_mode": r["match_mode"],
                    "target_type": r["target_type"],
                    "action": r["action"],
                }
                if r["target_type"] == TARGET_TITLE:
                    title_rules.append(entry)
                elif r["target_type"] == TARGET_UPLOADER:
                    uploader_rules.append(entry)
                elif r["target_type"] == TARGET_TAG:
                    tag_rules.append(entry)
                elif r["target_type"] == TARGET_ZONE:
                    zone_rules.append(entry)

            value = {
                "version": version,
                "title": title_rules,
                "uploader": uploader_rules,
                "tag": tag_rules,
                "zone": zone_rules,
                "blocked_mids": {u["mid"] for u in ups if u["mid"] is not None},
                "blocked_names": {u["name"] for u in ups if u["name"] and u["mid"] is None},
            }
            if owned:
                self._cache, self._cache_version = value, version
            return value

    def invalidate(self):
        with self._lock:
            self._cache = None
            self._cache_version = None

    # ---------------- 匹配 ----------------

    @staticmethod
    def _match_one(match_mode, keyword_norm, haystack_norm):
        if match_mode == MATCH_EXACT:
            return keyword_norm == haystack_norm
        if match_mode == MATCH_REGEX:
            # 无状态只读匹配；非法正则已在写入时拒绝
            import re

            return re.search(keyword_norm, haystack_norm) is not None
        return keyword_norm in haystack_norm

    def match_title(self, title, tags=None, rules=None):
        """返回命中的 title 规则列表。"""
        rules = rules or self.load_rules()
        haystack = ((title or "") + " " + " ".join(tags or [])).casefold()
        return [
            r for r in rules["title"] if self._match_one(r["match_mode"], r["keyword_norm"], haystack)
        ]

    def match_uploader(self, author, rules=None):
        """返回命中的 uploader 规则列表。"""
        rules = rules or self.load_rules()
        haystack = (author or "").casefold()
        return [
            r for r in rules["uploader"] if self._match_one(r["match_mode"], r["keyword_norm"], haystack)
        ]

    def match_exact_up(self, video, rules=None):
        """精确屏蔽：mid 或完整 name 命中 blocked_ups。返回 True/False。"""
        rules = rules or self.load_rules()
        mid = video.get("mid")
        author = video.get("author")
        if mid is not None and mid in rules["blocked_mids"]:
            return True
        return bool(author) and author in rules["blocked_names"]

    @perf.measured("filter_ms")
    def filter_candidates(self, candidates, record=False, rules=None, penalties=None):
        """按任务书顺序过滤：精确 MID → UP 关键词 → 标题关键词 → 标签 → 分区。

        返回 (保留的视频列表, 统计)。record=True 时把命中写入 hit_count（同一视频
        命中同一规则只记 1 次；命中多条规则各自 +1）。
        """
        rules = rules if rules is not None else self.load_rules()
        from backend.services.affinity import affinity, up_key
        penalties = affinity.penalties() if penalties is None else penalties
        kept = []
        stats = {"input": len(candidates), "blocked_mid": 0, "blocked_uploader": 0, "blocked_title": 0, "blocked_tag": 0, "blocked_zone": 0}
        pending = {}

        for video in candidates:
            video = dict(video)
            video.pop("downrank", None)
            video.pop("expand_disabled", None)
            bvid = video.get("bvid") or ""
            # 1) 精确屏蔽 UP（mid / name）
            if self.match_exact_up(video, rules):
                stats["blocked_mid"] += 1
                continue
            tags = video.get("tag") or video.get("tags") or []
            for target, hits in (
                ("uploader", self.match_uploader(video.get("author"), rules)),
                ("title", self.match_title(video.get("title"), tags, rules)),
                ("tag", [r for r in rules["tag"] if any(self._match_one(r["match_mode"], r["keyword_norm"], normalize(tag)) for tag in tags)]),
                ("zone", [r for r in rules["zone"] if self._match_one(r["match_mode"], r["keyword_norm"], normalize(video.get("tname")))]),
            ):
                if record:
                    for r in hits:
                        pending[(r["id"], bvid)] = r["id"]
                if any(r["action"] == ACTION_HARD_BLOCK for r in hits):
                    stats["blocked_" + target] += 1
                    break
                if hits:
                    video["downrank"] = True
            else:
                for penalty in penalties:
                    if (penalty["kind"] in ("up", "expand_up") and penalty["key"] == up_key(video)) or (penalty["kind"] == "tag" and penalty["key"] in [normalize(tag) for tag in tags]):
                        video["expand_disabled" if penalty["kind"] == "expand_up" else "downrank"] = True
                kept.append(video)

        if record and pending:
            self.record_hits(pending)
        return kept, stats

    # ---------------- 命中统计 ----------------

    def record_hits(self, pending):
        """pending: {(rule_id, bvid): rule_id}。

        字典键天然保证「同一视频命中同一条规则只记 1 次」；同一视频命中多条规则
        会产生多个 key，各自 +1。单次 UPDATE 批量落库。
        """
        if not pending:
            return 0
        counts = {}
        for rule_id in pending.values():
            counts[rule_id] = counts.get(rule_id, 0) + 1
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        conn = _connect()
        try:
            conn.execute("BEGIN")
            for rule_id, hits in counts.items():
                conn.execute(
                    "UPDATE filter_rules SET hit_count = hit_count + ?, last_hit_at = ?, "
                    "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (hits, stamp, rule_id),
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        return sum(counts.values())

    def invalidate_cache(self):
        """规则变更后由写入方调用：版本 +1 + 清 feed_cache + 丢弃内存规则缓存。"""
        conn = _connect()
        try:
            conn.execute("BEGIN")
            version = _bump_filter_version(conn)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        self.invalidate()
        return version

    # ---------------- 规则 CRUD ----------------

    def list_rules(self, target_type=None, enabled=None, q=None):
        sql = "SELECT * FROM filter_rules WHERE 1 = 1"
        params = []
        if target_type:
            if target_type not in TARGET_TYPES:
                raise FilterError(f"unknown target_type: {target_type}")
            sql += " AND target_type = ?"
            params.append(target_type)
        if enabled is not None:
            sql += " AND enabled = ?"
            params.append(1 if enabled else 0)
        if q:
            sql += " AND keyword_norm LIKE ?"
            params.append(f"%{normalize(q)}%")
        sql += " ORDER BY hit_count DESC, id DESC"
        conn = _connect()
        try:
            rows = [self._serialize(dict(r)) for r in conn.execute(sql, params).fetchall()]
        finally:
            conn.close()
        return rows

    def get_rule(self, rule_id):
        conn = _connect()
        try:
            row = conn.execute("SELECT * FROM filter_rules WHERE id = ?", (rule_id,)).fetchone()
        finally:
            conn.close()
        return self._serialize(dict(row)) if row else None

    def add_rule(
        self,
        keyword,
        target_type=TARGET_TITLE,
        match_mode=MATCH_CONTAINS,
        action=ACTION_HARD_BLOCK,
        enabled=True,
        source="manual",
    ):
        keyword, norm = self._validate(keyword, target_type, match_mode, action)
        conn = _connect()
        try:
            conn.execute("BEGIN")
            cur = conn.execute(
                "INSERT OR IGNORE INTO filter_rules"
                "(target_type, keyword, keyword_norm, match_mode, action, enabled, source, "
                " created_at, updated_at) "
                "VALUES(?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
                (target_type, keyword, norm, match_mode, action, 1 if enabled else 0, source),
            )
            created = bool(cur.rowcount)
            if not created:
                # 已存在：更新行为，保证「改动即生效」
                conn.execute(
                    "UPDATE filter_rules SET action = ?, enabled = ?, updated_at = CURRENT_TIMESTAMP "
                    "WHERE target_type = ? AND keyword_norm = ? AND match_mode = ?",
                    (action, 1 if enabled else 0, target_type, norm, match_mode),
                )
            row = conn.execute(
                "SELECT * FROM filter_rules WHERE target_type = ? AND keyword_norm = ? AND match_mode = ?",
                (target_type, norm, match_mode),
            ).fetchone()
            if created:
                _bump_filter_version(conn)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        if created:
            self.invalidate()
        return {**self._serialize(dict(row)), "created": created}

    def update_rule(self, rule_id, **fields):
        allowed = {"enabled", "action", "match_mode", "keyword"}
        unknown = set(fields) - allowed
        if unknown:
            raise FilterError(f"unsupported fields: {sorted(unknown)}")
        conn = _connect()
        try:
            existing = conn.execute("SELECT * FROM filter_rules WHERE id = ?", (rule_id,)).fetchone()
            if not existing:
                raise FilterError("rule not found")
            existing = dict(existing)
            sets = []
            params = []

            if "enabled" in fields:
                sets.append("enabled = ?")
                params.append(1 if fields["enabled"] else 0)
            if "action" in fields:
                if fields["action"] not in ACTIONS:
                    raise FilterError(f"unknown action: {fields['action']}")
                sets.append("action = ?")
                params.append(fields["action"])
            if "match_mode" in fields:
                if fields["match_mode"] not in MATCH_MODES:
                    raise FilterError(f"unknown match_mode: {fields['match_mode']}")
                sets.append("match_mode = ?")
                params.append(fields["match_mode"])
            if "keyword" in fields:
                keyword = (fields["keyword"] or "").strip()
                if not keyword or len(keyword) > MAX_KEYWORD_LEN:
                    raise FilterError("invalid keyword")
                sets.append("keyword = ?")
                params.append(keyword)
                sets.append("keyword_norm = ?")
                params.append(normalize(keyword))
            if not sets:
                return self._serialize(existing)

            sets.append("updated_at = CURRENT_TIMESTAMP")
            conn.execute("BEGIN")
            conn.execute(f"UPDATE filter_rules SET {', '.join(sets)} WHERE id = ?", (*params, rule_id))
            _bump_filter_version(conn)
            row = conn.execute("SELECT * FROM filter_rules WHERE id = ?", (rule_id,)).fetchone()
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        self.invalidate()
        return self._serialize(dict(row))

    def delete_rule(self, rule_id):
        conn = _connect()
        try:
            conn.execute("BEGIN")
            cur = conn.execute("DELETE FROM filter_rules WHERE id = ?", (rule_id,))
            deleted = bool(cur.rowcount)
            if deleted:
                _bump_filter_version(conn)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        if deleted:
            self.invalidate()
        return deleted

    def import_rules(self, text, target_type=TARGET_TITLE, action=ACTION_HARD_BLOCK, source="manual"):
        """批量导入：按 | 或换行拆分 -> trim -> 去空 -> casefold 去重 -> upsert。"""
        if target_type not in TARGET_TYPES:
            raise FilterError(f"unknown target_type: {target_type}")
        if action not in ACTIONS:
            raise FilterError(f"unknown action: {action}")

        raw = (text or "").replace("|", "\n").splitlines()
        entries = []
        seen = set()
        invalid = 0
        duplicate_in_input = 0
        for item in raw:
            keyword = item.strip()
            if not keyword:
                continue
            if len(keyword) > MAX_KEYWORD_LEN:
                invalid += 1
                continue
            norm = normalize(keyword)
            if norm in seen:
                duplicate_in_input += 1
                continue
            seen.add(norm)
            entries.append((keyword, norm))

        if not entries:
            return {
                "input": invalid + duplicate_in_input,
                "inserted": 0,
                "duplicate": duplicate_in_input,
                "invalid": invalid,
                "total": len(self.list_rules(target_type=target_type)),
            }

        inserted = 0
        duplicate = 0
        conn = _connect()
        try:
            conn.execute("BEGIN")
            for keyword, norm in entries:
                cur = conn.execute(
                    "INSERT OR IGNORE INTO filter_rules"
                    "(target_type, keyword, keyword_norm, match_mode, action, enabled, source, "
                    " created_at, updated_at) "
                    "VALUES(?, ?, ?, ?, ?, 1, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
                    (target_type, keyword, norm, MATCH_CONTAINS, action, source),
                )
                if cur.rowcount:
                    inserted += 1
                else:
                    duplicate += 1
            if inserted:
                _bump_filter_version(conn)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        if inserted:
            self.invalidate()
        return {
            "input": len(entries) + invalid + duplicate_in_input,
            "inserted": inserted,
            # duplicate = 文本内重复 + 与库中已有规则重复
            "duplicate": duplicate + duplicate_in_input,
            "invalid": invalid,
            "total": len(self.list_rules(target_type=target_type)),
        }

    # ---------------- 精确屏蔽 UP（保留原有 API 行为） ----------------

    def list_blocked_ups(self):
        conn = _connect()
        try:
            rows = [dict(r) for r in conn.execute("SELECT * FROM blocked_ups ORDER BY id DESC").fetchall()]
        finally:
            conn.close()
        return rows

    def add_blocked_up(self, name, mid=None):
        name = (name or "").strip()
        if not name:
            raise FilterError("invalid up")
        conn = _connect()
        try:
            exists = conn.execute("SELECT * FROM blocked_ups WHERE mid=? OR (mid IS NULL AND name=? AND ? IS NULL)", (mid,name,mid)).fetchone()
            if exists:
                return dict(exists)
            conn.execute("BEGIN")
            cur = conn.execute(
                "INSERT INTO blocked_ups(mid, name, created_at) VALUES(?, ?, ?)", (mid, name, now())
            )
            conn.execute(
                "INSERT INTO feedback(bvid, action, video, created_at,blocked_up_id) VALUES(?, ?, ?, ?,?)",
                ("", "block_up", json.dumps({"author": name, "mid": mid}, ensure_ascii=False), now(),cur.lastrowid),
            )
            _bump_filter_version(conn)
            row = conn.execute("SELECT * FROM blocked_ups WHERE id = ?", (cur.lastrowid,)).fetchone()
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        self.invalidate()
        return dict(row)

    def delete_blocked_up(self, up_id):
        conn = _connect()
        try:
            conn.execute("BEGIN")
            row = conn.execute("SELECT mid,name FROM blocked_ups WHERE id=?", (up_id,)).fetchone()
            cur = conn.execute("DELETE FROM blocked_ups WHERE id = ?", (up_id,))
            deleted = bool(cur.rowcount)
            if deleted:
                for event in conn.execute("SELECT id,video,blocked_up_id FROM feedback WHERE action='block_up' AND revoked_at IS NULL").fetchall():
                    video = json.loads(event["video"] or "{}")
                    if event["blocked_up_id"] == up_id or (event["blocked_up_id"] is None and ((row["mid"] is not None and video.get("mid") == row["mid"]) or (row["mid"] is None and video.get("author") == row["name"]))):
                        conn.execute("UPDATE feedback SET revoked_at=? WHERE id=?", (now(),event["id"]))
                _bump_filter_version(conn)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        if deleted:
            self.invalidate()
        return deleted

    # ---------------- 统计 ----------------

    def summary(self):
        conn = _connect()
        try:
            rows = conn.execute(
                "SELECT target_type, COUNT(*) total, "
                "SUM(enabled) enabled, "
                "SUM(CASE WHEN action = 'hard_block' THEN 1 ELSE 0 END) hard_block, "
                "SUM(CASE WHEN source = 'biliblock_import' THEN 1 ELSE 0 END) imported, "
                "SUM(hit_count) hits "
                "FROM filter_rules GROUP BY target_type"
            ).fetchall()
        finally:
            conn.close()
        return {
            "version": get_filter_version(),
            "by_target": [dict(r) for r in rows],
        }

    @staticmethod
    def _validate(keyword, target_type, match_mode, action):
        keyword = (keyword or "").strip()
        if not keyword:
            raise FilterError("empty keyword")
        if len(keyword) > MAX_KEYWORD_LEN:
            raise FilterError("keyword too long")
        if target_type not in TARGET_TYPES:
            raise FilterError(f"unknown target_type: {target_type}")
        if match_mode not in MATCH_MODES:
            raise FilterError(f"unknown match_mode: {match_mode}")
        if action not in ACTIONS:
            raise FilterError(f"unknown action: {action}")
        norm = normalize(keyword)
        if match_mode == MATCH_REGEX:
            import re

            try:
                re.compile(norm)
            except re.error as e:
                raise FilterError(f"invalid regex: {e}") from e
        return keyword, norm

    @staticmethod
    def _serialize(row):
        row["enabled"] = bool(row["enabled"])
        row["hit_count"] = int(row["hit_count"] or 0)
        return row


filters = FilterService()
