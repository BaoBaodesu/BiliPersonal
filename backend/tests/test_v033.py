"""v0.3.3 固定时间、假接口及临时SQLite回归。"""
import json
import time
import unittest
from unittest.mock import patch

from backend.tests import test_v032 as fixtures
video, db, pool, rs, bili, record_history = fixtures.video, fixtures.db, fixtures.pool, fixtures.rs, fixtures.bili, fixtures.record_history
from backend.services.bilibili_service import BiliError
from backend.services import entity_aliases as entities, entity_search, recommendation_controls as controls, recommendation_policy as policy, interest_profile as profile
from backend.services.affinity import affinity
from backend.services.source_scheduler import SourceScheduler
from backend.services.source_mixer import mix


class V033Test(unittest.TestCase):
    setUp = fixtures.V032Test.setUp
    tearDown = fixtures.V032Test.tearDown
    def position(self, name, stream, page, visible_at, **changes):
        position = self.fixture.position(name)
        with db.connect() as conn:
            conn.execute("UPDATE recommendation_history SET stream_id=?,page=?,video_snapshot=? WHERE id=?", (stream, page, json.dumps(video(name, **changes)), position))
            conn.execute("INSERT INTO recommendation_exposures(id,recommendation_id,view_id,received_at,visible_at) VALUES(?,?,'view',?,?)", (str(position), position, time.time(), visible_at))
        return position

    def dictionary(self):
        return entities.update({"version": 0, "entities": [{"id": "e", "type": "up", "name": "EdmundDZhang", "aliases": ["老E"], "accounts": [{"mid": "1", "name": "Edmund"}]}]})

    def test_v033_recent_affinity_dates_and_purposes(self):
        clock = time.time()
        record_history(video("old", source="history", progress=100, view_at=clock-91*86400, isliked=1))
        self.assertNotIn("1", affinity.snapshot()["ups"])
        for i in range(3):
            record_history(video(str(i), source="history", progress=50, view_at=clock-100-i))
        self.assertFalse(affinity.snapshot()["ups"]["1"]["regular"])
        record_history(video("next", source="history", progress=50, view_at=clock-86400))
        self.assertTrue(affinity.snapshot()["ups"]["1"]["regular"])
        record_history(video("fav", mid=2, source="favorite", fav_time=clock))
        self.assertTrue(affinity.snapshot()["ups"]["2"]["regular"])
        profile.set_preference("fav", {"purpose": "support"})
        self.assertNotIn("2", affinity.snapshot()["ups"])

    def test_v033_real_pages_late_events_and_actor_limits(self):
        clock = time.time()
        self.position("a", "s1", 0, clock-100)
        self.position("b", "s1", 1, clock-50)
        self.position("expired", "old", 0, clock-1801, mid=3)
        self.assertEqual(controls.select([video("c"), video("d", mid=2)], 12)[0]["mid"], 2)
        self.assertEqual(len(controls.window()), 2)
        self.assertEqual(len(controls.select([video("c"), video("d")], 12, pages=[])), 1)
        with patch("backend.services.recommendation_controls.time.time", return_value=clock+1801):
            self.assertEqual(len(controls.select([video("c")], 12)), 1)

    def test_v033_entity_longest_short_name_and_ambiguity(self):
        dictionary = {"version": 1, "entities": [{"id": name, "name": name, "aliases": [], "type": "character", "accounts": []} for name in ("赫萝", "赫萝老师", "赫萝的苹果")]}
        self.assertEqual([e["entity_id"] for e in entities.identify(video(title="赫萝老师玩游戏"), dictionary)["entities"]], ["赫萝老师"])
        dictionary["entities"] = dictionary["entities"][:1]
        for title in ("赫萝老师", "赫萝的苹果", "赫萝老师玩游戏"):
            self.assertFalse(entities.identify(video(title=title), dictionary)["entities"])
        self.assertTrue(entities.identify(video(title="【赫萝】", tag=[]), dictionary)["entities"])
        dictionary = self.dictionary()
        dictionary["entities"].append({"id": "other", "name": "另一个", "aliases": ["老E"], "type": "up", "accounts": []})
        self.assertFalse(entities.identify(video(mid=9, title="【老E】切片", tag=[]), dictionary)["entities"])

    def test_v033_same_subject_different_publishers(self):
        self.dictionary()
        first = video("first", mid=5, title="【老E】片段")
        second = video("second", mid=6, title="【老E】片段")
        first["entity_attribution"] = entities.identify(first)
        second["entity_attribution"] = entities.identify(second)
        self.assertEqual(len(controls.select([first, second, video("own", mid=1)], 12, [])), 1)

    def test_v033_freshness_unscored_unknown_and_high_old(self):
        frozen = policy.freeze(False)
        clock = time.time()
        items = [controls.annotate(rs.recommendation._item(video("new", pubdate=clock), .7, []), frozen, affinity.snapshot(), clock),
                 controls.annotate(rs.recommendation._item(video("old", pubdate=clock-365*86400), .95, []), frozen, affinity.snapshot(), clock)]
        self.assertGreater(items[1]["freshness_score"], items[0]["freshness_score"])
        item = controls.annotate(rs.recommendation._item(video(pubdate=clock+1), None, []), frozen, affinity.snapshot(), clock)
        self.assertIsNone(item["rating"])
        self.assertIsNone(item["_controls"]["age_days"])
        self.assertEqual(item["_controls"]["bonus"], 0)

    def test_v033_controls_signature_and_stream_freeze(self):
        original = policy.signature(policy.settings())
        first = rs.recommendation.refresh("for_you", view_id="first")
        policy.update_settings({"freshness": "off", "discovery": "active"})
        self.assertEqual(original, policy.signature(policy.settings()))
        frozen = db.get_state(f'stream:{first["stream_id"]}:policy')
        self.assertEqual(frozen["controls"]["settings"]["freshness"], "standard")

    def test_v033_closed_discovery_does_not_fall_back(self):
        item = {**video(), "_controls": {"intent": "discovery", "settings": {**controls.DEFAULTS, "discovery": "off"}}}
        self.assertFalse(controls.select([item], 12, []))
        self.assertFalse(mix({"related": [item]}, options={"hot": "off", "rcmd": "off", "vertical_search": "off", "_exposure_pages": []}))

    def test_v033_entity_search_compound_dedupe_cursor_and_partial_retry(self):
        self.dictionary()
        with patch.object(bili, "search", side_effect=[{"items": [video("a")], "num_pages": 2}, BiliError(-1)]) as remote:
            first = entity_search.search("老E 游戏实况")
        self.assertEqual([call.args[0] for call in remote.call_args_list], ["老E 游戏实况", "EdmundDZhang 游戏实况"])
        self.assertTrue(first["retry_cursor"])
        with patch.object(bili, "search", return_value={"items": [video("a"), video("b")], "num_pages": 1}) as remote:
            finished = entity_search.search("老E 游戏实况", cursor=first["retry_cursor"])
            self.assertEqual(remote.call_count, 1)
        self.assertEqual([v["bvid"] for v in finished["items"]], ["a", "b"])
        with patch.object(bili, "search") as remote:
            entity_search.search("老E 游戏实况", cursor=first["retry_cursor"])
            remote.assert_not_called()

    def test_v033_weak_related_once_regular_retry(self):
        clock = time.time()
        record_history(video(source="history", progress=100, view_at=clock-100))
        worker = SourceScheduler()
        with patch.object(bili, "related", return_value=[video("related")]) as remote:
            worker.expand_related()
            worker.expand_related()
            self.assertEqual(remote.call_count, 1)
        with patch("backend.services.source_scheduler.time.time", return_value=clock+86401), patch.object(bili, "related") as remote:
            worker.expand_related()
            remote.assert_not_called()

    def test_v033_readiness_poll_does_not_score(self):
        first = rs.recommendation.refresh("for_you", view_id="readiness")
        worker = SourceScheduler()
        worker.enabled = False
        with patch.object(rs.recommendation, "ready_buffer") as score, patch.object(bili, "search") as remote:
            self.assertEqual(worker.readiness(first["stream_id"])["status"], "checking")
            score.assert_not_called()
            remote.assert_not_called()

    def test_v033_background_readiness_publish_and_invalidate(self):
        first = rs.recommendation.refresh("for_you", view_id="waiting")
        worker = SourceScheduler()
        with patch.object(worker, "start"):
            worker.readiness(first["stream_id"])
        self.fixture.save([video("new", mid=3)])
        worker._publish_readiness()
        ready = worker.readiness(first["stream_id"])
        self.assertEqual(ready["status"], "ready")
        self.assertEqual(ready["available"], 1)
        self.assertEqual(ready["complete_pages"], 0)
        rs.recommendation.refresh("for_you", view_id="consume")
        self.assertEqual(worker.readiness(first["stream_id"])["status"], "checking")

    def test_v033_targeted_vertical_independent_and_closed_control(self):
        self.dictionary()
        profile.update_settings({"special_ups": {"1": {"enabled": True}}})
        frozen = policy.freeze(False)
        candidate = video("third", mid=5, title="【老E】片段", source="vertical_search", intent="special_related", entity_id="e", query="老E", special_mid="1")
        pool._save("vertical_search", [candidate])
        self.assertIn("vertical_search", rs.recommendation._mixed_sources("for_you", {**frozen["sources"], "_policy": frozen}))
        ranked = rs.recommendation.rank_sources(pool.all(("vertical_search",)), "for_you", "all", set(), None, policy=frozen)
        self.assertEqual(ranked["vertical_search"][0]["_controls"]["intent"], "third_party")
        self.assertEqual(len(mix(ranked, options={**frozen["sources"], "_policy": frozen, "_exposure_pages": []})), 1)
        policy.update_settings({"third_party": "off"})
        closed = policy.freeze(False)
        ranked = rs.recommendation.rank_sources(pool.all(("vertical_search",)), "for_you", "all", set(), None, policy=closed)
        self.assertFalse(ranked["vertical_search"])

    def test_v033_archive_done_head_preserves_cursor_and_daily_limit(self):
        record_history(video(source="favorite", fav_time=time.time()))
        db.set_state("sources:cursor:up_archive", {"hour": 0, "used": [], "1": {"page": 8, "order": "pubdate", "done": True, "last_head_at": time.time()-86401}})
        worker = SourceScheduler()
        with patch.object(bili, "up_archives", return_value={"items": [], "has_more": False}) as remote:
            worker.fetch("up_archive", {})
            worker.fetch("up_archive", {})
            remote.assert_called_once_with(1, 1, "pubdate")
        self.assertEqual(db.get_state("sources:cursor:up_archive")["1"]["page"], 8)

    def test_v033_search_ambiguous_requires_choice_and_empty_more(self):
        dictionary = self.dictionary()
        dictionary["entities"].append({"id": "second", "type": "character", "name": "其他", "aliases": ["老E"], "accounts": []})
        entities.update(dictionary)
        with patch.object(bili, "search") as remote:
            selected = entity_search.search("老E 游戏")
            remote.assert_not_called()
        self.assertEqual(len(selected["entity_choices"]), 2)
        with patch.object(bili, "search", return_value={"items": [video("same")], "num_pages": 3}):
            first = entity_search.search("老E 游戏", entity_id="e")
            second = entity_search.search("老E 游戏", entity_id="e", cursor=first["next_cursor"])
        self.assertFalse(second["items"])
        self.assertTrue(second["has_more"])

    def test_v033_cache_recheck_only_unseen_keeps_history_ids(self):
        self.fixture.save([video("first", mid=1), video("second", mid=2)])
        initial = rs.recommendation.refresh("for_you", view_id="cached-unseen")
        self.position("other1", "other", 0, time.time()-5, mid=1)
        self.position("other2", "other", 1, time.time()-4, mid=1)
        result = rs.recommendation.refresh("for_you", view_id="cached-unseen")
        self.assertNotIn("first", [item["bvid"] for item in result["items"]])
        self.assertEqual(next(item for item in initial["items"] if item["bvid"] == "second")["recommendation_id"], result["items"][0]["recommendation_id"])
        with db.connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM recommendation_history WHERE stream_id=?", (initial["stream_id"],)).fetchone()[0], 2)

    def test_v033_old_favorite_and_recent_partial_watch_not_seed(self):
        record_history(video(source="favorite", fav_time=time.time()-91*86400))
        record_history(video(source="history", progress=50, view_at=time.time()-5))
        self.assertFalse(affinity.seeds())

    def test_v033_new_author_cap_independent_of_old_exploration(self):
        profile.update_settings({"themes": {"测试": {"state": "fixed"}}})
        frozen = policy.freeze(True)
        ranked = rs.recommendation.rank_sources([video(str(i), mid=i+1) for i in range(8)], "for_you", "all", set(), fixtures.FakeBundle(), policy=frozen)
        items = mix(ranked, options={**frozen["sources"], "_policy": frozen, "_exposure_pages": []})
        self.assertEqual(len(items), 2)
        self.assertTrue(all(item["_controls"]["intent"] == "discovery" for item in items))

    def test_v033_regular_seed_retry_daily_and_failure_not_consumed(self):
        clock = time.time()
        record_history(video(source="favorite", fav_time=clock))
        worker = SourceScheduler()
        with patch.object(bili, "related", side_effect=BiliError(-1)):
            with self.assertRaises(BiliError):
                worker.expand_related()
        self.assertIsNone(db.get_state("sources:cursor:related"))
        with patch.object(bili, "related", return_value=[]) as remote:
            worker.expand_related()
            worker.expand_related()
            self.assertEqual(remote.call_count, 1)
        with patch("backend.services.source_scheduler.time.time", return_value=clock+86401), patch.object(bili, "related", return_value=[]) as remote:
            worker.expand_related()
            self.assertEqual(remote.call_count, 1)

    def test_v033_refill_progress_clears_early_return_backoff(self):
        worker = SourceScheduler()
        for i in range(30):
            record_history(video("fav-"+str(i), mid=i+1, source="favorite", fav_time=time.time()))
        self.fixture.save([video(str(i), mid=i+1, _detail_complete=False) for i in range(30)])
        with patch.object(worker, "start"):
            worker.request_refill("for_you", "all", 12, fixtures.settings(), "old")
        with patch.object(bili, "_detail_cache", {}), patch.object(bili, "detail", side_effect=lambda bvid: video(bvid, mid=int(bvid)+1)) as detail:
            worker._refill()
        self.assertEqual(detail.call_count, 12)
        self.assertTrue(all(not value.get("retry_at") for key, value in worker._demands.items() if key[0] != "idle"))

    def test_v033_expired_trial_history_does_not_block_restart(self):
        db.set_state("policy:trial", {"status": "running", "signature": "old", "model_version": "old", "started_at": 123})
        self.assertTrue(policy.trial_status()["historical"])
        with patch("backend.services.policy_review.report", return_value={"passed": True}):
            self.assertEqual(policy.trial_action("start")["status"], "running")
        self.assertEqual(db.get_state("policy:trial:history:123")["signature"], "old")

    def test_v033_settings_concurrent_partial_updates(self):
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=2) as workers:
            results = list(workers.map(policy.update_settings, [{"freshness": "strong"}, {"discovery": "off"}]))
        self.assertEqual(len(results), 2)
        self.assertEqual(policy.settings()["freshness"], "strong")
        self.assertEqual(policy.settings()["discovery"], "off")
        self.assertEqual(policy.settings()["third_party"], "small")
        with self.assertRaises(ValueError):
            policy.update_settings({"freshness": []})
        self.assertEqual(policy.settings()["freshness"], "strong")

    def test_v033_classic_freshness_preserves_source_and_unscored_slots(self):
        items = [dict(bvid="a", source="rcmd", rating=.8, freshness_score=.8), dict(bvid="b", source="hot", rating=.7, freshness_score=.7), dict(bvid="c", source="rcmd", rating=None, freshness_score=.08), dict(bvid="d", source="rcmd", rating=.79, freshness_score=.87)]
        ordered = rs.recommendation._freshness_order(items)
        self.assertEqual([v["bvid"] for v in ordered], ["d", "b", "c", "a"])
        self.assertEqual([v["source"] for v in ordered], [v["source"] for v in items])

    def test_v033_canonical_search_only_first_distinct_alias_and_expiry(self):
        dictionary = self.dictionary()
        dictionary["entities"][0]["aliases"] += ["E哥", "E总"]
        entities.update(dictionary)
        with patch.object(bili, "search", return_value={"items": [video()], "num_pages": 2}) as remote:
            first = entity_search.search("EdmundDZhang 游戏实况")
            self.assertEqual([call.args[0] for call in remote.call_args_list], ["EdmundDZhang 游戏实况", "老E 游戏实况"])
        dictionary = entities.snapshot()
        dictionary["entities"][0]["aliases"] = ["新别名"]
        entities.update(dictionary)
        with patch.object(bili, "search", return_value={"items": [video("next")], "num_pages": 2}) as remote:
            entity_search.search("EdmundDZhang 游戏实况", cursor=first["next_cursor"])
            self.assertEqual(remote.call_args_list[-1].args[0], "老E 游戏实况")
        with patch("backend.services.entity_search.time.time", return_value=time.time()+1801), self.assertRaises(ValueError):
            entity_search.search("EdmundDZhang 游戏实况", cursor=first["next_cursor"])

    def test_v033_diagnostics_keep_historical_entity_and_age(self):
        from backend.services.interest_analysis import diagnostics
        dictionary = self.dictionary()
        attribution = entities.identify(video(title="【老E】", mid=2), dictionary)
        self.position("historical", "history", 0, time.time(), mid=2, entity_attribution=attribution, _controls={"age_days": 45})
        entities.update({"version": dictionary["version"], "entities": []})
        result = diagnostics(7)
        self.assertEqual(result["exposed"], 1)
        self.assertEqual(result["repetition"]["subject"]["attributed"], 1)
        self.assertEqual(next(row for row in result["freshness"] if row["name"] == "31–90天")["exposed"], 1)

    def test_v033_cache_removes_newly_watched_without_rewriting_history(self):
        self.fixture.save([video("new")])
        first = rs.recommendation.refresh("for_you", view_id="watched-cache")
        self.assertEqual(len(first["items"]), 1)
        record_history(video("new", source="history", progress=50, view_at=time.time()))
        self.assertEqual(rs.recommendation.refresh("for_you", view_id="watched-cache")["items"], [])
        with db.connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM recommendation_history").fetchone()[0], 1)
