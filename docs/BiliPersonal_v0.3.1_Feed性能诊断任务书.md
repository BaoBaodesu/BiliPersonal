# BiliPersonal v0.3.1 Feed 性能诊断任务

日期：2026-10-01。本文是待执行任务书，本轮只编写任务书，不实施埋点或性能优化。

## 1. 目标与范围

定位 v0.3.1 首次加载、换一批、加载更多变慢的原因，回答：延迟主要来自哪个来源、同步外部请求、详情补全、缓存不足、排序、混合、SQLite，还是锁和节流等待？给出可复现的数据和按收益排序的最小修改建议。

先测量，再决定优化。诊断阶段不改变配额、质量闸门、屏蔽规则、曝光冷却、30 天窗口、UP 分层、收藏与已看分离、stream 不可变性、训练标签和排序器准入流程；不降低 Bilibili 请求间隔或取消风控退避。不新增依赖，不修改编辑器和项目设置，不提交代码。

必须区分三个用户操作：

- 首次加载：`GET /api/v1/feed`，没有 cursor，会调用 `refresh()` 新建或复用 stream。
- 换一批：`POST /api/v1/feed/refresh`，生成新批次。
- 加载更多：带 cursor 的 `GET /api/v1/feed`，可能命中已生成页，也可能生成下一页。

## 2. 当前代码证据与待验证假设

以下是静态代码确认的行为，尚不是实测耗时结论。

| 位置 | 已确认行为 | 需要测量的问题 |
|---|---|---|
| `recommendation_service._generate_mixed()` | 每个 stream 的第一页都会尝试扩展 related；相关候选已经充足也会进入扩展条件 | 热缓存换批是否仍同步发起最多 3 个种子请求 |
| 同上 → `pool.complete()` | 排序前同步补详情，预算为 12；当前实现预算也会计入失败尝试 | 已有足够可展示内容时，是否仍等待不必要的详情补全 |
| `recommendation_service._page()` | 持有全局 `_gen_lock`，覆盖缓存读取和整页生成，包括上述同步操作；`refresh()` 也获取该锁 | 慢批次是否阻塞其他 Feed 请求，甚至阻塞缓存页读取 |
| `bilibili_service.get()` | 全部调用共享 `_lock` 和至少 1 秒节流；sleep 位于锁内，网络请求在锁外 | 前台是否排在后台／历史同步之后；锁等待与自己的节流 sleep 分别多长 |
| `source_scheduler.tick()` | 工作锁 `_lock` 使用 `blocking=False`；候选来源锁也使用非阻塞获取 | 当前通常是锁忙后跳过，而非等待；后台是否经共享节流器间接拖慢前台 |
| `pool.complete()` / `rank_sources()` | 多次读取候选、执行过滤；过滤还读取惩罚数据 | 重复 SQLite 查询、JSON 解码和过滤是否随候选池规模明显增长 |
| `source_scheduler.tick()` | 顺序检查来源并补足详情缓冲 | 后台是否持续补充最终过不了质量闸门或不满足首页窗口的内容，消耗请求额度 |
| `useAutoUpgradeFeed()` / `LoadMoreTrigger` | 自动升级可能刷新，滚动触发器可能追加下一页；Feed 查询有重试 | 一次操作是否实际发出多个 Feed 请求，视觉等待是否包含串行第二批 |
| `_page()` 返回字段 | `from_cache` 当前固定为 false | 不能用这个字段判断实际缓存命中，必须在 `_cached_page()` 分支埋点 |

重点假设是“同步召回与补详情 + 全局生成锁 + 前后台共享节流”。不得仅凭代码将问题归因于 ranker、mixer 或 scheduler 工作锁，也不得把既有 63 项功能测试通过等同于性能验收通过。

## 3. 必须统计的指标与口径

计时统一使用单调高精度时钟，如 `time.perf_counter_ns()`；毫秒保留小数。每个 Feed HTTP 请求单独记录，成功、失败、空结果和风控降级都必须收尾。

| 指标 | 统计口径 |
|---|---|
| `total_ms` | Flask 接收请求到响应构造完成的墙钟耗时，包含锁等待、同步 API、SQL 和 JSON 响应序列化；不包含浏览器渲染、浏览器到服务端网络或响应发送完成时间 |
| `sources.{source}.source_ms` | 本请求中归属于该来源的缓存读取、同步召回、详情补全、来源内排序等 span 的时间并集；固定列出 follow / up_archive / related / rcmd / hot，禁用或未执行标为状态并计 0，共享画像／过滤另计 |
| `bilibili_api_calls` | 本请求发出的应用层 Bilibili HTTP 调用次数，在真实 Session 发起调用处计数；WBI 刷新产生的 nav 请求同样计入，按接口路径及 source 分类；命中缓存不算 API 调用 |
| `detail_requests` | 实际发出的 `/x/web-interface/view/detail` 应用层调用次数，是 `bilibili_api_calls` 的子集；另记 `detail_calls` 和 `detail_cache_hits`，不能把调用 `detail()` 等同于网络请求 |
| `sync_candidates_added` | 本请求同步召回新插入的唯一 `(source,bvid)` 数量；冲突更新旧候选不算新增，另记 fetched / prefiltered / inserted / updated 数量 |
| `sync_detail_completed` | 本请求把候选从未补全变为已补全的唯一 `(source,bvid)` 数量；另记 attempted / failed / LRU 命中 / 网络次数，跨来源同一 BV 的网络请求单独去重统计 |
| `candidate_cache_hit_rate` | 返回页中在本请求生成阶段开始前已存在、未过期且详情完整的条目数 ÷ 返回条目数；按最终来源与 BV 匹配。该指标回答“这页有多少内容直接复用缓存”；空页为 null，分子／分母同时输出 |
| `rank_ms` | 各来源模型打分、来源内排序与 item 构造的总耗时；公共过滤／质量闸门／品类选择计入 `rank_prepare_ms`，模型加载单列 `model_load_ms`，不能笼统合并成模型推理时间 |
| `mixer_ms` | 仅 `source_mixer.mix()` 的实际耗时；缓存页不执行、经典路径不适用时分别记录状态 |
| `SQLite_ms` | 本请求实际 SQLite 连接建立、PRAGMA、execute/executemany、fetch、commit/rollback、close 的耗时总和；不是整个 `with connect()` 的驻留时间，里面可能包含其他业务操作 |
| `waited_rate_limiter` | 是否实际因请求节流等待；拆出 `rate_limiter_lock_wait_ms`、`rate_limiter_sleep_ms`、次数，并记录是否在排队时观察到锁被占用 |
| `waited_source_scheduler_lock` | 是否实际阻塞等待 scheduler 工作锁；当前非阻塞失败应记录 busy/skipped，等待值为 0，不能写为 true |

配套指标必须包含：

- `gen_lock_wait_ms`：分别测量 `refresh()` 与 `_page()` 获取全局生成锁的等待，记录累计值、次数和持有时长。这是与 scheduler 锁不同的关键指标。
- `source_pool_lock_busy_count`：按来源记录 `CandidatePool._locks[source]` 非阻塞获取失败；不称为“等待”。记录 `source_scheduler_lock_busy_count`、`scheduler_start_lock_wait_ms`，区分工作锁与启动锁。
- `feed_page_cache_hit`：真正命中 `_cached_page()` 的布尔值。缓存页命中时不重新扫描整个候选池来计算诊断指标，候选复用比例标为不适用。
- `pool_read_calls`、`pool_rows_read`、`json_decode_ms`、`affinity_ms`、`filter_ms`、`cooldown_ms`、`record_ms`、`response_json_ms`，辅助定位 SQLite 之外的 CPU 成本。
- 每来源补全前的 raw / complete / eligible / returned 数量、TTL 是否过期、召回原因（空池、TTL、第一页 related、后台补缓冲）。对尚无详情的内容，不假称已通过依赖详情的完整过滤。
- `bilibili_http_ms`、成功／失败次数、超时／风控码、HTTP 重试次数。requests/urllib3 的内部重试不会再次调用服务的 `get()`，必须另记 retry/attempt 信息；无法完整观测底层尝试时明确标记未知，不能输出虚假的 0。
- `rate_limited_at_start`、`rate_limited_at_end`、`backoff_remaining_ms`、`backoff_skipped_count`。风控退避状态不等于前台实际睡满退避时间；后台 `stop.wait()` 不计入前台 `total_ms`。
- `sqlite_connect_count`、`sqlite_statement_count`、`sqlite_commit_ms`、最慢 SQL 模板及结果行数；另查过滤服务自己的 `_connect()`，不能只覆盖统一 `database.connect()`。不能把慢 SQL 全部称为 SQLite 锁等待。

计数归属使用请求上下文，而不是“全局计数器前后相减”：并发后台调用会污染后者。来源相关的 nav/WBI 子调用继承当前来源；无法归属某来源的调用标为 shared，不能漏记。

## 4. 时间归因与日志格式

每条记录有 `trace_id`、`owner`、`operation`、`feed_type`、`category`、`page`、`limit`、`mode`、`model_version`、来源档位、缓存状态、返回数、状态码及错误类型。`owner` 区分 foreground_feed / source_scheduler / history_sync / training；后台任务使用独立 trace，不继承并累加到前台。

后台 trace 与前台关联时，仅标记因果关系或时间重叠，并记录竞争资源。前台在节流锁上排队时，可额外记录已知的占用任务类型；不能把同期后台全部耗时算进前台。

必须保留 span 的父子关系和开始／结束相对时间：

```text
Feed total
├─ refresh / page 的 gen_lock wait
├─ stream / page cache read
├─ model load / affinity / cooldown / shared preparation
├─ source recall + source detail completion
│  └─ API: rate limiter lock wait → sleep → signing → HTTP / retries
├─ source rank
├─ mixer
├─ recommendation / page persistence
└─ return filtering / response JSON
```

`source_ms`、`rank_ms`、`SQLite_ms`、API 及等待都是可重叠的诊断视角，不能全部相加得到 `total_ms`。汇总报告另提供互不重复的前台关键路径分解；未归因部分保留为 `other_ms`，不得强行塞入某个来源。若未来并行化，来源耗时使用 span 并集，区分工作量总和与墙钟等待。

结构化日志采用 JSONL。下面是字段形状示例，null 表示待测值，不是测量结果：

```json
{
  "trace_id": "request-id",
  "owner": "foreground_feed",
  "operation": "refresh",
  "feed_type": "for_you",
  "mode": "mixed",
  "total_ms": null,
  "feed_page_cache_hit": false,
  "sources": {
    "follow": {"status": "not_measured", "source_ms": null},
    "up_archive": {"status": "not_measured", "source_ms": null},
    "related": {"status": "not_measured", "source_ms": null},
    "rcmd": {"status": "not_measured", "source_ms": null},
    "hot": {"status": "not_measured", "source_ms": null}
  },
  "bilibili_api_calls": null,
  "detail_requests": null,
  "sync_candidates_added": null,
  "sync_detail_completed": null,
  "candidate_cache_hit_rate": null,
  "rank_ms": null,
  "mixer_ms": null,
  "SQLite_ms": null,
  "gen_lock_wait_ms": null,
  "waited_rate_limiter": null,
  "rate_limiter_lock_wait_ms": null,
  "rate_limiter_sleep_ms": null,
  "waited_source_scheduler_lock": null,
  "source_scheduler_lock_busy_count": null
}
```

上述 source 对象执行时应展开缓存、召回、详情、排序、API 次数及新增数量。诊断开关关闭时不生成额外候选扫描；打开时也尽量利用已有读取结果。错误路径用 finally 收尾，计数不能因异常或提前返回缺失。

日志只保存接口路径、SQL 模板和必要的数量／耗时。不要保存 Cookie、签名参数、完整请求头、视频标题、用户历史或 SQL 参数。trace ID 必须不包含用户信息；不向第三方上传诊断数据。

## 5. 埋点位置与最小实施顺序

1. 增加轻量诊断上下文与 span 工具，默认关闭。Feed 路由请求完整覆盖，包括校验失败、异常响应和 jsonify；在本地诊断响应头给出 trace ID，方便与浏览器请求对应。
2. 在 `RecommendationService.refresh()` / `_page()` 测全局生成锁，区分缓存命中／生成；在 `_generate_mixed()`、`rank_sources()` 和 `mix()` 建立阶段及来源 span。
3. 在 `CandidatePool.all()` / `expand()` / `complete()` / `_save()` 计读取、来源锁忙、真实插入／更新和补全成功／失败。现有返回值保持兼容；不要直接把 `expand()` 返回值当真实新增数，也不要把 `complete()` 返回值当真实成功数。
4. 在 `BilibiliService.get()` 分开记录节流锁排队、sleep、签名、HTTP；在 `detail()` 的 LRU 命中／未命中分支计数。HTTP 重试留出明确统计口径。
5. 在 scheduler 的 `start()` / `tick()` / `fetch()` 记录启动锁、工作锁忙、来源任务及缓冲变化；不为了计时把非阻塞锁改为阻塞锁。
6. 覆盖统一数据库连接和过滤服务独立连接；用兼容的连接／游标计时封装测 SQL 和 fetch，不改变事务、timeout、WAL 或原有错误行为。同时统计查询后的 JSON 解码，防止误计为 SQL。
7. 增加可重复执行的本地采样／汇总脚本，输出 JSONL、CSV 和 Markdown 报告；前端仅为关联首屏操作添加最小必要记录，不制作新的调试大页面。

尽可能保持原结构与变量命名。埋点修改与优化修改分阶段；先交付未优化的数据，避免测量到的只有优化后的行为。

## 6. 对照场景与采样方法

先用可控假接口、独立临时数据库做重复测量，再做少量真实登录态核验。不要清空生产候选池、观看历史、曝光或模型；不要用会清空来源状态的 `scheduler.stop()` 来模拟“只暂停后台”。

| 场景 | 控制条件 | 重点 |
|---|---|---|
| 冷启动首次加载 | 临时库无候选；分别冷／热模型，保留同一画像输入 | 同步召回、补详情及模型初始化成本 |
| 暖进程、候选为空 | 模型已加载，其余与上项相同 | 排除 TensorFlow 初始化影响 |
| 热池首次加载／换批 | 已有足够通过过滤／闸门／冷却的完整详情候选 | 充足缓存下是否还请求 related、补详情 |
| 原始列表热、详情不足 | 候选存在但详情不足，分别冷／热 detail LRU | 候选缓存和 detail 内存缓存的区别 |
| 热池加载更多 | 同一 stream 的新 page | 是否每页同步补齐 12 条无关详情 |
| 缓存页重读／同 view 重试 | 重用有效 stream、cursor、view，不改过滤规则 | 是否零召回／零推理；会否仍被另一批次的生成锁阻塞 |
| 后台竞争 | 假接口中固定同一候选快照；scheduler／历史同步分别启用与不启动 | 共享节流锁、重复详情、全局生成锁；不同时改变候选质量 |
| 两个并发 Feed 请求 | 一个生成新页，一个重读缓存页；再测两个新批次 | `_gen_lock` 队首阻塞及锁持有阶段 |
| 分区切换 | 热池切换 all → 一个有种子的真实分区 | 同步种子扩展、画像与过滤成本 |
| 风控／超时／重试 | 用假接口触发，真实接口只观察自然出现的情况 | 本地退回是否迅速；请求次数是否包含失败和重试 |
| 关注页 | 热完整 follow 池、冷 follow 池各一次 | 不评分路径、详情补全和时间线读取 |
| 经典首页 | 同进程、同画像、同模型，使用新的 classic stream | 召回结构对照；注意仍共享 v0.3.1 新增的全局节流器 |
| 原 v0.3 对照（条件允许） | 核实实际 Git 版本；隔离 checkout 和数据，冻结同一候选输入 | 区分旧版差异与“经典首页”开关差异，不能将两者等同 |

每次记录可用候选池大小、过滤后各来源数量、已展示状态、模型热度、LRU 热度和后台活动。换批会消耗候选并影响冷却，不能把第 1 批和第 30 批直接当同条件样本；假接口对照需恢复同一快照，真实样本按可用候选状态分组。

- 本地确定性场景至少 30 个请求，输出 p50 / p95 / max、计数总和及失败数；并发场景按请求分别统计。
- 真实默认档位以 10 批为一组，保持现有限速并留出间隔。记录这一组的候选消耗与后台活动，不用高并发压测 Bilibili；样本不足时不宣称稳定的 p99。
- 浏览器记录操作到第一批卡片显示的 `ui_total_ms`，与每次 Feed `total_ms` 对齐；额外统计 Feed HTTP 数量及触发原因。自动升级／自动分页／重试分别列出，不把 status、categories 请求计入 Feed 请求数量。
- 若后端快而卡片慢，再检查响应传输、前端数据更新、渲染和图片加载；图片加载不计入后端 `total_ms`。

## 7. 诊断输出与优化决策

交付报告必须包含：

1. 指标完整的原始 JSONL 与采样环境、时间、版本、诊断开关及复现步骤。
2. 各场景 p50 / p95 / max，以及五来源耗时、API／detail 次数、同步新增／补全数、缓存命中、rank／mixer／SQLite 和所有锁／节流等待。
3. 至少 3 个代表性 trace：热缓存慢请求、冷请求、后台竞争请求；若某类不能复现，写明未复现。
4. 慢请求前台关键路径，SQL 与 API 的具体热点；解释是否因重复扫描、缓存可用性不足、背景抢占或队首阻塞。
5. 每项结论的证据强度：实测确认／代码风险但尚未复现／已排除。禁止用功能测试通过或原生来源比例达标替代性能证据。
6. 优化建议按预期收益、改动范围与语义风险排序，再单独实施；保留优化前后的同条件记录。

优先验证以下建议，不预先把它们当作确定方案：

- 当完整可用候选足够时，是否可先从缓存生成页，把新 related 扩展交给后台；需要明确保留分区切换的实时扩展约定。
- 同步补详情是否应基于各来源缺口决定，而非每批无条件消耗最多 12 个尝试；必须继续禁止展示未补全条目。
- `_gen_lock` 是否可缩小范围或按 stream/page 协调；必须保留并发重试幂等、固定模型／分组、一次记录和曝光归属。
- 后台是否可让出前台请求优先级、避免重复补同一 BV，并按实际可用候选计缓冲；不能通过取消 1 秒节流来提速。
- 同一请求是否可复用画像、惩罚和候选快照，减少重复 SQL／解码；跨请求缓存要有明确失效条件。
- 是否存在异常规模的全池排序、历史冷却扫描或逐条提交，是否需要有证据的索引、批量写入或候选裁剪；不能凭猜测改变排序结果。

## 8. 验证与验收

埋点测试必须证明：两个并发前台与后台计数互不串线；LRU 命中不算 HTTP；WBI nav 与失败请求不漏记；HTTP 内部重试的统计口径准确；候选冲突更新不是新增；补详情失败不是完成；缓存页状态来自真实分支；非阻塞锁忙不是等待；异常与空结果仍落日志；禁用来源字段完整；SQLite 统计覆盖 fetch／commit 和过滤服务独立连接，且没有业务驻留时间混入。

通过诊断开关关闭／开启的同输入对照，核实推荐结果、stream、冷却、记录数和反馈行为不变。用重复的本地样本量化埋点开销，若明显干扰被测场景，降低采样或修正埋点后重测。

诊断阶段的验收是“指标可靠、原因有证据、建议可实施”，不要求为了达标临时削减候选或降低过滤标准。若随后获准优化，建议将以下作为本地同条件目标，并在基线测完后确认其适用性：

- 热完整候选池、模型已热、无竞争时，新页生成 p95 ≤ 1 秒；缓存页重读 p95 ≤ 200 毫秒。
- 可用缓存足够时，同步 detail 网络请求为 0；同步召回若仍发生，必须说明必要性和实测成本。
- 前台 API、同步详情和等待次数不增加；默认档位来源比例及 v0.3.1 语义回归通过。
- 并发场景中缓存页不应仅因另一页同步网络请求而等待整个生成周期；冷池延迟不设脱离网络条件的统一阈值，但须展示同步等待预算及降级行为。

代码修改后运行相关诊断测试及原有回归；涉及前端再运行曝光脚本和生产构建。功能检查命令：

```powershell
.venv\Scripts\python.exe -W ignore -m unittest discover -s backend/tests -p "test_v0*.py" -v
cd frontend
node tests/telemetry.cjs
npm run build
```

最终交付建议路径：

- `docs/v0.3.1_feed_performance_report.md`：测量结果、关键路径、证据与建议。
- 项目本地忽略目录下的 `feed-performance/`：JSONL／CSV／复现参数，报告注明具体路径；原始诊断数据不加入 Git。
- 最小埋点、采样脚本及必要测试；优化实现另列变更，不与测量基线混淆。

收尾检查凭证未写入日志／Git，临时数据已清理，诊断默认关闭；保持未提交状态。
