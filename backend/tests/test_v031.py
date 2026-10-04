"""固定候选与假 B 站接口；所有写入在测试临时目录内。"""
import itertools
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from backend.storage import database as db
from backend.services import filter_service as fs
with patch("backend.config.COOKIE_PATH", str(Path(__file__).parent / "missing-cookie")):
    from backend.services.bilibili_service import bili, BiliError, RateLimited
from backend.services import recommendation_service as rs
from backend.services.affinity import affinity
from backend.services.cache_service import pool, SOURCES
from backend.services.feedback_service import feedback, FeedbackError
from backend.services.model_registry import registry, atomic_json
from backend.services.source_mixer import mix, quotas, settings, update_settings, source_report
from backend.services.source_scheduler import SourceScheduler
from backend.services.training_data import record_history, PROTOCOL
from backend.services.blind_review import approve_activate, capture
from backend.config import FOLLOW_WINDOW


def video(bvid="BV1", **changes):
    return {"bvid": bvid, "title": bvid, "mid": 1, "author": "测试UP", "tag": ["测试"], "tname": "科技",
            "view": 10000, "like": 100, "coin": 50, "favorite": 50, "duration": 100, "pubdate": time.time(),
            "source": "related", "_detail_complete": True, **changes}


class FakeBundle:
    policy = "base"
    def known_tags(self, tags):
        return tags
    def score(self, videos):
        return [(v, float(v.get("test_rating", .7)), v.get("tag", [])) for v in videos if v.get("view") is not None]


class V031Test(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        root = Path(self.tmp.name)
        self.patches = [patch.object(db, "DB_PATH", str(root / "test.db")), patch.object(db, "DATA_DIR", str(root)),
                        patch.object(fs, "DB_PATH", str(root / "test.db")), patch.object(registry, "root", root / "models"),
                        patch.object(registry, "_loaded", {"old": FakeBundle(), "new": FakeBundle()}),
                        patch.object(bili, "_cookie", "fixture"), patch.object(bili, "rate_limited_until", 0),
                        patch.object(rs.recommendation, "prefetch"), patch.object(pool, "expand", return_value=0),
                        patch.object(bili, "follow_feed", return_value={"items": [], "offset": "", "has_more": False}),
                        patch("backend.services.evaluation_service.evaluation.predict", return_value={})]
        for p in self.patches:
            p.start()
        db.init_db()
        fs.filters.invalidate()
        registry.update(active="old", anchor=None, trial=None, evaluation=None)

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def pools(self, count=20):
        return {source: [video(f"{source}-{i}", mid=i + index * 100, source=source, stranger=False) for i in range(count)] for index, source in enumerate(SOURCES)}

    def following(self, mid):
        with db.connect() as conn:
            conn.execute("INSERT OR REPLACE INTO followings(mid,name,synced_at) VALUES(?, 'UP', ?)", (mid, time.time()))

    def save(self, videos):
        for source in {v["source"] for v in videos}:
            pool._save(source, [v for v in videos if v["source"] == source])

    def position(self, bvid, source="related", served_at=None, clicked=0):
        served_at = time.time() if served_at is None else served_at
        with db.connect() as conn:
            conn.execute("INSERT INTO served_videos VALUES(?, 'for_you', ?, ?, 1) ON CONFLICT(bvid,feed_type) DO UPDATE SET times=times+1,last_served_at=excluded.last_served_at", (bvid, served_at, served_at))
            row = conn.execute("INSERT INTO recommendation_history(bvid,feed_type,source,served_at,clicked,view_id,video_snapshot) VALUES(?,'for_you',?,?,?,'view',?)", (bvid, source, served_at, clicked, json.dumps(video(bvid, source=source))))
        return row.lastrowid

    def test_default_quota_and_native_share(self):
        items = mix(self.pools())
        self.assertEqual({s: sum(v["source"] == s for v in items) for s in SOURCES}, {"hot": 0, "rcmd": 1, "follow": 4, "up_archive": 3, "related": 4, "vertical_search": 0})
        self.assertLessEqual(sum(v["source"] in ("hot", "rcmd") for v in items) / len(items), .15)

    def test_all_level_combinations_and_largest_remainder(self):
        for hot, rcmd in itertools.product(("off", "fallback", "small", "standard"), repeat=2):
            options = {"hot": hot, "rcmd": rcmd, "classic": False}
            self.assertEqual(sum(quotas(12, options).values()), 12)
            items = mix(self.pools(), options=options)
            self.assertEqual(len(items), 12)
            for source, level in (("hot", hot), ("rcmd", rcmd)):
                self.assertEqual(sum(v["source"] == source for v in items), {"off": 0, "fallback": 0, "small": 1, "standard": 3}[level])

    def test_deficits_and_fallback_order(self):
        pools = self.pools()
        pools["follow"] = pools["up_archive"] = []
        items = mix(pools)
        self.assertEqual(sum(v["source"] == "related" for v in items), 11)
        pools["related"] = []
        options = {"hot": "fallback", "rcmd": "fallback", "classic": False}
        self.assertTrue(all(v["source"] == "rcmd" for v in mix(pools, options=options)))
        options["hot"] = options["rcmd"] = "off"
        self.assertEqual(mix(pools, options=options), [])

    def test_limits_and_cross_source_deduplication(self):
        pools = self.pools()
        pools["follow"] = [video(f"f{i}", source="follow", mid=1) for i in range(8)]
        pools["related"] = [video(f"r{i}", mid=100 + i, stranger=True) for i in range(10)]
        pools["related"].insert(0, video("f0", mid=1, stranger=True))
        items = mix(pools)
        self.assertEqual(len({v["bvid"] for v in items}), len(items))
        self.assertLessEqual(sum(v["mid"] == 1 for v in items), 2)
        self.assertLessEqual(sum(v.get("stranger", False) and v["source"] == "related" for v in items), 3)

    def test_downrank_one_and_last(self):
        pools = self.pools()
        for source in pools:
            for v in pools[source][:2]:
                v["downrank"] = True
        items = mix(pools)
        self.assertEqual(sum(v.get("downrank", False) for v in items), 1)
        self.assertTrue(items[-1]["downrank"])

    def test_single_completion_familiar_and_distinct_regular(self):
        record_history(video(source="history", progress=-1))
        self.assertEqual(affinity.snapshot()["ups"]["1"]["level"], "familiar")
        record_history(video(source="history", progress=100, view_at=time.time()))
        self.assertEqual(affinity.snapshot()["ups"]["1"]["watched_count"], 1)
        for bvid in ("BV2", "BV3"):
            record_history(video(bvid, source="history", progress=50))
        self.assertEqual(affinity.snapshot()["ups"]["1"]["level"], "regular")

    def test_favorite_is_independent_and_can_be_recommended(self):
        record_history(video(source="favorite", isfaved=1))
        snapshot = affinity.snapshot()
        self.assertIn("BV1", snapshot["favorites"])
        self.assertNotIn("BV1", snapshot["watched"])
        self.assertEqual(snapshot["ups"]["1"]["level"], "regular")
        self.save([video()])
        result = rs.recommendation.refresh("for_you", view_id="favorite")
        self.assertEqual([v["bvid"] for v in result["items"]], ["BV1"])
        record_history(video(source="history", progress=1))
        self.assertEqual(rs.recommendation.refresh("for_you", view_id="watched")["items"], [])

    def test_public_favorite_count_does_not_mean_user_favorite(self):
        record_history(video(source="history", progress=1, favorite=10000))
        self.assertEqual(affinity.snapshot()["favorites"], set())
        self.assertEqual(affinity.snapshot()["ups"]["1"]["level"], "stranger")

    def test_explicit_watched_undo_preserves_real_history(self):
        feedback.record("BV1", "watched", video())
        self.assertIn("BV1", affinity.snapshot()["watched"])
        feedback.record("BV1", "undo")
        self.assertNotIn("BV1", affinity.snapshot()["watched"])
        record_history(video(source="history", progress=1))
        feedback.record("BV1", "watched", video())
        feedback.record("BV1", "undo")
        self.assertIn("BV1", affinity.snapshot()["watched"])

    def test_follow_30_day_boundary_and_timeline(self):
        clock = time.time()
        self.following(1)
        candidates = [video("boundary", source="follow", pubdate=clock-FOLLOW_WINDOW), video("expired", source="follow", pubdate=clock-FOLLOW_WINDOW-1)]
        with patch("backend.services.recommendation_service.time.time", return_value=clock):
            self.assertEqual([v["bvid"] for v in rs.recommendation.rank_sources(candidates, "for_you", "all", set(), None)["follow"]], ["boundary"])
            self.assertEqual(len(rs.recommendation.rank_sources(candidates, "following", "all", set(), None)["follow"]), 2)

    def test_familiar_does_not_recall_old_archives(self):
        record_history(video(source="history", progress=-1))
        self.assertEqual(rs.recommendation.rank_sources([video("old", source="up_archive")], "for_you", "all", set(), None)["up_archive"], [])
        record_history(video(source="favorite"))
        self.assertEqual(len(rs.recommendation.rank_sources([video("old", source="up_archive")], "for_you", "all", set(), None)["up_archive"]), 1)

    def test_quality_gate_and_familiar_exemption(self):
        candidates = [video("low", view=999), video("bad", mid=2, like=0, coin=0, favorite=0)] + [video(str(i), mid=i+20) for i in range(20)]
        kept = affinity.quality_gate(candidates)
        self.assertNotIn("low", {v["bvid"] for v in kept})
        self.assertNotIn("bad", {v["bvid"] for v in kept})
        record_history(video(source="history", progress=-1))
        self.assertIn("low", {v["bvid"] for v in affinity.quality_gate(candidates)})

    def test_native_24h_and_two_misses_permanent(self):
        self.position("one", "hot")
        self.position("two", "rcmd", time.time()-3*86400)
        self.position("two", "rcmd", time.time()-2*86400)
        self.position("clicked", "hot", time.time()-2*86400, clicked=1)
        cooled = rs.recommendation._cooled_bvids()
        self.assertIn(("one", "hot"), cooled)
        self.assertIn(("two", "rcmd"), cooled)
        self.assertNotIn(("clicked", "hot"), cooled)

    def test_related_two_misses_30_day_reset(self):
        clock = time.time()
        self.position("BV1", served_at=clock-62*86400)
        self.position("BV1", served_at=clock-31*86400)
        self.assertNotIn(("BV1", "related"), rs.recommendation._cooled_bvids())
        self.position("BV1", served_at=clock-1801)
        self.assertNotIn(("BV1", "related"), rs.recommendation._cooled_bvids())
        self.position("BV1", served_at=clock+1)
        self.assertIn(("BV1", "related"), rs.recommendation._cooled_bvids())

    def test_prefilter_does_not_request_blocked_details(self):
        fs.filters.add_blocked_up("测试UP", 1)
        self.save([video(_detail_complete=False), video("BV2", mid=2, author="其他UP", _detail_complete=False)])
        with patch.object(bili, "detail", return_value=video("BV2", mid=2, author="其他UP")) as detail:
            self.assertEqual(pool.complete(("related",), 12), 1)
            detail.assert_called_once_with("BV2")

    def test_tag_zone_and_downrank_rules(self):
        fs.filters.add_rule("测试", target_type="tag")
        self.assertEqual(fs.filters.filter_candidates([video()])[0], [])
        fs.filters.add_rule("生活", target_type="zone")
        self.assertEqual(fs.filters.filter_candidates([video(tag=[], tname="生活")])[0], [])
        fs.filters.add_rule("科技", target_type="zone", action="downrank")
        self.assertTrue(fs.filters.filter_candidates([video(tag=[])])[0][0]["downrank"])

    def test_feedback_reason_penalties_and_revocation(self):
        for reason in ("uploader", "topic", "clickbait"):
            feedback.record("BV1", "not_interested", video(), {"reason": reason})
        penalties = affinity.penalties()
        self.assertEqual(sum(v["kind"] == "up" for v in penalties), 2)
        self.assertEqual(sum(v["kind"] == "tag" for v in penalties), 1)
        self.assertEqual(sum(v["kind"] == "expand_up" for v in penalties), 3)
        feedback.record("BV1", "undo")
        self.assertEqual(len(affinity.penalties()), 4)
        for event in feedback.history():
            feedback.remove(event["id"])
        self.assertEqual(affinity.penalties(), [])
        with self.assertRaises(FeedbackError):
            feedback.record("BV1", "not_interested", context={"reason": "unknown"})

    def test_seeds_category_blocked_penalized_and_used(self):
        for i in range(6):
            record_history(video(str(i), mid=i+10, source="favorite", tname="科技" if i < 4 else "生活"))
        fs.filters.add_blocked_up("测试UP", 10)
        feedback.record("1", "not_interested", video("1", mid=11), {"reason": "uploader"})
        seeds = affinity.seeds("科技", used=["2"])
        self.assertEqual([v["bvid"] for v in seeds], ["3"])

    def test_completed_details_only_and_bounded_budget(self):
        self.save([video(str(i), mid=i+10, _detail_complete=False) for i in range(30)])
        with patch.object(bili, "detail", side_effect=lambda bvid: video(bvid, mid=int(bvid)+10)) as detail:
            result = rs.recommendation.refresh("for_you", view_id="budget")
        self.assertEqual(detail.call_count, 0)
        self.assertLessEqual(len(result["items"]), 12)
        self.assertTrue(all(v["bvid"] in {args[0][0] for args in detail.call_args_list} for v in result["items"]))

    def test_empty_off_sources_and_settings_validation(self):
        update_settings({"hot": "off", "rcmd": "off"})
        self.save([video("hot", source="hot")])
        result = rs.recommendation.refresh("for_you", view_id="empty")
        self.assertEqual(result["items"], [])
        self.assertEqual(result["notice"], "accumulating")
        with self.assertRaises(ValueError): update_settings({"hot": "bogus"})
        with self.assertRaises(ValueError): update_settings({"classic": 1})

    def test_stream_settings_immutable_after_switch(self):
        self.save([video(str(i), mid=i+1, source="related") for i in range(30)])
        first = rs.recommendation.refresh("for_you", view_id="immutable")
        update_settings({"classic": True, "hot": "off", "rcmd": "off"})
        self.assertEqual(first, rs.recommendation.get_feed("for_you", view_id="immutable"))
        nextpage = rs.recommendation.get_feed("for_you", cursor=first["next_cursor"])
        self.assertTrue(nextpage["items"])
        self.assertTrue(all(v["source"] == "related" for v in nextpage["items"]))

    def test_following_time_order_no_cooldown_no_trial(self):
        registry.update(trial={"id": "trial", "current": "old", "candidate": "new", "status": "running"})
        self.save([video(str(i), source="follow", pubdate=time.time()-i*86400) for i in range(40)])
        first = rs.recommendation.refresh("following", view_id="following")
        second = rs.recommendation.refresh("following", view_id="following2")
        self.assertEqual([v["bvid"] for v in first["items"]], [v["bvid"] for v in second["items"]])
        self.assertTrue(all(v["rating"] is None for v in first["items"]))
        self.assertEqual([v["pubdate"] for v in first["items"]], sorted((v["pubdate"] for v in first["items"]), reverse=True))
        with db.connect() as conn:
            self.assertIsNone(conn.execute("SELECT experiment_id FROM feed_streams WHERE view_id='following'").fetchone()[0])

    def test_rate_limited_uses_local_candidates(self):
        self.save([video("local")])
        with patch.object(bili, "rate_limited_until", time.time()+90):
            result = rs.recommendation.refresh("for_you", view_id="limited")
        self.assertEqual([v["bvid"] for v in result["items"]], ["local"])
        self.assertEqual(result["notice"], "rate_limited")

    def test_scheduler_only_regular_and_hourly_budget(self):
        scheduler = SourceScheduler()
        for i in range(4):
            record_history(video(str(i), mid=i+1, source="favorite"))
        record_history(video("familiar", mid=10, source="history", progress=-1))
        self.following(3)
        fs.filters.add_blocked_up("测试UP", 2)
        with patch.object(bili, "up_archives", return_value={"items": [], "has_more": True}) as fetch:
            scheduler.fetch("up_archive", {})
            scheduler.fetch("up_archive", {})
        self.assertEqual(fetch.call_count, 2)
        self.assertEqual(fetch.call_args_list[0].args[0], 3)
        self.assertNotIn(10, [call.args[0] for call in fetch.call_args_list])

    def test_scheduler_stop_clears_only_source_state(self):
        scheduler = SourceScheduler()
        self.following(1)
        db.set_state("sources:cursor:related", {"used": ["BV1"]})
        self.save([video("new"), video("hot", source="hot")])
        scheduler.stop()
        self.assertEqual(affinity.snapshot()["followings"], set())
        self.assertIsNone(db.get_state("sources:cursor:related"))
        self.assertEqual(pool.count("related"), 0)
        self.assertEqual(pool.count("hot"), 1)

    def test_report_rates_deduplicate_feedback(self):
        rid = self.position("BV1")
        with db.connect() as conn:
            conn.execute("INSERT INTO recommendation_exposures(id,recommendation_id,view_id,visible_at,received_at) VALUES('exposure',?,'view',?,?)", (rid,time.time(),time.time()))
        context = {"recommendation_id": rid, "view_id": "view", "exposure_id": "exposure", "event_id": "negative"}
        feedback.record("BV1", "not_interested", context=context)
        feedback.record("BV1", "not_interested", context=context)
        report = source_report()
        self.assertEqual(report["sources"][0]["not_interested_rate"], 1)

    def test_sparse_refetch_preserves_complete_details(self):
        self.save([video(seed_bvid="seed")])
        self.save([video(_detail_complete=False, tag=[], like=0)])
        self.assertTrue(pool.get("BV1")["_detail_complete"])
        self.assertEqual(pool.get("BV1")["tag"], ["测试"])
        with patch.object(bili, "detail") as detail:
            pool.complete(("related",))
            detail.assert_not_called()

    def test_source_settings_api_and_unauthorized(self):
        from backend.app import create_app
        client = create_app(start_background=False).test_client()
        self.assertEqual(client.get("/api/v1/system/sources").status_code, 200)
        self.assertEqual(client.put("/api/v1/system/sources", json={"hot": "off"}).get_json()["settings"]["hot"], "off")
        self.assertEqual(client.put("/api/v1/system/sources", json={"classic": 1}).status_code, 400)
        with patch.object(bili, "_cookie", ""):
            self.assertEqual(client.get("/api/v1/system/sources").status_code, 401)

    def test_archive_fallback_after_rate_limit(self):
        with patch.object(bili, "_archive_fallback_until", {}), patch.object(bili, "get", side_effect=[RateLimited(-352), {"archives": [video("archive")], "page": {"total": 1}}]) as get:
            with self.assertRaises(RateLimited):
                bili.up_archives(1)
            result = bili.up_archives(1)
            self.assertTrue(result["fallback"])
            self.assertEqual(result["items"][0]["bvid"], "archive")
            self.assertIn("recArchivesByKeywords", get.call_args.args[0])

    def test_fallback_missing_owner_still_prefilters_uploader(self):
        record_history(video(source="favorite"))
        fs.filters.add_rule("测试UP", target_type="uploader")
        with patch.object(bili, "_archive_fallback_until", {"1": time.time()+3600}), patch.object(bili, "get", return_value={"archives": [{"bvid": "archive", "title": "title", "upMid": 1, "stat": {"view": 1000}}], "page": {"total": 1}}), patch.object(bili, "detail") as detail:
            self.assertEqual(bili.up_archives(1)["items"], [])
            detail.assert_not_called()

    def test_related_seed_budget_and_category(self):
        for i in range(8):
            record_history(video(str(i), mid=i+1, source="favorite", tname="科技" if i < 6 else "生活"))
        scheduler = SourceScheduler()
        with patch.object(bili, "related", side_effect=lambda bvid: [video("related-"+bvid, seed_bvid=bvid)]) as get:
            items = scheduler.expand_related("科技")
        self.assertEqual(len(items), 3)
        self.assertEqual(get.call_count, 3)
        self.assertTrue(all(int(v["seed_bvid"]) < 6 for v in items))

    def test_blind_capture_freezes_all_sources_and_unique_positions(self):
        candidates = self.pools(12)
        for v in candidates["follow"]:
            self.following(v["mid"])
        for v in candidates["up_archive"]:
            record_history(video("favorite-"+v["bvid"], mid=v["mid"], source="favorite"))
        self.save([v for values in candidates.values() for v in values])
        registry.update(evaluation={"id": "capture", "candidate": "new", "current": "old"})
        capture()
        document = json.loads((registry.root / "blind" / "capture" / "private.json").read_text(encoding="utf-8"))
        batch = document["batches"][0]
        self.assertEqual({v["source"] for v in batch["candidates"]}, set(SOURCES)-{"vertical_search"})
        self.assertEqual(len(set(batch["positions"]["new"])), 10)
        self.assertEqual(len({v["bvid"] for v in batch["videos"]}), len(batch["videos"]))
        with self.assertRaises(ValueError):
            capture()

    def test_approve_activate_requires_confirm_and_five_batches(self):
        with self.assertRaises(ValueError): approve_activate("new")
        registry.update(evaluation={"id": "pair", "candidate": "new", "current": "old"})
        path = registry.root / "blind" / "pair"
        atomic_json(path / "private.json", {"candidate": "new", "current": "old", "batches": []})
        atomic_json(path / "rating.json", [])
        with self.assertRaises(ValueError): approve_activate("new", True)
        document = {"candidate": "new", "current": "old", "batches": [{"frozen_at": i, "sources_settings": settings(), "candidates": [], "positions": {"new": [f"BV{n}" for n in range(10)], "old": [f"BV{n}" for n in range(10)]}, "videos": [{"bvid": f"BV{n}"} for n in range(10)]} for i in range(5)]}
        atomic_json(path / "private.json", document)
        atomic_json(path / "rating.json", [{"bvid": f"BV{n}", "score": 2} for n in range(10)])
        with self.assertRaises(ValueError): approve_activate("other", True)
        with patch.object(registry, "metadata", return_value={"kind": "v03", "protocol": PROTOCOL}):
            approve_activate("new", True)
        self.assertEqual(registry.state()["anchor"], "new")
        self.assertEqual(registry.state()["approved_protocol"], PROTOCOL)
        with self.assertRaises(ValueError): approve_activate("new", True)


if __name__ == "__main__":
    unittest.main()
