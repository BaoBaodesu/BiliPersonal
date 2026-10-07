"""盲评协议、事务边界与断点续评；使用临时库和假模型。"""
import copy
import json
import threading
import time
import unittest
from unittest.mock import patch

from backend.tests import test_v032 as fixtures
from backend.services import policy_review as review, recommendation_policy as policy
from backend.services.source_scheduler import scheduler
from backend.services.filter_service import filters
from backend.services.model_registry import registry

db, pool, video = fixtures.db, fixtures.pool, fixtures.video


class V034Test(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.V032Test()
        self.fixture.setUp()
        self.patches = [patch.object(review, "owner", return_value="42"), patch.object(scheduler, "start"), patch.object(scheduler, "enabled", False)]
        for p in self.patches:
            p.start()
        with db.connect() as conn:
            conn.executemany("INSERT INTO followings(mid,name,synced_at) VALUES(?,'UP',?)", [(str(i), time.time()) for i in range(1, 101)])

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.fixture.tearDown()

    def experiment(self):
        return review.create(dict(policy.DEFAULTS), "create")

    def seed(self, offset=0, count=12):
        pool._save("follow", [video(f"BV{offset+i}", mid=offset+i+1, source="follow", author=f"UP{offset+i+1}") for i in range(count)])

    def ready(self, offset=0):
        self.seed(offset)
        experiment = self.experiment()
        experiment = review.prepare(experiment["id"])
        self.assertEqual(experiment["status"], "batch_ready", experiment["preparation"])
        batch = review.get_batch(experiment["id"], experiment["batches"][-1]["id"])
        return experiment, batch

    def score(self, experiment, batch, value=2):
        for item in batch["items"]:
            review.rate(experiment["id"], batch["id"], item["blind_item_id"], value, item["revision"], item["blind_item_id"])

    def complete(self):
        experiment = self.experiment()
        for number in range(5):
            self.seed(number*12)
            with db.connect() as conn:
                conn.execute("UPDATE policy_review_batches SET frozen_at=frozen_at-3600 WHERE experiment_id=? AND status='sealed'", (experiment["id"],))
            current = review.prepare(experiment["id"])
            self.assertEqual(current["status"], "batch_ready", current["preparation"])
            batch = review.get_batch(experiment["id"], current["batches"][-1]["id"])
            # 合成两组覆盖证据验证报告/试用协议，不声称真实推荐获益。
            with db.connect() as conn:
                private = json.loads(conn.execute("SELECT private FROM policy_review_batches WHERE id=?", (batch["id"],)).fetchone()[0])
                for name, value in (("baseline", 0), ("strategy", 1)):
                    for evidence in private["evidence"][name]:
                        evidence["L"] = value
                conn.execute("UPDATE policy_review_batches SET private=? WHERE id=?", (review.encode(private), batch["id"]))
            self.score(experiment, batch)
            review.submit(experiment["id"], batch["id"])
        return review.detail(experiment["id"])

    def test_waiting_candidates_and_shared_top12(self):
        experiment = self.experiment()
        result = review.prepare(experiment["id"])
        self.assertEqual(result["preparation"]["status"], "waiting_candidates")
        self.seed(count=11)
        result = review.prepare(experiment["id"])
        self.assertEqual(result["preparation"]["groups"], {"baseline": 11, "strategy": 11})
        self.seed()
        result = review.prepare(experiment["id"])
        batch = review.get_batch(experiment["id"], result["batches"][-1]["id"])
        self.assertEqual(len(batch["items"]), 12)
        self.assertEqual(result["status"], "batch_ready")

    def test_blind_response_autosave_conflict_and_seal(self):
        experiment, batch = self.ready()
        encoded = json.dumps(batch)
        for forbidden in ("positions", "baseline", "strategy", "_policy", "rating\"", "source", "model_version", "tag\""):
            self.assertNotIn(forbidden, encoded)
        item = batch["items"][0]
        response = review.rate(experiment["id"], batch["id"], item["blind_item_id"], 2, 0, "save")
        self.assertEqual(review.rate(experiment["id"], batch["id"], item["blind_item_id"], 2, 0, "save"), response)
        with self.assertRaises(review.ReviewError):
            review.rate(experiment["id"], batch["id"], item["blind_item_id"], 3, 0, "stale")
        with self.assertRaises(review.ReviewError):
            review.submit(experiment["id"], batch["id"])
        self.score(experiment, review.get_batch(experiment["id"], batch["id"]))
        result = review.submit(experiment["id"], batch["id"])
        self.assertEqual(result["completed_batches"], 1)
        self.assertEqual(review.submit(experiment["id"], batch["id"])["completed_batches"], 1)
        with self.assertRaises(review.ReviewError):
            review.rate(experiment["id"], batch["id"], item["blind_item_id"], 3, 1, "late")
        self.assertNotIn("groups", review.report(experiment["id"]))

    def test_interval_novelty_and_cross_batch_new_scores(self):
        experiment, batch = self.ready()
        self.score(experiment, batch)
        review.submit(experiment["id"], batch["id"])
        self.assertEqual(review.prepare(experiment["id"])["preparation"]["status"], "waiting_change")
        with db.connect() as conn:
            conn.execute("UPDATE policy_review_batches SET frozen_at=frozen_at-3600 WHERE id=?", (batch["id"],))
        self.assertEqual(review.prepare(experiment["id"])["preparation"]["status"], "waiting_change")
        self.seed(6)
        result = review.prepare(experiment["id"])
        self.assertEqual(result["status"], "batch_ready")
        new = review.get_batch(experiment["id"], result["batches"][-1]["id"])
        self.assertTrue(all(item["score"] is None for item in new["items"]))
        self.assertTrue({v["video"]["bvid"] for v in batch["items"]} & {v["video"]["bvid"] for v in new["items"]})

    def test_hard_block_rebuild_preserves_draft(self):
        experiment, batch = self.ready()
        item = batch["items"][0]
        review.rate(experiment["id"], batch["id"], item["blind_item_id"], 3, 0, "save")
        filters.add_rule(item["video"]["title"])
        self.assertEqual(review.get_batch(experiment["id"], batch["id"])["status"], "invalidated")
        self.seed(20)
        result = review.prepare(experiment["id"])
        self.assertEqual(result["status"], "batch_ready")
        with db.connect() as conn:
            self.assertEqual(conn.execute("SELECT score FROM policy_review_ratings WHERE id=?", (item["blind_item_id"],)).fetchone()[0], 3)
        self.assertEqual(result["batches"][-1]["number"], 1)

    def test_owner_idempotency_and_abort_race(self):
        experiment = self.experiment()
        self.assertEqual(self.experiment()["id"], experiment["id"])
        with self.assertRaises(review.ReviewError):
            review.detail(experiment["id"], "other")
        with self.assertRaises(review.ReviewError):
            review.create(dict(policy.DEFAULTS), "different")
        self.seed()
        original = review._context
        def aborted(snapshot):
            result = original(snapshot)
            review.abort(experiment["id"], "取消")
            return result
        with patch.object(review, "_context", side_effect=aborted):
            self.assertEqual(review.prepare(experiment["id"])["status"], "aborted")
        self.assertFalse(review.detail(experiment["id"])["batches"])

    def test_capture_does_not_consume_feed_or_change_parameters(self):
        before = policy.settings()
        experiment, batch = self.ready()
        self.assertEqual(policy.settings(), before)
        with db.connect() as conn:
            for table in ("recommendation_history", "recommendation_exposures", "served_videos", "feed_cache"):
                self.assertEqual(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0], 0)
        with patch.object(review, "prepare", side_effect=AssertionError("轮询不得排序")):
            review.detail(experiment["id"])
            review.list_reviews()

    def test_report_five_original_guards_and_frozen_history(self):
        experiment = self.complete()
        report = review.report(experiment["id"])
        self.assertTrue(report["passed"])
        self.assertEqual(len(report["guards"]), 5)
        self.assertEqual(report["groups"]["baseline"]["top12"]["samples"], 60)
        self.assertEqual(report["groups"]["baseline"]["top4"]["samples"], 20)
        self.assertEqual(len(report["batches"]), 5)
        policy.update_settings({"freshness": "strong"})
        self.assertEqual(review.report(experiment["id"]), report)
        self.assertEqual(report["rated_occurrences"], 60)
        self.assertEqual(report["unique_bvids"], 60)

    def test_trial_confirmation_guard_idempotency_and_rollback(self):
        experiment = self.complete()
        policy.update_settings({"freshness": "strong"})
        previous = policy.settings()
        with self.assertRaises(review.ReviewError) as caught:
            review.start_trial(experiment["id"], "trial")
        self.assertEqual(caught.exception.code, "restore_confirmation")
        trial = review.start_trial(experiment["id"], "trial", True)
        self.assertEqual(trial["status"], "running")
        self.assertEqual(review.start_trial(experiment["id"], "trial", True)["id"], trial["id"])
        self.assertNotEqual(policy.settings(), previous)
        with self.assertRaises(review.ReviewError):
            policy.update_settings({"freshness": "weak"})
        with self.assertRaises(review.ReviewError):
            review.finish_trial("keep")
        with db.connect() as conn:
            value = review._state(conn, "policy:trial", {})
            value["started_at"] = time.time()-7*86400
            review._write_state(conn, "policy:trial", value)
        self.assertEqual(review.finish_trial("keep")["status"], "kept")
        review.finish_trial("stop")
        self.assertEqual(policy.settings(), previous)
        with db.connect() as conn:
            self.assertEqual(review._state(conn, "policy:trial:history:"+trial["id"], {})["status"], "stopped")

    def test_trial_different_dictionary_model_and_model_trial_rejected(self):
        experiment = self.complete()
        db.set_state("entities:dictionary", {"version": 1, "entities": []})
        with self.assertRaisesRegex(review.ReviewError, "词典"):
            review.start_trial(experiment["id"], "trial", True)
        db.set_state("entities:dictionary", {"version": 0, "entities": []})
        registry.update(active="new")
        with self.assertRaisesRegex(review.ReviewError, "模型"):
            review.start_trial(experiment["id"], "trial", True)
        registry.update(active="old", trial={"id": "model", "status": "running"})
        with self.assertRaisesRegex(review.ReviewError, "已有"):
            review.start_trial(experiment["id"], "trial", True)
        self.assertEqual(registry.state()["trial"]["id"], "model")

    def test_concurrent_freeze_publishes_once(self):
        self.seed()
        experiment = self.experiment()
        barrier = threading.Barrier(2)
        original = review._context
        errors = []
        def context(snapshot):
            result = original(snapshot)
            barrier.wait(timeout=20)
            return result
        def run():
            try:
                review.prepare(experiment["id"])
            except Exception as error:
                errors.append(error)
        with patch.object(review, "_context", side_effect=context):
            threads = [threading.Thread(target=run) for _ in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=30)
            self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertFalse(errors, errors)
        self.assertEqual(len(review.detail(experiment["id"])["batches"]), 1)

    def test_consistent_read_context_does_not_load_live_inputs_during_rank(self):
        self.seed()
        experiment = self.experiment()
        snapshot = None
        with db.connect() as conn:
            snapshot = json.loads(review._experiment(conn, experiment["id"], "42")["snapshot"])
        videos, context = review._context(snapshot)
        from backend.services.recommendation_service import recommendation
        # 提交复检需读取最新屏蔽；纯排序阶段不能重新读取规则。
        with patch.object(filters, "load_rules", side_effect=AssertionError("排序不得读实时规则")):
            recommendation.rank_sources(videos, "for_you", "all", set(), registry.load("old"), snapshot=context["affinity"], policy=snapshot["policy"], cooled=context["cooled"], context=context)

    def test_internal_failure_is_logged_and_retryable(self):
        experiment = self.experiment()
        with self.assertLogs(level="ERROR"), patch.object(review, "_context", side_effect=RuntimeError("fixture")):
            self.assertEqual(review.prepare(experiment["id"])["status"], "failed")
        self.seed()
        review.request_prepare(experiment["id"])
        self.assertEqual(review.prepare(experiment["id"])["status"], "batch_ready")

    def test_configuration_freeze_and_read_only_transaction(self):
        experiment = self.experiment()
        before = experiment["config"]
        policy.update_settings({"interest": "strong", "freshness": "weak"})
        self.assertEqual(review.detail(experiment["id"])["config"], before)
        with db.read_snapshot() as first:
            with db.connect() as second:
                self.assertIs(first, second)
                with self.assertRaises(Exception):
                    second.execute("INSERT INTO app_state VALUES('forbidden','0',0)")

    def test_debug_import_cannot_change_sealed_batch(self):
        experiment, batch = self.ready()
        path = review.export(experiment["id"])
        from pathlib import Path
        ratings = json.loads(Path(path).read_text(encoding="utf-8"))
        for row in ratings:
            row["score"] = 2
        Path(path).write_text(review.encode(ratings), encoding="utf-8")
        review.import_ratings(experiment["id"], path)
        review.submit(experiment["id"], batch["id"])
        with self.assertRaises(review.ReviewError):
            review.import_ratings(experiment["id"], path)

    def test_guard_boundaries_and_average_not_a_gate(self):
        baseline = {"top12": {"wanted": .8, "repelled": .1}, "top4": {"wanted": .75, "repelled": .1}, "long_coverage": .5, "average": 3}
        strategy = {"top12": {"wanted": .75, "repelled": .1}, "top4": {"wanted": .75, "repelled": .1}, "long_coverage": .51, "average": -1}
        groups = {"baseline": baseline, "strategy": strategy}
        self.assertTrue(all(v["passed"] for v in review.guard_report(groups)))
        for metric in ("top12.wanted", "top4.wanted", "top12.repelled", "top4.repelled", "long_coverage"):
            changed = copy.deepcopy(groups)
            if "." in metric:
                group, key = metric.split(".")
                changed["strategy"][group][key] += .001 if key == "repelled" else -.001
            else:
                changed["strategy"][metric] = .5
            self.assertFalse(next(v for v in review.guard_report(changed) if v["metric"] == metric)["passed"])

    def test_trial_metrics_require_visible_and_count_page_two_top4(self):
        started = time.time()-100
        trial = {"id": "test-trial", "protocol": review.PROTOCOL, "owner": "42", "status": "running", "started_at": started, "signature": policy.signature(policy.settings()), "model_version": "old", "settings": policy.settings(), "sources": __import__('backend.services.source_mixer', fromlist=['settings']).settings(), "dictionary": {"version": 0, "entities": []}, "execution": review.EXECUTION}
        db.set_state("policy:trial", trial)
        with db.connect() as conn:
            conn.execute("INSERT INTO feed_streams(stream_id,feed_type,category,view_id,model_version,page_limit,created_at) VALUES('metrics','for_you','all','metrics','old',12,?)", (time.time(),))
            for i, (page, rank, visible, clicked) in enumerate(((0, 1, False, 1), (1, 13, True, 1), (1, 17, True, 0))):
                snapshot = {"_policy": {"trial_id": "test-trial", "signature": trial["signature"]}}
                cursor = conn.execute("INSERT INTO recommendation_history(bvid,feed_type,title,author,pic,rank,stream_id,page,model_version,served_at,clicked,video_snapshot) VALUES(?,'for_you','title','UP','',?,'metrics',?,'old',?,?,?)", (str(i), rank, page, time.time(), clicked, json.dumps(snapshot)))
                conn.execute("INSERT INTO recommendation_exposures(id,recommendation_id,view_id,received_at,visible_at,acted_at) VALUES(?,?,'metrics',?,?,?)", (str(i), cursor.lastrowid, time.time(), time.time() if visible else None, time.time()))
        metrics = policy.trial_status()["metrics"]
        self.assertEqual(metrics["exposed"], 2)
        self.assertEqual(metrics["clicks"], 1)
        self.assertEqual(metrics["ctr_proxy"], .5)
        self.assertEqual(metrics["top4_exposed"], 1)
        self.assertEqual(metrics["top4_ctr"], 1)

    def test_new_block_between_calculation_and_publish_prevents_batch(self):
        self.seed()
        experiment = self.experiment()
        original = review._context
        def changed(snapshot):
            videos, context = original(snapshot)
            filters.add_rule("BV0")
            return videos, context
        with patch.object(review, "_context", side_effect=changed):
            result = review.prepare(experiment["id"])
        self.assertEqual(result["status"], "preparing")
        self.assertFalse(result["batches"])

    def test_pause_preserves_ratings_owner_and_prevents_stale_publish(self):
        experiment, batch = self.ready()
        self.score(experiment, batch)
        review.pause("退出登录，采集暂停")
        self.assertEqual(review.detail(experiment["id"])["preparation"]["status"], "paused")
        self.assertEqual(review.get_batch(experiment["id"], batch["id"])["items"][0]["score"], 2)
        with patch.object(review, "owner", return_value="43"):
            self.assertIsNone(review.list_reviews()["current"])
            review.background()
        review.submit(experiment["id"], batch["id"])
        self.seed(20)
        with db.connect() as conn:
            conn.execute("UPDATE policy_review_batches SET frozen_at=frozen_at-3600 WHERE id=?", (batch["id"],))
        original = review._context
        def paused(snapshot):
            result = original(snapshot)
            review.pause("登录失效，采集暂停")
            return result
        with patch.object(review, "_context", side_effect=paused):
            result = review.prepare(experiment["id"])
        self.assertEqual(len(result["batches"]), 1)
        review.request_prepare(experiment["id"])
        review.background()
        self.assertEqual(len(review.detail(experiment["id"])["batches"]), 2)

    def test_refill_uses_remaining_budget_and_respects_sources_off(self):
        from backend.services import request_coordination as budgets
        from backend.services.source_mixer import update_settings
        update_settings({"hot": "off", "rcmd": "off", "vertical_search": "off"})
        self.experiment()
        calls = []
        def expand(source, **kwargs):
            value = {**budgets.current(), "url": "https://fixture/recall"}
            budgets.check(value)
            budgets.consume(value)
            calls.append(source)
        with budgets.scope(background=True, total=1, details=12, recalls=3, candidate=True), patch.object(pool, "expand", side_effect=expand):
            with self.assertRaises(budgets.BudgetEnded):
                scheduler._blind_refill(None)
        self.assertEqual(len(calls), 1)
        self.assertNotIn(calls[0], ("hot", "rcmd", "vertical_search"))


if __name__ == "__main__":
    unittest.main()
