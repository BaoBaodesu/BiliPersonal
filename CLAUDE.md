# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

基于 bilibili 网页 API 的个性化视频推荐系统：扫码登录 → 抓取收藏/历史 → 生成 `historyVideo.json` → 现场训练 Wide&Deep + Attention 模型 → 对热门/探索候选池重新排序并展示。上游为 tLLWtG/bilibili-Recommender，本地修改在分支上进行，不向上游提交。

## 常用命令

- 环境：项目使用独立虚拟环境 `.venv`（README 指定 Python 3.9.20；本机实际用 uv 创建的 3.11，见 `requirements.win-test.txt` 头部的三步安装说明）。原始 `requirements.txt` 在 Windows 上因 `tensorflow_intel` 与 `numpy==2.2.0` 元数据冲突无法一次装完，需用 `requirements.win-test.txt` 的方式安装。
- 启动：`python run.py`，监听 `http://127.0.0.1:8345`（保持 127.0.0.1，不要改为 0.0.0.0）。v0.2 后端路径均基于 `backend/config.py` 的绝对路径；前端 `cd frontend && npm run build`（产物由 Flask 托管）或 `npm run dev`（5173，代理 /api）。
- 旧版 Jinja 实现已移至 `legacy/`（`python legacy/run_legacy.py`，需在项目根目录运行），以下“架构”一节描述的是旧版。
- 无测试框架、无 lint 配置。`testAPI.py` 仅为手动接口调试脚本；`python app/Recommender.py` 可用 `user_data/cookie.txt` 单独跑一遍训练+推荐；`app/model.py` 的 `main()` / `testmodel()` 用于离线训练与评估。

## 架构

路由不使用装饰器：`app/app.py` 中的视图函数是普通函数（`# @app.route` 已注释），由 `app/__init__.py` 的 `create_app()` 通过 `add_url_rule` 统一注册。新增路由要在两处同时改。

`app/app.py` 用模块级全局变量（`cookie_str`、`cookie_data`、`recommender`）保存登录态和模型单例；`logout` 必须把它们全部清空，否则重新登录后 `recommender` 仍持有旧 Cookie。

数据流：
1. `app.py` 二维码登录（generate/poll）→ Cookie 写入 `user_data/cookie.txt`。
2. `dashboard` → `getHistoryData.get_history_data(cookie, n)`：收藏（`get_fav_data`）+ 点赞（`get_vote_data`）+ 历史，逐视频补充 tag/详情，写出 `historyVideo.json`。字段结构（bvid/title/pic/author/view/like/favorite/coin/share/duration/progress/tag/isfaved/isliked）是推荐模型的输入契约，改 API 层时必须保持。
3. 首次请求推荐接口时才创建 `Recommender(cookie_str)`：在 `__init__` 中读取 `historyVideo.json`，用 `model.py` 的 `FeatureProcessor` 构建 tag/author 索引，同步训练 30 个 epoch（会阻塞请求），并把权重和 `feature_processor.pkl` 存入 `saved_model/`。
4. `Recommender.recommend(pool_type, n)`：候选池不足时调用 `getHotData.get_hot_data`（热门）或 `getRecommandData.get_recommand_data`（探索）扩充；**与训练集 `tag2idx` 没有交集 tag 的候选会被直接丢弃**，候选池为空多半源于此；其余按模型预测值（rating）排序取前 n。
5. 图片由 `download_img` 下载到 `app/static/user_img`，返回前端时 `pic` 被改写为相对静态路径。

模型（`app/model.py`）：`ResizableEmbedding`（可扩容）+ `AttentionLayer` + deep 部分（tag/author 嵌入拼接 → 注意力 → 3 层全连接）+ wide 部分（质量分数线性层）→ sigmoid。标签为 Interest 分数经阈值二值化，训练用 balanced class_weight。

## 本地工作约定

- v0.2（React Web + /api/v1 + Feed 生命周期 + 反馈基础设施）：不改推荐算法与超参（threshold/alpha/beta/gamma/embedding_dim/num_epochs），`backend/recommender/model.py` 仅调整了导入与路径。算法改造留到 v0.3。
- v0.3.1（统一过滤规则层）：`filter_rules` 是屏蔽规则的唯一数据源，`blocked_ups` 继续负责按 mid/name 精确屏蔽，`blocked_keywords` 仅作历史保留、**已无任何代码依赖**。过滤顺序固定为 精确 MID → UP 关键词 → 标题关键词 → 标签 → 分区，实现在 `backend/services/filter_service.py`；降权只打标记，混合器限制每页 1 条并后置。不要在 route handler / FeedPage.tsx / recommender 里另写一套匹配逻辑。
- 规则变更（增删改启停、blocked_up 增删）必须调用 `filters.invalidate_cache()` 或经由其 CRUD 方法，它会 `filters:version +1`；保留不可变 stream 的模型与来源设置，读取缓存页时应用最新硬过滤。**绝不要**顺手清 `recommendation_history` / `served_videos` / `feedback` / `candidates`。命中统计只在候选真正参与 Feed 生成时累加（`record_hits=True`），预取预判不计数。
- 来源设置位于 `app_state['sources:settings']`，生成 stream 时冻结设置；切换档位或经典首页不改已有批次。`affinity.py` 分别计算观看与收藏集合：收藏不能推导已看，公开 `favorite` 收藏数不能推导用户收藏。单次完播只进入熟悉层，旧作仅挖常看 UP。`training_data.label` 和 `PROTOCOL` 不随来源改造改变。
- `backend/data/app.backup-*.db` 为迁移前时间戳备份（`backend/data/` 已被 gitignore，不会提交）。
- Bilibili API 可能已变化（-352/-101/412 等），先确认接口本身再怀疑算法。
- 日志/输出中绝不能出现 Cookie、SESSDATA、bili_jct 等凭证；`user_data/cookie.txt` 已在 `.gitignore`，提交前用 `git status` 确认。
- `docs/baseline_eval.md` 记录人工评分 Baseline；`.tmp/` 为临时测试文件，测试完须删除。


Host OS is Windows 10. Claude Code runs commands through Git Bash (MINGW64).
Do not assume macOS, Linux, WSL or Docker.
Use Windows-native paths for Windows programs and /d/... style paths only inside Git Bash.
Verify OS-specific commands before executing them.
