"""python -m backend.tools.policy_review capture|report；仅本地候选，不产生 HTTP。"""
import argparse
import json
from backend.services.policy_review import capture, report

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("capture", "report"))
    args = parser.parse_args()
    print(json.dumps(capture() if args.action == "capture" else report(), ensure_ascii=False, indent=2))
