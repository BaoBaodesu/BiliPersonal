"""策略盲评调试CLI，正常流程请使用设置内的策略盲评页面。"""
import argparse
import json
from backend.storage.database import init_db
from backend.services import policy_review as review


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("status", "capture", "report", "export", "import"))
    parser.add_argument("--experiment")
    parser.add_argument("--owner", required=True, help="明确指定调试账号UID")
    parser.add_argument("--file")
    args = parser.parse_args()
    init_db()
    if args.command == "status":
        result = review.list_reviews(args.owner)
    elif args.command == "capture":
        result = review.capture(args.experiment, args.owner)
    elif not args.experiment:
        parser.error("此操作需要--experiment")
    elif args.command == "report":
        result = review.report(args.experiment, args.owner)
    elif args.command == "export":
        result = review.export(args.experiment, args.owner)
    elif not args.file:
        parser.error("导入需要--file")
    else:
        result = review.import_ratings(args.experiment, args.file, args.owner)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
