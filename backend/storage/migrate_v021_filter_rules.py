"""v0.2.1 过滤规则数据迁移：blocked_keywords -> filter_rules + BiliBlock 导入。

设计要点：
- schema 由 backend/storage/migrations.py 的 SCHEMA 提供（CREATE IF NOT EXISTS，幂等）。
- 本脚本只做「数据迁移 / 导入」，单事务执行，任一步失败整体 rollback。
- 不删除任何既有数据；blocked_keywords 表保留不动，仅复制为 title 规则。
- 可重复执行：依赖 idx_filter_rules_unique 做 INSERT OR IGNORE，不会产生重复规则。

用法：
    .venv/Scripts/python.exe backend/storage/migrate_v021_filter_rules.py
"""

import json
import os
import sqlite3
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from backend.config import DB_PATH
from backend.storage.migrations import SCHEMA

# ---------------- 本次导入数据（来源：v0.2.1 任务书，原文照抄不做增删） ----------------

TITLE_KEYWORDS = """
你长大了 | 大一 | 以防你不知道 | 大冰 | 揭露 | 本质 | 训狗 | 建议收藏 | 为啥 | 风俗简史 | 智商税 | 小伙 | 鬼叔 | 感谢您 | 寻找童年 | 食录 | 良子 | 我真的很需要 | 为什么现在的 | 事态升级 | 大型纪录片 | 徐静雨 | 瓶子 | 只有我一个人觉得 | 补档 | 爆典 | 坚持不下去 | 直播切片 | 骗局 | 张雪峰 | vlog | 通俗易懂 | 终于把 | 蓝牙耳机推荐 | 降噪全能选手 | 高颜值高性价比 | 学生党 | 考公 | 广告 | 挑战 | 推广 | 真相 | 演都不演 | 曼波 | 哈基米 | 被网暴 | 为什么很多人 | 指南 | epz | 游戏耳机 | 开放式耳机 | 国补 | 以防你没有 | 假期 | 劳动法 | 考研 | 上岸 | 程序员 | 绘画 | 理想 | 为什么很少 | 百元神器 | 电竞神器 | 留学 | 日语教程
"""

UPLOADER_KEYWORDS = """
记录生活 | 大冰 | 疯话 | 真相 | 赛博食录 | 鸭魂 | 文化遗产 | 平台特惠 | 浪子 | 小岳来了 | 食来话长 | 在生活 | 子文又饿了 | 会魔法的头哥 | 差评君 | 王师傅の日记 | 罐头哥 | 咸鱼梦想家 | 桥半舫 | Java面试分享官 | AI绘画 | ai动画 | 李什么闯啊 | 推荐 | 教学 | 说科技 | 数码 | 码士集团 | Python教程 | 是莫叔吗 | 马督工 | 张雪峰
"""

SOURCE_IMPORT = "biliblock_import"
SOURCE_LEGACY = "legacy_blocked_keywords"
TARGET_TITLE = "title"
TARGET_UPLOADER = "uploader"
MATCH_CONTAINS = "contains"
ACTION_HARD_BLOCK = "hard_block"

TRACKED_TABLES = (
    "app_state",
    "blocked_keywords",
    "blocked_ups",
    "candidates",
    "feed_cache",
    "feedback",
    "recommendation_history",
    "served_videos",
)


def parse_keywords(raw):
    """按 | 或换行拆分，trim + 去空。返回保持原顺序的原始文本列表。"""
    text = raw.replace("|", "\n")
    return [line.strip() for line in text.splitlines() if line.strip()]


def normalize(keyword):
    """归一化：trim + casefold（中文按包含关系匹配，英文大小写不敏感）。"""
    return keyword.strip().casefold()


def table_counts(conn):
    counts = {}
    for name in TRACKED_TABLES:
        try:
            counts[name] = conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
        except sqlite3.OperationalError:
            counts[name] = None
    try:
        counts["filter_rules"] = conn.execute("SELECT COUNT(*) FROM filter_rules").fetchone()[0]
    except sqlite3.OperationalError:
        counts["filter_rules"] = None
    return counts


def main():
    if not os.path.exists(DB_PATH):
        print(f"数据库不存在：{DB_PATH}")
        return 1
    print(f"数据库：{DB_PATH}")

    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row

    print("\n=== 迁移前完整性检查 ===")
    print("integrity_check:", conn.execute("PRAGMA integrity_check").fetchone()[0])
    fk = conn.execute("PRAGMA foreign_key_check").fetchall()
    print("foreign_key_check:", [tuple(r) for r in fk] or "ok")

    before = table_counts(conn)
    print("\n=== 迁移前各表行数 ===")
    for k, v in before.items():
        print(f"  {k:<24} {v}")

    # 逐行清洗 + 去重（同一 target_type + keyword_norm + match_mode 只保留第一条，保留原始显示文本）
    title_raw = parse_keywords(TITLE_KEYWORDS)
    uploader_raw = parse_keywords(UPLOADER_KEYWORDS)

    title_rules, title_dup_in_input = _dedup(title_raw)
    uploader_rules, uploader_dup_in_input = _dedup(uploader_raw)

    print("\n=== 输入解析 ===")
    print(f"  标题关键词：原始 {len(title_raw)} 条，输入内重复/空 {title_dup_in_input} 条，去重后 {len(title_rules)} 条")
    print(f"  UP 关键词：原始 {len(uploader_raw)} 条，输入内重复/空 {uploader_dup_in_input} 条，去重后 {len(uploader_rules)} 条")

    stats = {
        "legacy_total": 0,
        "legacy_migrated": 0,
        "legacy_duplicate": 0,
        "title_input": len(title_rules),
        "uploader_input": len(uploader_rules),
        "title_inserted": 0,
        "uploader_inserted": 0,
        "title_duplicate": 0,
        "uploader_duplicate": 0,
        "failed": 0,
    }

    try:
        conn.execute("BEGIN")
        # schema（幂等）
        conn.executescript(SCHEMA)

        # 1) 迁移 blocked_keywords -> title 规则
        legacy_rows = conn.execute(
            "SELECT id, keyword, enabled FROM blocked_keywords ORDER BY id"
        ).fetchall()
        stats["legacy_total"] = len(legacy_rows)
        for row in legacy_rows:
            kw = (row["keyword"] or "").strip()
            if not kw:
                continue
            cur = conn.execute(
                "INSERT OR IGNORE INTO filter_rules"
                "(target_type, keyword, keyword_norm, match_mode, action, enabled, source, created_at, updated_at) "
                "VALUES(?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
                (
                    TARGET_TITLE,
                    kw,
                    normalize(kw),
                    MATCH_CONTAINS,
                    ACTION_HARD_BLOCK,
                    int(row["enabled"] or 0),
                    SOURCE_LEGACY,
                ),
            )
            if cur.rowcount:
                stats["legacy_migrated"] += 1
            else:
                stats["legacy_duplicate"] += 1

        # 2) 导入 BiliBlock 标题关键词
        for kw, norm in title_rules:
            cur = conn.execute(
                "INSERT OR IGNORE INTO filter_rules"
                "(target_type, keyword, keyword_norm, match_mode, action, enabled, source, created_at, updated_at) "
                "VALUES(?, ?, ?, ?, ?, 1, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
                (TARGET_TITLE, kw, norm, MATCH_CONTAINS, ACTION_HARD_BLOCK, SOURCE_IMPORT),
            )
            if cur.rowcount:
                stats["title_inserted"] += 1
            else:
                stats["title_duplicate"] += 1

        # 3) 导入 BiliBlock UP 主名称关键词
        for kw, norm in uploader_rules:
            cur = conn.execute(
                "INSERT OR IGNORE INTO filter_rules"
                "(target_type, keyword, keyword_norm, match_mode, action, enabled, source, created_at, updated_at) "
                "VALUES(?, ?, ?, ?, ?, 1, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
                (TARGET_UPLOADER, kw, norm, MATCH_CONTAINS, ACTION_HARD_BLOCK, SOURCE_IMPORT),
            )
            if cur.rowcount:
                stats["uploader_inserted"] += 1
            else:
                stats["uploader_duplicate"] += 1

        # 4) 规则集变化 -> 过滤版本 +1，并清空 feed_cache（不清其它表）
        version = _bump_version(conn)
        deleted_cache = conn.execute("DELETE FROM feed_cache").rowcount

        conn.commit()
    except Exception as e:
        conn.rollback()
        print(f"\n迁移失败，已 rollback：{type(e).__name__}: {e}")
        conn.close()
        return 1

    after = table_counts(conn)
    print("\n=== 迁移后各表行数 ===")
    for k, v in after.items():
        delta = "" if before.get(k) == v else f"   (变化 {before.get(k)} -> {v})"
        print(f"  {k:<24} {v}{delta}")

    print("\n=== filter_rules 汇总 ===")
    for row in conn.execute(
        "SELECT target_type, COUNT(*) n, SUM(enabled) en, "
        "SUM(CASE WHEN action='hard_block' THEN 1 ELSE 0 END) hb, "
        "SUM(CASE WHEN source=? THEN 1 ELSE 0 END) imp "
        "FROM filter_rules GROUP BY target_type",
        (SOURCE_IMPORT,),
    ):
        print(
            f"  {row['target_type']:<10} 总数={row['n']:<4} 启用={row['en']:<4} "
            f"hard_block={row['hb']:<4} 来自导入={row['imp']}"
        )
    total = conn.execute("SELECT COUNT(*) FROM filter_rules").fetchone()[0]
    print(f"  filter_rules 最终总数：{total}")

    print("\n=== 统计 ===")
    print(f"  旧 blocked_keywords 原有：{stats['legacy_total']} 条")
    print(f"  成功迁移为 title 规则：{stats['legacy_migrated']} 条（重复跳过 {stats['legacy_duplicate']}）")
    print(f"  标题规则输入 {stats['title_input']}：新增 {stats['title_inserted']}，重复跳过 {stats['title_duplicate']}")
    print(f"  UP 规则输入 {stats['uploader_input']}：新增 {stats['uploader_inserted']}，重复跳过 {stats['uploader_duplicate']}")
    print(f"  过滤器版本 filters:version = {version}，清空 feed_cache {deleted_cache} 行")
    print(f"  失败：{stats['failed']}")

    print("\n=== 迁移后完整性检查 ===")
    print("integrity_check:", conn.execute("PRAGMA integrity_check").fetchone()[0])
    fk = conn.execute("PRAGMA foreign_key_check").fetchall()
    print("foreign_key_check:", [tuple(r) for r in fk] or "ok")

    conn.close()
    return 0


def _dedup(raw_list):
    """返回 [(原始文本, norm)]，按 norm 去重，保持首次出现顺序。"""
    seen = set()
    result = []
    dup = 0
    for kw in raw_list:
        norm = normalize(kw)
        if not norm or norm in seen:
            dup += 1
            continue
        seen.add(norm)
        result.append((kw, norm))
    return result, dup


def _bump_version(conn):
    row = conn.execute("SELECT value FROM app_state WHERE key = 'filters:version'").fetchone()
    version = (json.loads(row["value"]) if row else 0) + 1
    conn.execute(
        "INSERT INTO app_state(key, value, updated_at) VALUES('filters:version', ?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
        (json.dumps(version), time.time()),
    )
    return version


if __name__ == "__main__":
    sys.exit(main())
