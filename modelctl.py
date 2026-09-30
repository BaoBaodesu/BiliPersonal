"""本地工件、冻结评价及人工准入命令；切换/训练前请停止服务。"""
import argparse
import json

from backend.services.model_registry import registry


def main():
    parser = argparse.ArgumentParser(description="BiliPersonal v0.3 模型管理")
    parser.add_argument("command", choices=["list", "switch", "rollback", "train", "evaluate", "finish", "blind-capture", "blind-report", "approve-trial", "stop-trial", "observe"])
    parser.add_argument("version", nargs="?")
    parser.add_argument("--quality", action="store_true", help="独立 Quality 实验，默认关闭")
    parser.add_argument("--diversity", action="store_true", help="独立排序策略实验，默认关闭")
    args = parser.parse_args()
    if args.command not in ("list", "observe"):
        import socket
        with socket.socket() as client:
            client.settimeout(.5)
            if client.connect_ex(("127.0.0.1", 8345)) == 0:
                raise ValueError("请先按 Ctrl+C 停止服务，再执行模型管理命令")
    from backend.storage.database import init_db
    init_db()
    registry.capture_baseline()
    if args.command == "list":
        result = {"state": registry.state(), "versions": []}
        from backend.storage.database import connect
        with connect() as conn:
            result["runs"] = [dict(r) for r in conn.execute("SELECT model_version,status,protocol,data_cutoff_at,started_at,finished_at,artifact_path,metrics FROM model_runs ORDER BY started_at DESC")]
        for path in sorted((registry.root / "versions").glob("*/bundle.json")):
            try:
                result["versions"].append(registry.metadata(path.parent.name))
            except Exception as error:
                result["versions"].append({"version": path.parent.name, "error": str(error)})
    elif args.command in ("switch", "rollback"):
        version = args.version or registry.state().get("previous")
        if not version:
            raise ValueError("需要指定已保存的版本")
        state = registry.state()
        if version not in (state.get("baseline"), state.get("previous"), state.get("anchor"), state.get("active")):
            from backend.storage.database import connect
            with connect() as conn:
                row = conn.execute("SELECT status FROM model_runs WHERE model_version=?", (version,)).fetchone()
            if not row or row[0] != "approved":
                raise ValueError("未批准候选不能跳过盲评及护栏直接切换")
        registry.load(version)
        if state.get("trial"):
            from backend.services.experiment_service import experiments
            experiments.stop("人工切换模型")
        registry.activate(version)
        registry.update(evaluation=None, auto_paused=True)
        result = registry.state()
    elif args.command == "train":
        if args.quality and args.diversity:
            raise ValueError("Quality 和排序策略必须分别实验")
        import time
        from backend.services.training_data import build_samples, PROTOCOL
        from backend.recommender.ranker_v03 import Ranker
        from backend.services.evaluation_service import evaluation
        from backend.storage.database import connect
        cutoff = time.time()
        _, result = Ranker.train(build_samples(cutoff), registry, cutoff, quality=args.quality, policy="diversity" if args.diversity else "base")
        with connect() as conn:
            conn.execute("INSERT INTO model_runs(model_version,protocol,status,data_cutoff_at,started_at,finished_at,data_hash,artifact_path,metrics,config) VALUES(?,?,?,?,?,?,?,?,?,?)", (result["version"], PROTOCOL,"candidate",cutoff,cutoff,time.time(),result["data_hash"],str(registry.directory(result["version"])),json.dumps(result["metrics"]),json.dumps(result)))
        evaluation.start(result["version"])
    elif args.command in ("evaluate", "finish"):
        from backend.services.evaluation_service import evaluation
        result = evaluation.check() if args.command == "evaluate" else evaluation.finish()
    elif args.command.startswith("blind-") or args.command == "approve-trial":
        from backend.services.blind_review import capture, report
        if args.command == "blind-capture":
            result = str(capture())
        elif args.command == "blind-report":
            result = report()
        else:
            from backend.services.experiment_service import experiments
            result = experiments.start(report())
    elif args.command == "stop-trial":
        from backend.services.experiment_service import experiments
        result = experiments.stop()
    else:
        from backend.services.exposure_service import unclicked_summary
        result = unclicked_summary()
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        raise SystemExit(str(error))
