"""v0.3.2 的迁移、真实日期、共享身份、约束与持久预算回归。"""
import json
import sqlite3
import threading
import time
import unittest
from unittest.mock import patch

from backend.tests import test_v031 as fixtures
video, FakeBundle, db, pool, rs, bili, record_history = (fixtures.video, fixtures.FakeBundle, fixtures.db, fixtures.pool, fixtures.rs, fixtures.bili, fixtures.record_history)
from backend.services import interest_profile as profile, recommendation_policy as policy, request_coordination as coordination
from backend.services.source_mixer import mix, settings, quotas
from backend.storage.migrate_v032 import upgrade, downgrade


class V032Test(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.V031Test("test_default_quota_and_native_share")
        self.fixture.setUp()

    def tearDown(self):
        self.fixture.tearDown()

    def test_unique_body_reasons_and_complete_protection(self):
        pool._save("related", [video(seed_bvid="one")])
        pool._save("related", [video(seed_bvid="two", _detail_complete=False, tag=[])])
        pool._save("hot", [video(source="hot", _detail_complete=False, tag=[])])
        with db.connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM candidates").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM candidate_sources").fetchone()[0], 2)
        result = pool.all(("related", "hot"))[0]
        self.assertEqual(result["tag"], ["测试"])
        self.assertEqual(len(result["source_reasons"]["related"]), 2)
        self.assertEqual(set(result["sources"]), {"related", "hot"})

    def test_details_and_scoring_unique_across_sources(self):
        for source in ("related", "hot", "rcmd"):
            pool._save(source, [video(source=source, _detail_complete=False)])
        with patch.object(bili, "detail", return_value=video()) as detail:
            self.assertEqual(pool.complete(("related", "hot", "rcmd")), 1)
        detail.assert_called_once_with("BV1")
        bundle = FakeBundle()
        with patch.object(bundle, "score", wraps=bundle.score) as score:
            ranked = rs.recommendation.rank_sources([v for s in ("related", "hot", "rcmd") for v in pool.all((s,))], "for_you", "all", set(), bundle)
        self.assertEqual(len(score.call_args.args[0]), 1)
        self.assertEqual(set(ranked), {"related", "hot", "rcmd"})

    def test_relation_ttl_keeps_other_source(self):
        pool._save("hot", [video()])
        pool._save("related", [video()])
        with db.connect() as conn:
            conn.execute("UPDATE candidate_sources SET last_refresh_at=0 WHERE source='related'")
        pool._save("related", [])
        self.assertEqual(pool.count("related"), 0)
        self.assertEqual(pool.count("hot"), 1)
        self.assertIsNotNone(pool.get("BV1"))

    def test_global_floor_boundary_and_source_cooldown(self):
        clock = time.time()
        self.fixture.position("BV1", "hot", served_at=clock-1799)
        with patch.object(rs.time, "time", return_value=clock):
            self.assertIn(("BV1", "related"), rs.recommendation._cooled_bvids())
        with patch.object(rs.time, "time", return_value=clock+1):
            self.assertNotIn(("BV1", "related"), rs.recommendation._cooled_bvids())
            self.assertIn(("BV1", "hot"), rs.recommendation._cooled_bvids())

    def test_commit_rechecks_cooldown_before_any_position_write(self):
        item = rs.recommendation._item(video(), None, [])
        item["rank"] = 1
        self.fixture.position("BV1", "hot")
        items = [item]
        rs.recommendation._record("commit", "for_you", "all", 0, items, False, time.time(), {"model_version": "fallback", "view_id": "view", "experiment_id": None, "arm": None, "page_limit": 12}, recheck_cooldown=True)
        self.assertEqual(items, [])
        with db.connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM recommendation_history WHERE stream_id='commit'").fetchone()[0], 0)
            self.assertEqual(json.loads(conn.execute("SELECT items FROM feed_cache WHERE stream_id='commit'").fetchone()[0]), [])

    def test_long_watch_dates_and_unknown_time(self):
        clock = time.time()
        for i in range(5):
            record_history(video(str(i), source="history", progress=50, view_at=clock-(40-i*10)*86400))
        value = profile.publish(clock)["topics"][0]
        self.assertTrue(value["qualified"])
        self.assertGreater(value["L"], 0)
        self.assertEqual(value["watch_bvids"], 5)
        profile.update_settings({"themes": {"测试": {"state": "paused"}}})
        self.assertEqual(profile.match_scores(video(), profile.snapshot())["L"], 0)
        with db.connect() as conn:
            conn.execute("DELETE FROM history_events")
        for i in range(10):
            record_history(video(str(i), source="history", progress=100))
        self.assertEqual(profile.publish()["topics"][0]["L"], 0)

    def test_explicit_distinct_dates_and_favorite_purposes(self):
        clock = time.time()
        for i in range(3):
            record_history(video(str(i), source="favorite", fav_time=clock-i*16*86400))
        self.assertTrue(profile.publish(clock)["topics"][0]["qualified"])
        for i in range(3):
            profile.set_preference(str(i), {"purpose": "utility"})
        value = profile.snapshot()["topics"][0]
        self.assertEqual(value["L"], 0)
        self.assertLessEqual(value["S"], .25/6)
        # 用途不影响真实观看或原训练标签。
        from backend.services.training_data import label
        self.assertEqual(label(video(isfaved=1))[0], 1)

    def test_like_state_without_time_cannot_qualify(self):
        for i in range(5):
            record_history(video(str(i), source="history", isliked=1, view_at=time.time()-i*86400*20))
        value = profile.publish()["topics"][0]
        self.assertFalse(value["qualified"])
        self.assertEqual(value["L"], 0)

    def test_short_daily_cap_decay_and_alias(self):
        clock = time.time()
        profile.update_settings({"aliases": {"test": "测试"}})
        for i in range(9):
            record_history(video(str(i), tag=["test"], source="favorite", fav_time=clock))
        value = profile.publish(clock)["topics"][0]
        self.assertEqual(value["name"], "测试")
        self.assertEqual(value["S"], 1)
        self.assertAlmostEqual(profile.publish(clock+3*86400)["topics"][0]["S"], .5)

    def test_fixed_excluded_and_long_preservation(self):
        profile.update_settings({"themes": {"旧爱": {"state": "fixed", "words": ["旧作品"]}}})
        self.assertEqual(profile.match_scores(video(title="旧作品"), profile.snapshot())["L"], 1)
        profile.update_settings({"themes": {"旧爱": {"state": "excluded", "words": ["旧作品"]}}})
        self.assertEqual(profile.match_scores(video(title="旧作品"), profile.snapshot())["L"], 0)

    def test_search_opt_in_event_dedup_and_delete(self):
        self.assertFalse(profile.record_search("测试", "event"))
        profile.update_settings({"search_memory": True, "themes": {"测试": {"state": "auto"}}})
        self.assertTrue(profile.record_search("测试", "event"))
        self.assertFalse(profile.record_search("测试", "event"))
        self.assertEqual(len(profile.search_history()), 1)
        profile.delete_search("event")
        self.assertEqual(profile.search_history(), [])

    def test_hour_budget_concurrency_persistence_and_collection_exemption(self):
        db.set_state("requests:candidate_hour", [[time.time(), False] for _ in range(239)])
        admitted = []
        def attempt():
            try:
                coordination.consume({"purpose": "candidate", "background": True})
                admitted.append(True)
            except coordination.BudgetEnded:
                pass
        workers = [threading.Thread(target=attempt) for _ in range(6)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join()
        self.assertEqual(len(admitted), 1)
        self.assertEqual(len(db.get_state("requests:candidate_hour")), 240)
        coordination.consume({"purpose": "collection", "background": True})
        with self.assertRaises(coordination.BudgetEnded):
            coordination.consume({"purpose": "candidate", "background": True})

    def test_search_sub_budget_includes_nav_not_details(self):
        db.set_state("requests:candidate_hour", [[time.time(), True] for _ in range(40)])
        with self.assertRaises(coordination.BudgetEnded):
            coordination.consume({"purpose": "search", "background": True, "url": "/nav"})
        coordination.consume({"purpose": "search", "background": True, "url": "/view/detail"})
        self.assertEqual(len(db.get_state("requests:candidate_hour")), 41)

    def test_policy_caps_unscored_and_closed_source(self):
        profile.update_settings({"themes": {"测试": {"state": "fixed"}}})
        frozen = policy.freeze(True)
        candidates = [video(str(i), mid=i//2, source="related") for i in range(30)]
        ranked = rs.recommendation.rank_sources(candidates, "for_you", "all", set(), None, policy=frozen)
        items = mix(ranked, options={**settings(), "_policy": frozen})
        self.assertEqual(len({v["mid"] for v in items}), len(items))
        self.assertTrue(all(v["rating"] is None and v["rule_score"] is None for v in items))
        self.assertTrue(all(v["policy_diagnostics"]["top4_gap"] == 4 for v in items))
        frozen["settings"]["archive"] = "off"
        self.assertEqual(quotas(12, {**settings(), "_policy": frozen})["up_archive"], 0)

    def test_replay_requires_favorite_and_respects_real_time(self):
        record_history(video(source="favorite"))
        record_history(video(source="history", progress=100, view_at=time.time()-31*86400))
        profile.set_preference("BV1", {"allow_replay": True})
        from backend.services.affinity import affinity
        self.assertIn("BV1", profile.replay_eligible(affinity.snapshot()))
        record_history(video(source="history", progress=50, view_at=time.time()))
        self.assertNotIn("BV1", profile.replay_eligible(affinity.snapshot()))

    def test_cached_replay_permission_and_new_watch_are_immediate(self):
        record_history(video(source="favorite"))
        record_history(video(source="history", progress=100, view_at=time.time()-31*86400))
        profile.set_preference("BV1", {"allow_replay": True})
        pool._save("related", [video()])
        page = rs.recommendation.refresh("for_you", view_id="replay")
        self.assertTrue(page["items"][0]["replay"])
        profile.set_preference("BV1", {"allow_replay": False})
        self.assertEqual(rs.recommendation.get_feed("for_you", cursor=page["stream_id"]+":0")["items"], [])
        profile.set_preference("BV1", {"allow_replay": True})
        record_history(video(source="history", progress=100, view_at=time.time()))
        self.assertEqual(rs.recommendation.get_feed("for_you", cursor=page["stream_id"]+":0")["items"], [])

    def test_vertical_hourly_pages_dedup_and_failure_does_not_switch_query(self):
        from backend.services.vertical_search import fetch
        from backend.services.source_mixer import update_settings
        from backend.services.bilibili_service import BiliError
        update_settings({"vertical_search": "small"})
        profile.update_settings({"themes": {"测试": {"state": "fixed", "queries": ["query"]}}})
        with patch.object(bili, "search", return_value={"items": [video(), video()], "has_more": True}) as search:
            self.assertEqual(len(fetch()), 1)
            self.assertEqual(fetch(), [])
            self.assertEqual(fetch(), [])
        self.assertEqual([call.args[1] for call in search.call_args_list], [1, 2])
        db.set_state("vertical:queries", {})
        profile.update_settings({"themes": {"测试": {"state": "fixed", "queries": ["query", "other"]}}})
        with patch.object(bili, "search", side_effect=BiliError(-400)) as search:
            self.assertEqual(fetch(), [])
        search.assert_called_once()

    def test_policy_blind_report_requires_five_complete_batches(self):
        from pathlib import Path
        from backend.services import policy_review
        path = Path(self.fixture.tmp.name)/"policy-blind"
        path.mkdir()
        ids = [str(i) for i in range(12)]
        document = {"signature": "fixture", "model_version": "old", "batches": [{"at": i, "positions": {"baseline": ids, "strategy": ids}, "evidence": {"baseline": [{"L": 0}]*12, "strategy": [{"L": 1}]*12}} for i in range(5)]}
        (path/"private.json").write_text(json.dumps(document), encoding="utf-8")
        (path/"rating.json").write_text(json.dumps([{"bvid": bv, "score": 2} for bv in ids]), encoding="utf-8")
        with patch.object(policy_review, "location", return_value=path):
            self.assertTrue(policy_review.report()["passed"])
            document["batches"].pop()
            (path/"private.json").write_text(json.dumps(document), encoding="utf-8")
            self.assertFalse(policy_review.report()["passed"])

    def test_vertical_off_never_searches(self):
        from backend.services.vertical_search import fetch
        with patch.object(bili, "search") as search:
            self.assertEqual(fetch(), [])
        search.assert_not_called()

    def test_ready_idle_targets_and_category_expiry(self):
        from backend.services.source_scheduler import SourceScheduler
        worker = SourceScheduler()
        db.set_state("sources:warm_categories", {"科技": time.time(), "旧分类": time.time()-86401})
        with patch.object(rs.recommendation, "ready_buffer", return_value=(3, {})), patch.object(pool, "is_stale", return_value=False), patch.object(pool, "expand") as expand:
            worker._refill()
        self.assertIn(("idle", "all"), worker._demands)
        self.assertIn(("idle", "科技"), worker._demands)
        self.assertNotIn(("idle", "旧分类"), worker._demands)
        expand.assert_not_called()
        self.assertLess(db.get_state("sources:warm_categories")["旧分类"], time.time()-86400)

    def test_full_pool_never_refreshes_vertical_search(self):
        from backend.services.source_scheduler import SourceScheduler
        from backend.services.source_mixer import update_settings
        update_settings({"vertical_search": "standard"})
        with patch.object(rs.recommendation, "ready_buffer", return_value=(3, {})), patch.object(pool, "is_stale", return_value=True), patch.object(pool, "expand") as expand:
            SourceScheduler()._refill()
        self.assertNotIn("vertical_search", [call.args[0] for call in expand.call_args_list])

    def test_logout_does_not_restart_scheduler_while_cookie_exists(self):
        from backend.services.source_scheduler import SourceScheduler
        worker = SourceScheduler()
        worker.stop()
        with patch.object(type(bili), "has_cookie", new_callable=unittest.mock.PropertyMock, return_value=True), patch("backend.services.source_scheduler.threading.Thread") as thread:
            worker.start()
            worker.request_refill("for_you", "all", 12, settings(), "fallback")
        thread.assert_not_called()
        self.assertFalse(worker._demands)

    def test_no_background_login_prefetch_and_callback(self):
        from backend.app import create_app
        from backend.services.source_scheduler import scheduler
        from backend.services.training_service import training
        previous = (scheduler.enabled, training.enabled, rs.recommendation.background_enabled)
        try:
            client = create_app(start_background=False).test_client()
            with patch.object(bili, "qrcode_poll", return_value="success"), patch.object(scheduler, "stop"), patch("backend.services.training_service.threading.Thread") as thread:
                self.assertEqual(client.get("/api/v1/auth/qrcode/status?qrcode_key=fixture").status_code, 200)
                rs.recommendation.prefetch("for_you")
                rs.recommendation._on_model_changed()
                scheduler.start()
            thread.assert_not_called()
        finally:
            scheduler.enabled, training.enabled, rs.recommendation.background_enabled = previous

    def test_special_failure_retains_previous_and_local_override(self):
        from backend.services.source_scheduler import SourceScheduler
        from backend.services.bilibili_service import BiliError
        self.fixture.following(1)
        with db.connect() as conn:
            conn.execute("UPDATE followings SET special=1")
        with patch.object(bili, "followings", return_value={"items": [{"mid": 1, "name": "UP", "special": None}], "has_more": False}), patch.object(bili, "special_followings", side_effect=BiliError(-404)):
            SourceScheduler().sync_followings()
        self.assertEqual(profile.special_ups(), {"1"})
        self.assertEqual(db.get_state("sources:special_status")["status"], "failed")
        profile.update_settings({"special_ups": {"1": {"enabled": False}}})
        self.assertEqual(profile.special_ups(), set())

    def test_primary_query_does_not_leak_between_sources(self):
        from backend.services.source_mixer import update_settings
        update_settings({"vertical_search": "small"})
        pool._save("vertical_search", [video(query="测试")])
        pool._save("related", [video(seed_bvid="seed")])
        ranked = rs.recommendation.rank_sources(pool.all(("vertical_search", "related")), "for_you", "all", set(), None)
        self.assertNotIn("query", ranked["related"][0]["_snapshot"])
        self.assertEqual(ranked["vertical_search"][0]["_snapshot"]["query"], "测试")
        profile.update_settings({"query_blacklist": ["测试"]})
        ranked = rs.recommendation.rank_sources(pool.all(("vertical_search", "related")), "for_you", "all", set(), None)
        self.assertFalse(ranked.get("vertical_search"))
        self.assertEqual(len(ranked["related"]), 1)

    def test_history_explanation_keeps_frozen_evidence(self):
        profile.update_settings({"themes": {"测试": {"state": "fixed"}}})
        frozen = policy.freeze(True)
        ranked = rs.recommendation.rank_sources([video()], "for_you", "all", set(), FakeBundle(), policy=frozen)
        item = mix(ranked, options={**settings(), "_policy": frozen})[0]
        item["rank"] = 1
        rs.recommendation._record("stream", "for_you", "all", 0, [item], False, time.time(), {"model_version": "old", "view_id": "view", "experiment_id": None, "arm": None, "page_limit": 12})
        profile.update_settings({"themes": {"测试": {"state": "excluded"}}})
        result = rs.recommendation.explain("BV1", item["recommendation_id"])
        self.assertEqual(result["policy"]["L"], 1)
        self.assertEqual(result["policy"]["profile_version"], frozen["profile"]["version"])

    def test_exploration_and_short_caps_top4_and_frequency(self):
        profile.update_settings({"themes": {"长期": {"state": "fixed"}}})
        clock = time.time()
        for i in range(4):
            record_history(video("history"+str(i), tag=["短期"], mid=i+100, source="history", progress=100, view_at=clock))
        profile.publish()
        frozen = policy.freeze(True)
        frozen["settings"]["exploration"] = "active"
        candidates = [video("long"+str(i), tag=["长期"], mid=i+1) for i in range(8)] + [video("short"+str(i), tag=["短期"], mid=i+20) for i in range(8)]
        items = mix(rs.recommendation.rank_sources(candidates, "for_you", "all", set(), FakeBundle(), policy=frozen), options={**settings(), "_policy": frozen})
        self.assertLessEqual(sum(v["_policy"]["pure_short"] for v in items), 3)
        self.assertTrue(all(v["_policy"]["top4_eligible"] for v in items[:4]))
        frozen["frequency"]["1"] = 8
        value = policy.annotate(rs.recommendation._item(video(mid=1), .7, []), frozen, {"ups": {}, "followings": set()})
        self.assertEqual(value["rating"], .7)
        self.assertEqual(value["_policy"]["creator_penalty"], .06)

    def test_policy_trial_requires_gate_and_never_stops_model_trial(self):
        from backend.services.model_registry import registry
        registry.update(trial={"status": "running", "id": "model-trial"})
        with self.assertRaises(ValueError):
            policy.trial_action("start")
        self.assertEqual(registry.state()["trial"]["id"], "model-trial")
        registry.update(trial=None)
        with self.assertRaises(ValueError):
            policy.trial_action("start")


class MigrationTest(unittest.TestCase):
    def test_upgrade_downgrade_preserves_new_feedback(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("CREATE TABLE app_state(key TEXT PRIMARY KEY,value TEXT,updated_at REAL)")
        conn.execute("CREATE TABLE feedback(value TEXT)")
        conn.execute("CREATE TABLE candidates(bvid TEXT,source TEXT,data TEXT,created_at REAL,last_refresh_at REAL,PRIMARY KEY(bvid,source))")
        for source, complete, refresh in (("hot", False, 3), ("related", True, 2), ("rcmd", True, 1)):
            conn.execute("INSERT INTO candidates VALUES(?,?,?,?,?)", ("BV1", source, json.dumps(video(source=source, _detail_complete=complete)), 1, refresh))
        upgrade(conn)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM candidates").fetchone()[0], 1)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM candidate_sources").fetchone()[0], 3)
        self.assertTrue(json.loads(conn.execute("SELECT data FROM candidates").fetchone()[0])["_detail_complete"])
        conn.execute("INSERT INTO feedback VALUES('新增反馈')")
        downgrade(conn)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM candidates").fetchone()[0], 3)
        self.assertEqual(conn.execute("SELECT value FROM feedback").fetchone()[0], "新增反馈")
        upgrade(conn)
        self.assertFalse(conn.execute("PRAGMA foreign_key_check").fetchall())
        conn.close()

    def test_invalid_json_does_not_modify_old_table(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("CREATE TABLE candidates(bvid TEXT,source TEXT,data TEXT,created_at REAL,last_refresh_at REAL)")
        conn.execute("INSERT INTO candidates VALUES('BV1','hot','bad json',1,1)")
        with self.assertRaises(ValueError):
            upgrade(conn)
        self.assertIn("source", {r[1] for r in conn.execute("PRAGMA table_info(candidates)")})
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM candidates").fetchone()[0], 1)
        conn.close()
