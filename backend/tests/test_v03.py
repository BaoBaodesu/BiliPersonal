"""固定样本、临时数据库；不读取账号或调用真实 B 站接口。"""
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

# 在创建服务单例前替换 Cookie 路径，验收进程不读取真实账号。
with patch("backend.config.COOKIE_PATH", str(Path(__file__).parent/"missing-cookie")):
    from backend.services.bilibili_service import bili

from backend.storage import database as db
from backend.services import filter_service as fs
from backend.services.model_registry import registry, ModelRegistry, atomic_json, digest
from backend.services.training_data import label, record_history, build_samples, temporal_split, fingerprint
from backend.services.feedback_service import feedback
from backend.services.exposure_service import record_exposures, unclicked_summary
from backend.services.evaluation_service import evaluation, compare
from backend.services.experiment_service import experiments, product_report


def video(bvid="BV1", **changes):
    return {"bvid": bvid, "title": bvid, "author": "测试UP", "mid": 10, "tag": ["测试"],
            "view": 1000, "like": 20, "favorite": 10, "duration": 100, "source": "history", **changes}


class FakeBundle:
    policy = "base"
    def known_tags(self, tags):
        return [t for t in tags if t == "测试"]
    def score(self, videos):
        return [(v, .7, self.known_tags(v.get("tag", []))) for v in videos]


class V03Test(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        root = Path(self.tmp.name)
        self.patches = [patch.object(db,"DB_PATH",str(root/"test.db")), patch.object(db,"DATA_DIR",str(root)),
                        patch.object(fs,"DB_PATH",str(root/"test.db")), patch.object(registry,"root",root/"models"),
                        patch.object(registry,"_loaded", {"old": FakeBundle(), "new": FakeBundle(), "anchor": FakeBundle()})]
        for p in self.patches: p.start()
        db.init_db()
        # v0.3 回归仍验证经典路径，混合来源的默认行为由 test_v031 单独覆盖。
        db.set_state("sources:settings", {"hot": "fallback", "rcmd": "small", "classic": True})
        fs.filters.invalidate()
        registry.update(active="old",anchor=None,trial=None,evaluation=None,pending=None,auto_paused=False)

    def tearDown(self):
        for p in reversed(self.patches): p.stop()
        self.tmp.cleanup()

    def position(self, bvid="BV1", **changes):
        context = {"view_id":"view", "model_version":"old", "experiment_id":None, "arm":None, **changes}
        with db.connect() as conn:
            r=conn.execute("INSERT INTO recommendation_history(bvid,feed_type,title,author,pic,rank,model_version,served_at,view_id,experiment_id,arm,video_snapshot,predictions) VALUES(?,'for_you',?,'UP','',1,?,?,?,?,?, ?,?)",
                           (bvid,bvid,context["model_version"],time.time(),context["view_id"],context["experiment_id"],context["arm"],json.dumps(video(bvid)),json.dumps({"old":.5,"new":.6})))
        return {"recommendation_id":r.lastrowid,"view_id":context["view_id"],"exposure_id":f'{context["view_id"]}:{r.lastrowid}'}

    def test_label_precedence(self):
        self.assertEqual(label(video(source="favorite",progress=None,isfaved=1)),(1,2.,"interaction"))
        self.assertEqual(label(video(progress=1,isliked=1)),(1,2.,"interaction"))
        self.assertEqual(label(video(progress=90),["not_interested","like"]),(0,2.,"not_interested"))
        self.assertEqual(label(video(progress=1),["click"]),(0,.25,"low_progress"))
        self.assertEqual(label(video(progress=-1)),(1,1.,"completed"))
        self.assertEqual(label(video(progress=50)),(1,.5,"watched_half"))
        self.assertIsNone(label(video(progress=30),["click"]))
        self.assertIsNone(label(video(),["block_up","watched","watch_later"]))
        self.assertEqual(label(video(),["click","click"]),(1,.25,"click"))
        self.assertIsNone(label(video(coin=10000)))

    def test_history_merge_and_time(self):
        record_history(video(progress=1,view_at=100),observed_at=1000)
        record_history(video(source="favorite",isfaved=1,fav_time=200),observed_at=1001)
        samples=build_samples(2000)
        self.assertEqual(len(samples),1)
        self.assertEqual(samples[0]["label"],1)
        self.assertEqual(samples[0]["weight"],2)
        self.assertEqual(samples[0]["video"]["progress"],1)
        record_history(video("BV2",source="favorite",isfaved=1,pubdate=10),observed_at=1002)
        self.assertIsNone(build_samples(2000)[1]["event_at"])
        with db.connect() as conn:
            self.assertIsNone(conn.execute("SELECT progress FROM history_events WHERE bvid='BV2'").fetchone()[0])

    def test_cross_device_update_and_frozen_snapshot(self):
        record_history(video(progress=1,view_at=100),observed_at=1000)
        frozen=build_samples(1000)
        record_history(video(progress=95,view_at=200),observed_at=2000)
        self.assertEqual(frozen[0]["label"],0)
        self.assertEqual(build_samples(3000)[0]["label"],1)
        self.assertEqual(build_samples(1000),frozen)

    def test_time_split(self):
        samples=[{"bvid":str(i),"event_at":i+1,"observed_at":100} for i in reversed(range(10))]
        train,val=temporal_split(samples)
        self.assertEqual(len(train),8)
        self.assertLess(train[-1]["event_at"],val[0]["event_at"])
        with self.assertRaises(ValueError): temporal_split(samples+[samples[0]])

    def test_click_idempotency_and_position(self):
        one,two=self.position(),self.position(view_id="view2")
        context={**one,"event_id":"same"}
        feedback.record("BV1","click",context=context)
        feedback.record("BV1","click",context=context)
        with db.connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM feedback").fetchone()[0],1)
            self.assertEqual(conn.execute("SELECT clicked FROM recommendation_history WHERE id=?",(two["recommendation_id"],)).fetchone()[0],0)
            self.assertIsNone(conn.execute("SELECT visible_at FROM recommendation_exposures").fetchone()[0])
        self.assertEqual(len(build_samples()),1)
        self.assertEqual(build_samples()[0]["weight"],.25)
        self.assertEqual(fingerprint(build_samples()),fingerprint(build_samples()))

    def test_exposure_retry_threshold_and_transaction(self):
        context=self.position()
        item={**context,"id":context["exposure_id"],"visible_at":time.time(),"visible_ratio":.5,"duration_ms":1000}
        record_exposures([item]);record_exposures([item])
        with db.connect() as conn: self.assertEqual(conn.execute("SELECT COUNT(*) FROM recommendation_exposures").fetchone()[0],1)
        with self.assertRaises(ValueError):record_exposures([{**item,"duration_ms":999}])
        with self.assertRaises(ValueError):record_exposures([{**item,"visible_ratio":.49}])
        with self.assertRaises(ValueError):record_exposures([{**item,"view_id":"other","id":item["id"]}])

    def test_24h_observation_not_negative(self):
        for view in ("v1","v2"):
            context=self.position(view_id=view)
            record_exposures([{**context,"id":context["exposure_id"],"visible_at":time.time()-86401,"visible_ratio":1,"duration_ms":1000}])
        self.assertEqual(len(unclicked_summary()),1)
        self.assertEqual(build_samples(),[])

    def test_block_and_undo_invalidate(self):
        self.assertEqual(len(fs.filters.filter_candidates([video()])[0]),1)
        feedback.record("BV1","block_up",video())
        self.assertEqual(fs.filters.filter_candidates([video()])[0],[])
        feedback.record("BV1","undo",video())
        self.assertEqual(len(fs.filters.filter_candidates([video()])[0]),1)
        self.assertEqual(build_samples(),[])

    def test_stream_fixed_and_unknown_records(self):
        from backend.services import recommendation_service as rs
        service=rs.RecommendationService()
        class Pool:
            def all(self,sources):return [video(f'BV{i}',mid=i+1,tag=["未知"]) for i in range(15)]
            def expand(self,*args,**kwargs):pass
        with patch.object(rs,"pool",Pool()), patch.object(service,"_background"), patch.object(evaluation,"predict",return_value={}):
            first=service.get_feed("for_you",limit=4,view_id="first")
            registry.update(active="new")
            again=service.get_feed("for_you",limit=4,view_id="first")
            nextpage=service.get_feed("for_you",cursor=first["next_cursor"],limit=12)
            self.assertEqual(first,again)
            self.assertEqual(nextpage["model_version"],"old")
            self.assertEqual(len(nextpage["items"]),4)
            self.assertFalse(set(v["bvid"] for v in first["items"]) & set(v["bvid"] for v in nextpage["items"]))
            newer=service.get_feed("for_you",limit=4,view_id="newview")
            self.assertEqual(newer["model_version"],"new")
            with db.connect() as conn:self.assertEqual(conn.execute("SELECT COUNT(*) FROM recommendation_history").fetchone()[0],12)
            feedback.record(first["items"][0]["bvid"],"block_up",context={"recommendation_id":first["items"][0]["recommendation_id"],"view_id":"first","exposure_id":"block","event_id":"block"})
            self.assertNotIn(first["items"][0]["mid"], {v["mid"] for v in service.get_feed("for_you",limit=4,view_id="first")["items"]})

    def test_fallback_records_and_warmup(self):
        from backend.services import recommendation_service as rs
        service=rs.RecommendationService()
        registry.update(active=None)
        class Pool:
            def all(self,sources):return [video(f'BV{i}') for i in range(5)]
            def expand(self,*args,**kwargs):pass
        with patch.object(rs,"pool",Pool()),patch.object(service,"_background"),patch.object(evaluation,"predict",return_value={}):
            page=service.get_feed("hot",limit=4,view_id="fallback")
            self.assertEqual(page["model_version"],"fallback-v03")
            self.assertTrue(all(v["rating"] is None for v in page["items"]))
            with db.connect() as conn:self.assertEqual(conn.execute("SELECT COUNT(*) FROM recommendation_history").fetchone()[0],4)

    def test_queue_latest_and_no_training_leak(self):
        fake=lambda v:{"data_cutoff_at":100,"train_bvids":["trained"],"validation_bvids":["validated"]}
        with patch.object(registry,"metadata",side_effect=fake):
            evaluation.start("new")
            frozen=registry.state()["evaluation"]
            evaluation.start("later1");evaluation.start("later2")
            self.assertEqual(registry.state()["evaluation"],frozen)
            self.assertEqual(registry.state()["pending"],"later2")
            self.assertIn("trained",frozen["excluded"])
            self.assertIn("validated",frozen["excluded"])
            self.assertEqual(evaluation.check()["status"],"waiting")

    def test_first_trial_requires_blind_confirmation_and_balance(self):
        registry.update(evaluation={"id":"pair","current":"old","candidate":"new","versions":["old","new"],"status":"waiting"})
        with self.assertRaises(ValueError):experiments.start()
        trial=experiments.start({"candidate":"new","batches":5,"complete":True})
        with db.connect() as conn:
            one=experiments.context("for_you","all","v1")
            conn.execute("INSERT INTO feed_streams VALUES('s1','v1','for_you','all',12,?,?,?,?)",(one["model_version"],one["experiment_id"],one["arm"],time.time()))
        two=experiments.context("for_you","all","v2")
        self.assertNotEqual(one["arm"],two["arm"])
        self.assertIsNone(experiments.context("hot","all","v3")["arm"])
        self.assertIsNone(experiments.context("for_you","测试","v4")["arm"])
        self.assertEqual(experiments.check(trial["started_at"]+86400)["status"],"waiting")
        stopped=experiments.stop()
        self.assertEqual(stopped["status"],"stopped")
        self.assertEqual(registry.state()["active"],"old")

    def test_paired_ap_anchor_and_relative_loss(self):
        samples=[{"bvid":str(i),"label":i%2,"weight":2 if i%2 else .25,"evidence":"fixture",
                  "predictions":{"old":.8 if i%2 else .2,"new":.8 if i%2 else .2,"anchor":.8 if i%2 else .2}} for i in range(100)]
        result=compare(samples,["old","new","anchor"],"new","old","anchor")
        self.assertEqual(result["status"],"passed")
        self.assertEqual(result["resamples"],10000)
        self.assertEqual(result["delta_AP_lower_95_one_sided"]["anchor"],0)
        for sample in samples:sample["predictions"]["new"]=.6 if sample["label"] else .4
        with patch("backend.services.evaluation_service.BOOTSTRAPS",100):
            self.assertEqual(compare(samples,["old","new","anchor"],"new","old","anchor")["status"],"rejected")
        self.assertEqual(compare(samples[:10],["old","new"],"new","old",None)["status"],"waiting")

    def test_anchor_failure_and_exact_relative_boundary(self):
        samples=[{"bvid":str(i),"label":i%2,"weight":1,"evidence":"fixture","predictions":{"old":.8 if i%2 else .2,"new":.8 if i%2 else .2,"anchor":.8 if i%2 else .2}} for i in range(100)]
        with patch("backend.services.evaluation_service.BOOTSTRAPS",100):
            for exponent,expected in ((1.01999,"passed"),(1.02001,"rejected")):
                for s in samples:s["predictions"]["new"]=(.8**exponent if s["label"] else 1-.8**exponent)
                self.assertEqual(compare(samples,["old","new","anchor"],"new","old","anchor")["status"],expected)
            for i,s in enumerate(samples):
                s["predictions"]["old"] = .8 if s["label"] else (.9 if i<40 else .2)
                s["predictions"]["new"] = .8 if s["label"] else (.9 if i<20 else .2)
            result=compare(samples,["old","new","anchor"],"new","old","anchor")
            self.assertGreater(result["metrics"]["new"]["AP"],result["metrics"]["old"]["AP"])
            self.assertLess(result["delta_AP_lower_95_one_sided"]["anchor"],-.01)
            self.assertEqual(result["status"],"rejected")

    def test_product_zero_feedback_and_limits(self):
        rows=[{"arm":a,"view_id":f'{a}{i//25}',"bvid":str(i),"not_interested":0,"block_up":0} for a in ("current","candidate") for i in range(500)]
        report=product_report(rows)
        self.assertEqual(report["status"],"passed")
        self.assertIn("不足",report["intervals"]["block_up"]["evidence"])
        rows[-1]["not_interested"]=1
        self.assertEqual(product_report(rows)["status"],"rejected")
        self.assertEqual(product_report(rows[:500])["status"],"waiting")

    def test_registry_checksum_and_rollback(self):
        local=ModelRegistry(Path(self.tmp.name)/"registry")
        path=local.directory("fixture")
        path.mkdir(parents=True)
        (path/"weights").write_bytes(b'original')
        atomic_json(path/"bundle.json",{"version":"fixture","checksums":{"weights":digest(path/"weights")}})
        self.assertEqual(local.metadata("fixture")["version"],"fixture")
        (path/"weights").write_bytes(b'changed')
        with self.assertRaises(ValueError):local.metadata("fixture")
        with self.assertRaises(ValueError):local.directory("../other")

    def test_diversity_soft_degrade(self):
        from backend.services.ranking_policy import diversify
        videos=[video(str(i),mid=i%6,matched_tags=["测试"]) for i in range(24)]
        mixed=diversify(videos,12)
        self.assertEqual(len(mixed),12)
        self.assertEqual(len({v["bvid"] for v in mixed}),12)
        same=[video(str(i)) for i in range(24)]
        self.assertEqual(len(diversify(same,12)),12)

    def test_sync_checkpoint_rate_limit_daily_incremental(self):
        from backend.services import history_sync as hs
        from backend.services.bilibili_service import RateLimited
        class Bili:
            rate_limited=False
            fail=True
            progress=1
            isliked=0
            calls=[]
            def watch_history(self,**kwargs):return {"items":[video("BV1",progress=self.progress,view_at=100),video("BV2",progress=1,view_at=200)],"cursor":{"max":1,"view_at":200},"has_more":False}
            def detail(self,bvid):
                self.calls.append(bvid)
                if bvid=="BV2" and self.fail:raise RateLimited(-352,"fixture")
                return video(bvid,aid=123)
            def get(self,*args,**kwargs):return self.isliked
            def fav_folders(self):return []
        fake=Bili()
        with patch.object(hs,"bili",fake),patch.object(hs,"HISTORY_PATH",str(Path(self.tmp.name)/"missing.json")),patch.object(hs.time,"sleep"):
            with self.assertRaises(RateLimited):hs.sync.run(page_budget=1)
            self.assertEqual(db.get_state("v03_history_sync")["history_bvids"],["BV1"])
            fake.fail=False
            hs.sync.run(page_budget=2)
            self.assertTrue(db.get_state("v03_history_sync")["complete"])
            self.assertEqual(fake.calls,["BV1","BV2","BV2"])
            hs.sync.run()
            self.assertEqual(fake.calls,["BV1","BV2","BV2"])
            fake.progress=95
            fake.isliked=1
            hs.sync.run(force=True,page_budget=2)
            self.assertEqual(fake.calls,["BV1","BV2","BV2"])
            self.assertEqual(build_samples()[0]["label"],1)
            self.assertEqual(build_samples()[0]["weight"],2)

    def test_undo_retains_product_action_and_is_idempotent(self):
        context=self.position(experiment_id="trial",arm="candidate")
        feedback.record("BV1","not_interested",context={**context,"event_id":"negative"})
        feedback.record("BV1","not_interested",context={**context,"event_id":"negative2"})
        feedback.record("BV1","undo",context={"event_id":"undo"})
        feedback.record("BV1","undo",context={"event_id":"undo"})
        with db.connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM feedback WHERE action='not_interested'").fetchone()[0],2)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM feedback WHERE action='not_interested' AND revoked_at IS NULL").fetchone()[0],1)
        rows,_=experiments.rows({"id":"trial","started_at":0})
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]["not_interested"],1)
        self.assertEqual(build_samples()[0]["weight"],2)

    def test_exact_mid_and_filter_removal(self):
        feedback.record("BV1","block_up",video())
        self.assertEqual(len(fs.filters.filter_candidates([video("BV2",mid=11)])[0]),1)
        with db.connect() as conn:up_id=conn.execute("SELECT id FROM blocked_ups").fetchone()[0]
        fs.filters.delete_blocked_up(up_id)
        self.assertEqual(len(fs.filters.filter_candidates([video()])[0]),1)
        with db.connect() as conn:self.assertIsNotNone(conn.execute("SELECT revoked_at FROM feedback WHERE action='block_up'").fetchone()[0])

    def test_future_time_and_exclusions(self):
        pair={"cutoff":100,"frozen_at":200,"excluded":["trained"],"versions":["old","new"]}
        for bvid,event_at,served_at in (("past",190,210),("future",240,220),("trained",240,220),("late_prediction",210,220)):
            context=self.position(bvid)
            with db.connect() as conn:conn.execute("UPDATE recommendation_history SET served_at=? WHERE id=?",(served_at,context["recommendation_id"]))
            record_history(video(bvid,progress=95,view_at=event_at),observed_at=300)
        context=self.position("middle")
        feedback.record("middle","click",context={**context,"event_id":"middle-click"})
        record_history(video("middle",progress=30,view_at=time.time()))
        self.assertEqual([s["bvid"] for s in evaluation.collect(pair)],["future"])

    def test_fixed_settlement_once_and_atomic_promotion(self):
        registry.update(anchor="anchor",evaluation={"id":"pair","current":"old","candidate":"new","versions":["old","new","anchor"],"status":"passed"})
        trial=experiments.start()
        rows=[{"arm":a,"view_id":f'{a}{i//25}',"bvid":str(i),"not_interested":0,"block_up":0} for a in ("current","candidate") for i in range(500)]
        from backend.services.training_service import training
        with patch.object(experiments,"rows",return_value=(rows,3)),patch.object(registry,"metadata",return_value={}),patch.object(training,"_load_active"),patch.object(evaluation,"finish") as finish:
            report=experiments.check(trial["started_at"]+8*86400)
            self.assertEqual(report["status"],"passed")
            self.assertEqual(report["unattributed_feedback"],3)
            self.assertEqual(registry.state()["active"],"new")
            self.assertEqual(registry.state()["anchor"],"anchor")
            registry.update(evaluation=None)
            self.assertIsNone(experiments.check(trial["started_at"]+9*86400))
            self.assertEqual(finish.call_count,1)

    def test_error_artifact_reject_and_no_false_ready(self):
        from backend.services.training_service import TrainingService
        service=TrainingService()
        registry.update(previous="new",baseline="new")
        with patch.object(registry,"capture_baseline"),patch.object(registry,"metadata",return_value={}),patch.object(registry,"load",side_effect=lambda v: (_ for _ in ()).throw(ValueError("bad")) if v=="old" else FakeBundle()):
            service._load_active()
            self.assertEqual(service.model_version,"new")
            self.assertEqual(registry.state()["active"],"new")
            self.assertTrue(registry.state()["auto_paused"])
        registry.update(active="old",previous=None,baseline=None)
        with patch.object(registry,"capture_baseline"),patch.object(registry,"load",side_effect=ValueError("bad")):
            service=TrainingService();service._load_active()
            self.assertEqual(service.stage,"error")
            self.assertIsNone(service.recommender)
            self.assertEqual(registry.state()["active"],"fallback-v03")

    def test_logout_login_restarts_cancelled_worker(self):
        import threading
        from backend.services.training_service import TrainingService
        service=TrainingService()
        first,second=threading.Event(),threading.Event()
        calls=[]
        def cycle(*args):
            calls.append(1)
            (first if len(calls)==1 else second).set()
            service._stop.wait(2)
        with patch("backend.services.training_service.bili._cookie", "fixture"),patch.object(service,"_load_active"),patch.object(service,"_cycle",side_effect=cycle):
            service.ensure_started()
            self.assertTrue(first.wait(2))
            service.reset()
            service.resume_session()
            service.ensure_started()
            self.assertTrue(second.wait(2))
            service.reset()
            service._thread.join(2)
            self.assertFalse(service._thread.is_alive())

    def test_forward_sealed_labels_and_next_round_exclusion(self):
        from backend.services.training_data import PROTOCOL
        pair={"id":"sealed","cutoff":1,"frozen_at":1,"candidate":"new","current":"old","anchor":None,"excluded":[],"versions":["old","new"],"status":"waiting","protocol":PROTOCOL}
        registry.update(evaluation=pair)
        with patch.object(registry,"metadata",return_value={}),patch.object(evaluation,"collect",return_value=[{"bvid":"sealed-bv","label":0}]),patch("backend.services.evaluation_service.compare",return_value={"status":"passed"}):
            evaluation.check()
        path=registry.root/"evaluations"/"sealed"/"snapshot.json"
        original=path.read_bytes()
        record_history(video("sealed-bv",progress=95,view_at=time.time()))
        evaluation.check()
        self.assertEqual(path.read_bytes(),original)
        self.assertIn("sealed-bv",registry.state()["consumed_bvids"])
        registry.update(pending="newer")
        with patch.object(registry,"metadata",return_value={}):evaluation.finish()
        self.assertIn("sealed-bv",registry.state()["evaluation"]["excluded"])

    def test_concurrent_rejection_cannot_be_overwritten(self):
        from backend.services.training_data import PROTOCOL
        pair={"id":"race","cutoff":1,"frozen_at":1,"candidate":"new","current":"old","anchor":None,"excluded":[],"versions":["old","new"],"status":"waiting","protocol":PROTOCOL}
        registry.update(evaluation=pair)
        def compare_with_error(*args):
            registry.update(evaluation={**pair,"status":"rejected"},auto_paused=True)
            return {"status":"passed"}
        with patch.object(registry,"metadata",return_value={}),patch.object(evaluation,"collect",return_value=[]),patch("backend.services.evaluation_service.compare",side_effect=compare_with_error):
            self.assertEqual(evaluation.check()["status"],"rejected")
        self.assertFalse((registry.root/"evaluations"/"race"/"snapshot.json").exists())
        self.assertTrue(registry.state()["auto_paused"])

    def test_stop_during_settlement_cannot_promote(self):
        registry.update(anchor="anchor",evaluation={"id":"pair","current":"old","candidate":"new","versions":["old","new","anchor"],"status":"passed"})
        trial=experiments.start()
        rows=[{"arm":a,"view_id":f'{a}{i//25}',"bvid":str(i),"not_interested":0,"block_up":0} for a in ("current","candidate") for i in range(500)]
        def report_and_stop(*args):
            experiments.stop("异常提前停止")
            return {"status":"passed"}
        with patch.object(experiments,"rows",return_value=(rows,0)),patch("backend.services.experiment_service.product_report",side_effect=report_and_stop):
            self.assertEqual(experiments.check(trial["started_at"]+8*86400)["status"],"stopped")
        self.assertEqual(registry.state()["active"],"old")

    def test_flask_login_feed_retry_exposure_and_spa(self):
        from backend.app import create_app
        from backend.services import recommendation_service as rs
        class Pool:
            def all(self,sources):return [video(f'BV{i}') for i in range(8)]
            def expand(self,*args,**kwargs):return 0
        app=create_app(start_background=False)
        client=app.test_client()
        self.assertEqual(client.get('/api/v1/feed').status_code,401)
        with patch.object(bili,"_cookie","fixture"),patch.object(rs,"pool",Pool()),patch.object(rs.recommendation,"_background"),patch.object(evaluation,"predict",return_value={}):
            one=client.get('/api/v1/feed?view_id=retry&limit=4').get_json()
            two=client.get('/api/v1/feed?view_id=retry&limit=4').get_json()
            self.assertEqual(one,two)
            item=one["items"][0]
            payload={"bvid":item["bvid"],"action":"click","recommendation_id":item["recommendation_id"],"view_id":item["view_id"],"exposure_id":"quick","event_id":"retryclick"}
            self.assertEqual(client.post('/api/v1/feedback',json=payload).status_code,200)
            self.assertEqual(client.post('/api/v1/feedback',json=payload).status_code,200)
            self.assertEqual(client.post('/api/v1/feed/exposures',json={"items":[{"id":"quick","recommendation_id":item["recommendation_id"],"view_id":item["view_id"],"visible_at":time.time(),"visible_ratio":.5,"duration_ms":1000}]}).status_code,200)
            self.assertEqual(client.get('/api/v1/feed?cursor=invalid').status_code,400)
            self.assertEqual(client.post('/api/v1/feed/refresh',json={"view_id":"refresh","limit":4}).status_code,200)
        self.assertEqual(client.get('/api/v1/system/status').status_code,200)
        self.assertEqual(client.get('/history').status_code,200)


class NetworkTest(unittest.TestCase):
    def test_train_best_reload_unknown_and_quality(self):
        from backend.recommender.ranker_v03 import Ranker, Processor
        import numpy as np
        samples=[{"bvid":f'BV{i}',"video":video(f'BV{i}',tag=["测试"] if i<32 else ["只在验证集"],mid=10 if i<32 else 999),
                  "label":i%2,"weight":2 if i%2 else .25,"evidence":"fixture","event_at":i+1,"observed_at":100} for i in range(40)]
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            local=ModelRegistry(directory)
            bundle,meta=Ranker.train(samples,local,100,epochs=3)
            history=json.loads((local.directory(meta["version"])/"training.json").read_text())
            self.assertAlmostEqual(meta["metrics"]["loss"],min(r["loss"] for r in history),places=6)
            self.assertNotIn("只在验证集",bundle.processor.tag2idx)
            self.assertNotIn("999",bundle.processor.author2idx)
            self.assertEqual(len(bundle.score([video(tag=[]),video("unknown",tag=["全新"],mid=999)])),2)
            self.assertTrue(np.allclose(bundle.model.tag_embedding.embedding_matrix.numpy()[:2],0))
            loaded=local.load(meta["version"])
            self.assertAlmostEqual(bundle.score([video()])[0][1],loaded.score([video()])[0][1],places=7)
            processor=Processor.fit(samples[:32],quality=True)
            self.assertEqual(processor.transform([video()])[2].shape,(1,3))
            quality,_=Ranker.train(samples,local,100,quality=True,epochs=2)
            self.assertEqual(len(quality.score([video(tag=["未知"])])),1)

    def test_single_class_validation_unavailable(self):
        from backend.recommender.ranker_v03 import metrics, Ranker
        self.assertIsNone(metrics([1,1],[.8,.9],[1,2])["AP"])
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            samples=[{"bvid":str(i),"event_at":i,"observed_at":100,"label":1} for i in range(40)]
            with self.assertRaises(ValueError):Ranker.train(samples,ModelRegistry(directory),100,epochs=1)


if __name__ == "__main__":
    unittest.main()
