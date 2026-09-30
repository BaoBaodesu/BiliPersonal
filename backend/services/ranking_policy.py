"""独立策略实验：约十二分之一探索位，同 UP 软限制。"""


def diversify(ranked, limit, prior=()):
    remaining, result = list(ranked), []
    authors = list(prior)
    while remaining and len(result) < limit:
        recent = authors[-11:]
        eligible = [v for v in remaining if recent.count(str(v.get("mid") or v.get("author"))) < 2]
        # 候选不足时软降级，不硬丢弃唯一 UP 的内容。
        options = eligible or remaining
        if (len(authors)+1)%12 == 0:
            exploration = [v for v in options if not v.get("matched_tags")]
            item = exploration[0] if exploration else options[-1]
        else:
            item = options[0]
        result.append(item)
        authors.append(str(item.get("mid") or item.get("author")))
        remaining.remove(item)
    return result
