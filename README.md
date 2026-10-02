# BiliPersonal｜个人化 B 站推荐器

## 日常一键启动（Windows）

完成下方环境配置后，双击根目录的 **`启动BiliPersonal.bat`**，或在 PowerShell 中执行：

```powershell
.\启动BiliPersonal.bat
```

脚本检查 `.venv`、Node/npm 和前端依赖，每次先构建最新前端，再使用项目虚拟环境启动后端。打开 `http://127.0.0.1:8345` 使用；保留启动窗口查看日志，按 **Ctrl+C** 停止服务，若出现终止批处理的确认提示，输入 **Y** 并回车。

缺少环境、构建失败或端口不可用时，脚本显示错误并停止，不自动安装依赖或结束已有进程。首次安装前端依赖可在 `frontend` 目录执行 `npm ci`。下方保留手动启动及前端热更新的开发方式。

## v0.3.1 推荐来源

首页采用「关注新作 / 相关视频 / 常看 UP 旧作 / B 站推荐流 / 热门」多路召回。默认每页 12 条按 4 / 4 / 3 / 1 分配，热门仅补空位；个性化来源不足时先互相补位，再由仅兜底来源补齐（推荐流先于热门）。同一 UP 每页最多 2 条，相关来源的陌生 UP 每页最多 3 条、每人最多 1 条；降权内容每页最多 1 条并排在末尾。

设置 → 推荐来源，可分别将热门和推荐流设为关闭 / 仅兜底 / 少量（每页 1 条）/ 标准（每页 3 条）。关闭会隐藏对应入口；「经典首页」恢复 v0.3 首页路径。修改从下一次换一批生效，已有批次保留原设置与模型。最近 7 天面板按新来源记录展示占比，点击和不感兴趣率以实际曝光为分母；无法归因来源的旧记录不计入面板。

「关注」页按发布时间排列，不评分、不做曝光冷却、发布时间不限；首页的关注新作只取最近 30 天。单次完播只成为熟悉 UP，免陌生质量闸门，但不挖旧作；最近 1000 条观看历史中看过同一 UP 至少 3 个不同视频且各自达到 50%，或点赞 / 收藏过其视频，才成为常看 UP。

已看和收藏分别计算。实际观看历史或有效的「看过了」反馈排除视频；仅收藏且未看过的视频仍可推荐，并作为兴趣和种子信号。关注新作 / 推荐流 / 热门在 24 小时内不重复，连续展示 2 次未点击后不再推荐；旧作 / 相关视频在 2 次未点击后冷却 30 天。视频特征 `favorite` 是公开收藏数，不能据此认定用户已收藏。

「不感兴趣」可选择不喜欢 UP / 不喜欢题材 / 标题或封面党，分别施加 30 天 UP / 主标签降权；对陌生 UP 还暂停扩展 30 天。撤销反馈会撤销关联惩罚。调试开关「显示推荐评分」会显示来源理由、种子和 UP 观看统计。后台请求至少间隔 1 秒，遇风控退避并使用本地池；换一批最多补 12 条详情，不展示未补全内容。

首次 v0.3 排序器准入改为：新来源积累两三天后，在混合候选池上完成五批盲评；用户确认结果后，停止服务执行：

```powershell
.venv\Scripts\python.exe modelctl.py approve-activate 候选版本名 --confirm
```

该命令仅用于首个长期锚点，跳过首次在线试用；后续自动晋升继续走 50% 试用与原有护栏。未完成五批盲评、模型不匹配、缺少确认或已有锚点都会拒绝准入。实现与验收记录见 [docs/v0.3.1_validation.md](docs/v0.3.1_validation.md)。

## v0.3 推荐闭环与模型管理

实施约定见 [v0.3_PLAN.md](v0.3_PLAN.md)，验收记录见 [docs/v0.3_validation.md](docs/v0.3_validation.md)。SQLite 采用增量迁移，旧推荐流水保留；`historyVideo.json`、根目录旧权重和处理器不会被新训练覆盖。首次加载会把当前磁盘 v0.2 工件复制为不可变基线，`saved_model/active.json` 管理活动版本。

启动且已登录后，后台渐进收集最多 1000 个不同观看视频及 200 个收藏，保存分页断点并对风控退避；完成后每日增量同步。新训练使用每个 BV 一条证据加权样本、按行为时间 80/20 验证、重载最佳验证 checkpoint。新工件先进入冻结评价或最新待评队列，当前模型继续提供 Feed。未点击曝光只用于观察，屏蔽 UP 只作精确过滤。

前端只增加位置归因与无界面采集：50% 可见、前台连续 1 秒才记录曝光；点击立即打开 B 站，网络重试沿用事件 ID。快速操作保留主动操作时间，可见时间留空。旧生成记录不会补造为曝光。

在项目根目录执行以下命令。**切换、训练、评价和准入命令前先 Ctrl+C 停止服务**；命令会检查 8345 端口，完成后用启动脚本重新启动。

```powershell
.venv\Scripts\python.exe modelctl.py list
.venv\Scripts\python.exe modelctl.py rollback
.venv\Scripts\python.exe modelctl.py switch 已批准的版本名
.venv\Scripts\python.exe modelctl.py train
.venv\Scripts\python.exe modelctl.py evaluate
```

`rollback` 默认切换到前一版，`switch` 可选已批准版本或保存的 v0.2 基线；不会清除反馈、候选、过滤或观看历史。活动工件加载失败会尝试验证前一版和基线；全部失败时明确报错并按候选顺序降级，不能假报模型 ready。已有 stream 保持原模型版本，下一次浏览使用新活动版本。

首次准入：在五个不同时间正常浏览、更新候选池后，分别停止服务执行 `modelctl.py blind-capture`。未更新候选池不能重复填满五批。命令输出 `saved_model/blind/<评价ID>/rating.json`；为每项填写 `score`（3=非常想看、2=想看、1=一般、0=不想看、-1=强烈排斥），可分次完成，已有评分会保留。

```powershell
.venv\Scripts\python.exe modelctl.py blind-report
# 需要保留原有在线试用流程时，用户确认后执行：
.venv\Scripts\python.exe modelctl.py approve-trial
# 异常时提前停止并继续当前模型：
.venv\Scripts\python.exe modelctl.py stop-trial
```

盲评报告包含想看率、低分率、共同候选池的 NDCG@10 和批次差异。首次试用只在个性化首页“全部”按新浏览批次等量分组；分页和重试不换模型。前向验证须包含至少 100 个不同视频、正负各 20 个，验证证据晚于模型冻结及训练截止；不复用训练、内部验证或已消费的发布集合。正式发布同时要求配对 AP 非劣、证据加权 loss 相对增加不超过 2%、至少 7 天和每组 20 次有效浏览/500 个展示机会/100 个不同视频，以及两项负反馈率观测值不升。

服务按固定 UTC 每日结算点先检查数量，首次同时达到全部条件时只正式判定一次，并保存不可变报告。零反馈和退化置信区间会注明证据不足。后续同协议通过离线及产品护栏才自动切换；失败暂停自动晋升，需人工判断。已结束且封存的模型对可用 `modelctl.py finish` 开启最新待评版本；运行中的试用不能跳过结算。更改标签、权重或评价语义需重新人工确认。

Quality 与排序策略实验默认关闭，分别使用 `modelctl.py train --quality` 或 `modelctl.py train --diversity` 创建独立候选，不能在同一实验同时启用两项。策略收益仍须通过盲评/在线产品护栏，不以模型 AP 不变宣称有效。时间衰减及新向量主干留待 v0.4。

验收命令（临时 SQLite、固定样本、假接口，不调用真实 B 站）：

```powershell
.venv\Scripts\python.exe -W ignore -m unittest discover -s backend/tests -p "test*.py" -v
cd frontend
node tests/telemetry.cjs
npm run build
```

以下原项目的特征和指标说明主要描述保留的 v0.2 基线；当前 v0.3 训练和发布以本节及定稿计划为准。

---

本地优先的 B 站个性化推荐客户端。从 Bilibili 获取候选视频与用户行为数据，在**你自己的机器上**构建兴趣画像、执行过滤规则、训练推荐模型并完成排序，最终以一个类似现代视频平台的信息流界面呈现。

项目的目标不是复刻 Bilibili 官方推荐算法，而是让用户拥有更多控制权。

> 声明：BiliPersonal 是非官方第三方项目，与哔哩哔哩（Bilibili）官方无关联，仅用于学习和测试。

---

## 功能

- 自定义个性化推荐 Feed（`for_you` / `hot` / `explore` 三条流）
- 兴趣探索、换一批、推荐去重（24 小时窗口内不重复）
- 推荐缓存与秒开（分页幂等，`stream_id` + `page`）
- 反馈学习基础设施：不感兴趣 / 屏蔽 UP / 已看 / 稍后再看 / 点赞 / 点击
- **统一过滤规则**：标题关键词、UP 主名称关键词、精确屏蔽 UP（按 mid）
- 规则命中统计（命中次数 + 最近命中时间）与规则变更即时缓存失效
- 推荐历史、兴趣画像、搜索
- Light / Dark Mode
- React + TypeScript Web UI
- 本地 SQLite 数据存储

推荐模型、候选策略与过滤规则全部运行在用户自己的环境中。

---

## 与上游的关系

BiliPersonal 派生自 [tLLWtG/bilibili-Recommender](https://github.com/tLLWtG/bilibili-Recommender)（MIT），并在其基础上做了两轮改造：

| 版本 | 内容 |
|---|---|
| 上游 | Flask + Jinja2 页面：二维码登录 → 抓取收藏/历史 → 现场训练 Wide&Deep + Attention → 从热门榜重排推荐 |
| **v0.2** | 前后端重构为 **React SPA + Flask `/api/v1`**；新增 Feed 生命周期（候选池 / 去重 / 分页缓存）、反馈基础设施与 SQLite 存储；原 Jinja 实现移入 `legacy/` |
| **v0.2.1** | 新增 **`filter_rules` 统一过滤规则层**：迁移旧屏蔽数据、导入 BiliBlock 规则、三 Tab 规则管理界面、`filters:version` 缓存即时失效 |

推荐模型结构、评分公式与超参（`threshold` / `alpha` / `beta` / `gamma` / `embedding_dim` / `num_epochs`）在 v0.2 与 v0.2.1 中**均未改动**，与原版保持一致，以便对比原始推荐效果。算法改造留待 v0.3。

---

## 目录结构

```text
backend/    Flask /api/v1 + 服务层 + SQLite
  app.py            create_app、SPA 托管
  config.py         路径、TTL 等集中配置
  routes/           auth / feed / feedback / user / system
  services/         bilibili / cache / filter / feedback / recommendation / training
  recommender/      Wide&Deep + Attention 模型（自上游继承，仅调整导入与路径）
  storage/          数据库连接、表结构、迁移脚本
frontend/   React + TypeScript + Vite + TanStack Query + Tailwind
legacy/     原 Jinja 页面实现（python legacy/run_legacy.py，仅供对照）
docs/       Baseline 评价记录、Codex Skills 说明
.agents/    Codex Skills（frontend-design / ui-ux-pro-max）
```

---

## 环境配置及运行

### 1. Python 环境

原始 `requirements.txt` 指定 Python 3.9.20。**注意：该文件在 Windows 上无法一次装完** —— `tensorflow_intel==2.18.0` 的包元数据声明 `numpy>=1.26.0,<2.1.0`，与文件里的 `numpy==2.2.0` 直接冲突：

```text
× No solution found when resolving dependencies:
  Because tensorflow-intel>=2.18.0 depends on numpy>=1.26.0,<2.1.0
  and you require numpy==2.2.0 ... your requirements are unsatisfiable.
```

请改用 **`requirements.win-test.txt`**，其头部记录了三步安装法（先让 TensorFlow 满足自身元数据，再把 `numpy` / `keras` 覆写回原版锁定值）。该清单已在 Windows + Python 3.11.16 实测通过，TensorFlow 2.18.0 在 numpy 2.2.0 下可正常导入与训练。

```powershell
uv venv --python 3.11 --seed .venv
# 再按 requirements.win-test.txt 头部说明安装依赖
```

### 2. 启动后端

```powershell
.\.venv\Scripts\python.exe run.py
```

监听 `http://127.0.0.1:8345`。**仅绑定本机回环地址**，不要改成 `0.0.0.0`。

### 3. 前端

```powershell
cd frontend
npm install
npm run build      # 产物由 Flask 托管，之后直接访问 8345
```

开发模式（`/api` 代理到 8345）：

```powershell
cd frontend
npm run dev        # http://127.0.0.1:5173
```

### 4. 使用

1. 打开 `http://127.0.0.1:8345`
2. 用手机 Bilibili App 扫描二维码登录并确认
3. 后端自动抓取收藏与观看历史，生成兴趣数据并在后台线程训练模型
4. 进入 Feed 浏览推荐；设置页可管理过滤规则与调试选项

要点：

- 模型结构、特征与训练参数与原版一致；`saved_model/meta.json` 记录 `history_hash`，历史数据未变化时启动直接加载模型，训练在后台线程执行。
- 候选池、已展示视频、Feed Cache、反馈、过滤规则、推荐历史保存在 `backend/data/app.db`。
- Cookie 只保存在 `user_data/cookie.txt`，任何 API、数据库和日志中都不会出现。
- 快捷键：`/` 聚焦搜索，`R` 换一批；设置 → 调试 可开启评分显示与 Debug 面板。

---

## 过滤规则

过滤按固定顺序执行，全程在推荐排序**之前**，同样作用于搜索结果：

```text
候选视频
  ↓ 精确屏蔽 UP（blocked_ups，按 mid / 完整名称）
  ↓ UP 主名称关键词（filter_rules.target_type = uploader）
  ↓ 标题关键词（filter_rules.target_type = title，同时匹配标签）
  ↓ 标签规则（filter_rules.target_type = tag）
  ↓ 分区规则（filter_rules.target_type = zone）
  ↓ 推荐模型 Rating / 排序
  ↓ served_videos 去重
Feed
```

规则模型实现在 `backend/services/filter_service.py`，SQL 与匹配逻辑集中于此，不散落到 route handler 或前端：

- `hard_block` 命中任意一条即不进入最终 Feed，模型 Rating 不能覆盖
- `downrank` 命中时保留视频，混合页最多展示 1 条并排在末尾
- 关键词统一 `trim` + `casefold` 归一化，唯一索引 `(target_type, keyword_norm, match_mode)` 防重
- 英文大小写不敏感，中文按包含关系匹配
- 命中统计只在候选**真正参与 Feed 生成**时累加；同一视频命中同一规则只记 1 次，命中多条规则各自 +1

**缓存即时过滤**：规则或 `blocked_up` 变更让 `app_state['filters:version']` 自增；保留 stream 的模型和来源归属，读取缓存页时重新应用当前硬过滤，不清理 `recommendation_history` / `served_videos` / `feedback` / `candidates`。降权顺序从新生成页面开始生效。

设置页 → 过滤规则 提供五个 Tab：标题关键词 / UP 主关键词 / 标签 / 分区 / 精确屏蔽 UP，支持搜索、单条增删、启用禁用、屏蔽 / 降权切换、批量导入（按 `|` 或换行分隔，返回输入/新增/重复/无效数量）。

---

## API

统一前缀 `/api/v1`，仅监听本机。主要端点：

```http
# 认证
POST   /auth/qrcode              获取登录二维码
GET    /auth/qrcode/status       轮询扫码状态
POST   /auth/logout
GET    /auth/status

# Feed
GET    /feed                     推荐流（type / category / cursor / limit）
POST   /feed/refresh             换一批
GET    /feed/categories
GET    /feed/explain/<bvid>      推荐解释
GET    /feed/history             推荐历史
GET    /search

# 反馈与过滤
POST   /feedback
GET    /feedback/history
DELETE /feedback/<id>
GET    /filters                  旧兼容接口
GET    /filters/rules            统一规则列表（target_type / enabled / q）
POST   /filters/rules
PATCH  /filters/rules/<id>
DELETE /filters/rules/<id>
POST   /filters/rules/import     批量导入
GET    /filters/summary
POST   /filters/ups              精确屏蔽 UP
DELETE /filters/ups/<id>

# 用户与系统
GET    /user/profile  /user/interests  /user/history  /user/favorites  /user/watch-later
GET    /system/status  /system/debug
GET    /system/sources           来源设置与最近 7 天来源统计
PUT    /system/sources           修改热门 / 推荐流档位与经典首页开关
POST   /system/retrain
```

---

## 数据存储

全部数据保存在本地，相关路径均在 `.gitignore` 中：

| 路径 | 内容 |
|---|---|
| `user_data/cookie.txt` | 登录凭证（**唯一**保存位置） |
| `backend/data/app.db` | SQLite：候选池、Feed 缓存、已展示、推荐流水、反馈、过滤规则、状态键值 |
| `historyVideo.json` | 模型输入契约（历史 + 收藏视频特征） |
| `saved_model/` | 模型权重、特征处理器、训练指标、`meta.json`（含 `history_hash`） |

数据库表：`candidates`、`feed_cache`、`served_videos`、`recommendation_history`、`feedback`、`blocked_ups`、`blocked_keywords`、`filter_rules`、`app_state`。

**隐私说明**：

- Cookie 只写入 `user_data/cookie.txt`，**任何 API 响应、数据库表与日志中都不会出现**
- `filter_rules` 等表不含任何凭证字段
- 封面图片缓存在本地静态目录，不直连 B 站图床

---

## 技术细节

### 用户行为数据获取

本项目调用 bilibili 网页 API 获取登录用户的观看、收藏、投币、点赞等行为数据（使用的 bilibili API 来源于 [bilibili-API-collect](https://github.com/SocialSisterYi/bilibili-API-collect)）。之后进行简单的预处理，并整理成 Json 文件，方便推荐模型处理。

相较于常见用户手动打分的方案，系统自动获取用户行为数据可以提供更加流畅、无缝的用户体验，也为之后的基于深度学习的推荐模型提供了更多维度的特征（点赞、收藏、观看时长等）。

### 前后端

- **前后端数据交互**：后端以 Flask 提供 `/api/v1` JSON API，前端为 React SPA；Flask 同时托管前端构建产物。
- **模拟登陆操作**：后端调用 bilibili 网页登陆 api，然后将相关信息合成可以识别的登陆码存于二维码中，再将二维码图片传给前端。这样用手机 app 扫描后，后端即可模拟登陆操作，获取用户的 cookie。
- **获取用户行为数据**：利用登陆时获取的用户 cookie，然后使用 requests 库访问对应的 api 即可获取用户行为数据（历史观看、收藏等）。之后将其整理成方便展示和适合推荐模型处理的 json 格式。

> 上游的 Flask + Jinja2 实现已完整保留在 `legacy/`，可用 `python legacy/run_legacy.py` 对照运行。

### 推荐模型

考虑到只能获取当前用户的数据，因此使用基于物品的推荐方法。除此之外，希望模型能支持在线学习。因此模型方案选用：**Wide&Deep + Attention**（基于 Tensorflow 库）。

#### 特征工程

对于模型需要提取的特征，我们划分了以下三类：

1. 内容类型特征：标签向量和作者特征，这里表示了这个样本主要吸引的人群范围。对于作者特征，这里将作者视为类型变量。标签向量的处理，这里考虑标签特征通过 embedding 层处理，使用 attention 机制关注重要的标签，作者信息同理。因为要考虑在线学习机制，所以 embedding 层要求能动态重建。

2. 内容质量特征：总浏览量、点赞数、收藏数，这里可能要做一个比例，计算出来内容质量评分。

   $$\text{Score} = \alpha \times \left( \frac{\text{Likes}}{\log{(\text{Views +1})}} \right)+ \beta \times \left( \frac{\text{Favs}}{\log{(\text{Views +1})}} \right) + \gamma \times \log(\text{Views}+1)$$

3. 用户兴趣画像：对于每个视频，当前用户的观看进度，是否点赞，是否收藏，从而计算出来用户的感兴趣评分。

   $$\text{Interest} = \max \left( \text{isliked} + \text{isfaved}, \frac{\text{progress}}{\text{duration}}\right)$$

#### 模型构建

提取特征后，分别经过 deep 部分和 wide 部分。

* **deep 部分**

  主要处理内容类型（或者说视频面向的兴趣范围）特征。输入层中，标签通过可扩展的 Embedding 层，转为低维稠密向量，作者 ID 也是，转成了作者向量，然后两个 embedding 拼接在一起做一个组合特征 `combined_embedded`。而质量分数直接作为数值特征输入。Attention 层，使用注意力机制来处理标签序列，前面的组合特征 `combined_embedded` 会被输入进来，计算不同的标签的重要性权重，然后得到一个综合的内容特征向量。Deep layer 层，包含了 3 个全连接层。

* **wide 部分**

  主要是处理内容质量特征，通过一个简单的线性层直接学习内容质量分数和用户兴趣之间的关系，用来尝试捕捉一些比较明显的特征关系。线性变化公式为：`wide_output = quality_score * w + b`

最后将 Wide 部分和 Deep 部分的输出连接起来，通过一个 sigmoid 激活函数的全连接层得到最终的预测分数。

另外考虑到正负样本可能存在不平衡问题（事实上确实存在此问题，因为用户点赞信息相比总共的浏览记录是比较少的），在实际训练时，还加上了类别权重：`class_weight.compute_class_weight(class_weight='balanced', classes=np.unique(labels), y=labels)`

---

## 已知限制

v0.3.1 加载／刷新延迟的第一轮诊断见 [docs/v0.3.1_feed_performance_report.md](docs/v0.3.1_feed_performance_report.md)。默认不启用诊断；运行服务前设置环境变量 `BILIPERSONAL_FEED_PERF=1` 可记录第一层 Feed 阶段、请求和等待耗时，日志默认写入忽略目录 `.tmp/feed-performance/traces.jsonl`，响应头包含 `X-Feed-Trace-Id`。`BILIPERSONAL_FEED_PERF_DEEP=1` 才开启额外 SQL 等深度统计，本轮定位不需要开启。采样工具 `python -m backend.tools.feed_performance --real --count 2` 使用生产数据库副本，保持请求限速，不写生产 Feed 记录。

性能优化的行为边界、50 次 HTTP 验收及测试结果见 [docs/v0.3.1_feed_optimization_report.md](docs/v0.3.1_feed_optimization_report.md)。普通新版 Feed 先用完整候选成页，不足时由后台补池，可稍后手动换一批；关注首屏与具体分区保留有限的新鲜度请求。

- **Windows 上 `requirements.txt` 无法直接安装**，请使用 `requirements.win-test.txt`（原因见上）。
- **Bilibili API 可能变化**（`-352` / `-101` / `412` 等）。热门榜接口在短时间连续请求时会触发 `-352` 风控，属瞬时限流，通常数十秒后自愈。遇到空结果请先确认接口状态，再怀疑算法。
- 用户行为数据仅来自当前登录账号，训练样本量有限（通常数十条），模型指标为**训练集自评**，不代表泛化能力。实测中 `quality_score` 常因 `np.clip` 截断为常数，使 wide 部分退化为偏置项——该现象记录在 `docs/baseline_eval.md`，未做修改以保留原始 Baseline。
- 过滤规则中的宽泛关键词（如「生活」「vlog」）命中率高，可能连带屏蔽相关内容；如需放宽，可在设置页把该规则行为切换为「降权」。

---

## 开发团队

* [tLLWtG](https://github.com/tLLWtG)（编写后端框架、设计前端页面）
* [wegret](https://github.com/wegret)（构建推荐模型、部分后端代码）
* [corgiInequation](https://github.com/corgiInequation)（对接 bilibili 网站的相关 API、部分后端代码）

BiliPersonal 的 v0.2 / v0.2.1 改造由 [BaoBaodesu](https://github.com/BaoBaodesu) 维护。

## License

本项目采用 **MIT License**，派生自 [tLLWtG/bilibili-Recommender](https://github.com/tLLWtG/bilibili-Recommender)（MIT）。

上游的版权声明与许可证全文**原样保留**在 [LICENSE](LICENSE)（`Copyright (c) 2024 tLLWtG`），未作任何修改：

> The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software.

本项目对上游的修改说明、各版本变更范围与第三方组件清单见 [NOTICE](NOTICE)。项目使用的第三方依赖遵循各自的许可证。
