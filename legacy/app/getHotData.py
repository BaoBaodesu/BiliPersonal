import requests
import json
import time

# len为需要读取的信息量（有可能会小于len,因为可能总共的信息就不多）

# 兼容性修复：热门榜单接口在短时间内连续请求时会返回 -352（B 站风控/瞬时限流），
# 属于外部接口可用性问题而非推荐逻辑问题，这里做有限次数重试（默认 2 次，间隔 3 秒）。
# 不改变任何取数结构与推荐逻辑。
HOT_RETRY_TIMES = 2
HOT_RETRY_INTERVAL = 3


def _get_json_with_retry(url, headers, params=None):
    """
    带有限重试的 GET，返回解析后的 JSON；全部失败返回 None。
    仅重试瞬时限流（-352）与网络异常，成功或业务错误立即返回。
    """
    for attempt in range(1, HOT_RETRY_TIMES + 1):
        try:
            response = requests.get(url, headers=headers, params=params, timeout=15)
        except requests.exceptions.RequestException as e:
            print(f"热门接口请求异常（第 {attempt} 次）：{type(e).__name__}，{url}")
            if attempt < HOT_RETRY_TIMES:
                time.sleep(HOT_RETRY_INTERVAL)
                continue
            return None
        if response.status_code != 200:
            print(f"热门接口请求失败（第 {attempt} 次），状态码：{response.status_code}，{url}")
            if attempt < HOT_RETRY_TIMES:
                time.sleep(HOT_RETRY_INTERVAL)
                continue
            return None
        try:
            data = response.json()
        except ValueError:
            print(f"热门接口返回非 JSON（第 {attempt} 次），{url}")
            if attempt < HOT_RETRY_TIMES:
                time.sleep(HOT_RETRY_INTERVAL)
                continue
            return None
        if data.get("code") == -352 and attempt < HOT_RETRY_TIMES:
            print(f"热门接口触发风控 code=-352（第 {attempt} 次），{HOT_RETRY_INTERVAL} 秒后重试")
            time.sleep(HOT_RETRY_INTERVAL)
            continue
        return data
    return None


def get_hot_data(cookie, len):
    # 目标 URL
    url = "https://api.bilibili.com/x/web-interface/popular/series/list"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
        "Cookie": cookie,
    }
    videos_info = _get_json_with_retry(url, headers)
    if videos_info is None:
        return None
    if videos_info["code"] != 0:
        print(f"热门榜单期数接口返回 code={videos_info['code']} message={videos_info.get('message', '')}")
        return None
    # 获取最新一期的数据，page即为期数
    page = videos_info["data"]["list"][0]["number"]
    # res为最终的结果
    res = []
    count = 1

    while count <= len:
        url = "https://api.bilibili.com/x/web-interface/popular/series/one?number="
        str_page = str(page)
        url += str_page
        print(url)
        # 解析 JSON 数据
        videos_info = _get_json_with_retry(url, headers)
        if videos_info is None:
            return None
        if videos_info["code"] != 0:
            print(f"热门榜单接口返回 code={videos_info['code']} message={videos_info.get('message', '')}")
            return None

        for video_info in videos_info["data"]["list"]:
            sigle_res = {}
            sigle_res["bvid"] = video_info["bvid"]
            sigle_res["title"] = video_info["title"]
            sigle_res["pic"] = video_info["pic"]
            sigle_res["author"] = video_info["owner"]["name"]
            sigle_res["view"] = video_info["stat"]["view"]
            sigle_res["like"] = video_info["stat"]["like"]
            sigle_res["favorite"] = video_info["stat"]["favorite"]
            sigle_res["coin"] = video_info["stat"]["coin"]
            sigle_res["share"] = video_info["stat"]["share"]
            sigle_res["duration"] = video_info["duration"]
            # tag比价麻烦，需要单独去获取详细信息
            url_2 = (
                "https://api.bilibili.com/x/web-interface/view/detail?bvid="
                + video_info["bvid"]
            )
            response = requests.get(url_2, headers=headers)
            if response.status_code == 200:
                video_detail = response.json()
                if video_detail["code"] != 0:
                    print(
                        f"视频详情接口返回 code={video_detail['code']} message={video_detail.get('message', '')}，{url_2}"
                    )
                    return None
            else:
                print(f"请求失败，状态码：{response.status_code}，{url_2}")
                return None
            # print(video_detail['data']['Tags'])
            sigle_res["tag"] = [tag["tag_name"] for tag in video_detail["data"]["Tags"]]

            res.append(sigle_res)
            print(video_info["bvid"], count)
            count += 1
            if count > len:
                break
        page -= 1
        if page == 0:
            break
        with open("hotVideo.json", "w", encoding="utf-8") as json_file:
            # 使用 json.dump() 将字典写入文件
            json.dump(
                res, json_file, indent=4, ensure_ascii=False
            )  # indent=4 用来让输出格式更易读

    return res
