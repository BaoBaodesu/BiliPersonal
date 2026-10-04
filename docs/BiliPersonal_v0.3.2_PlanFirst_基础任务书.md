# BiliPersonal v0.3.2 Plan-First 基础任务书

> 用途：提供给 Agent 在 **Plan 模式**下进行仓库审计、方案分析、风险讨论与阶段拆分。  
> 本轮只要求产出详细 Plan，不直接实施功能。  
> 目标是在 v0.3.1 多路候选源与 Feed 性能优化已经完成的基础上，继续提升 BiliPersonal 的个性化控制力、兴趣建模能力与首页推荐质量。

---

# 0. 当前背景

当前 BiliPersonal 已完成：

- v0.3 数据闭环、Label、Ranker、评估、版本追踪和回滚；
- v0.3.1 多路候选源：
  - `follow`
  - `up_archive`
  - `related`
  - `rcmd`
  - `hot`
- 来源混合、过滤、降权、陌生 UP 闸门、关注页；
- Ready Pool / Cache-first Feed；
- 普通新版 Feed 前台热路径零同步网络；
- Feed 热候选新页已达到亚秒级；
- 已保留经典首页回滚能力；
- 当前仍优先使用 SQLite，不做 PostgreSQL 迁移；
- v0.4 的封面视觉过滤暂不进入本轮。

v0.3.2 的主要目标：

> **让 BiliPersonal 更主动地理解“用户长期喜欢什么、最近暂时在看什么、哪些内容值得主动召回、首页第一屏应该优先展示什么”，并让用户继续保有明确的推荐/过滤控制权。**

---

# 1. Plan Mode 强制要求

Agent 首先只进入 **Plan Mode**。

本轮禁止：

- 不直接修改代码；
- 不执行数据库迁移；
- 不安装新依赖；
- 不训练新模型；
- 不修改当前生产数据；
- 不改变现有推荐来源配额；
- 不改变 Label 协议；
- 不修改曝光定义；
- 不激活新排序器；
- 不提交 Git；
- 不为了完成方案而假设不存在的接口或数据；
- 不把尚未验证的推测写成既定事实。

允许：

- 查看真实仓库；
- 查看现有数据库结构；
- 查看 v0.3 / v0.3.1 代码；
- 查看 Feed / Candidate / Mixer / Filter / Feedback / Affinity / Scheduler；
- 查看前端设置页、兴趣画像页、来源面板；
- 查看测试；
- 查看已有性能报告和 validation；
- 查看现有 Bilibili 接口封装；
- 查看现有缓存、stream、cursor、served / history 行为；
- 进行只读代码追踪和结构分析；
- 提出多个方案并比较优缺点。

最终只输出：

```text
v0.3.2_PLAN.md
```

计划经用户确认后再执行。

---

# 2. 本轮总体范围

v0.3.2 分为四个阶段：

```text
P0
重复候选修复
候选池从“打开网页后启动”改为“项目启动后开始准备”
推荐来源等现有四档选择 UI 从下拉框改为滑杆

P1
兴趣垂类召回
长期 / 短期兴趣
特别关注 UP
收藏行为语义改进

P2
推荐 / 探索 / 过滤力度控制
旧视频召回强度
Top Row Precision

P3
来源与兴趣分析面板
```

Agent 必须分别给出：

```text
需要查看
需要校验
需要修改
需要增加
需要执行
风险
验收
```

不能只给概念性建议。

---

# 3. 必须保持不变的基础语义

除非 Plan 明确指出现有实现存在 Bug，并经过用户确认，否则以下语义必须保持：

- `follow / up_archive / related / rcmd / hot` 多路候选结构；
- Source Mixer；
- 经典首页回滚；
- stream 不可变；
- 同一 stream 固定 model / experiment；
- 硬过滤立即生效；
- downrank 与 hard block 分离；
- 收藏与已看分离；
- 单次完播不直接升级为常看 UP；
- 常看 UP 判定规则沿用当前 v0.3.1；
- 关注页不限发布时间；
- 首页关注新作窗口沿用当前实现，除非本轮明确另行调整；
- Ready Pool / Cache-first；
- 热候选普通 Feed 不同步请求 Bilibili；
- Bilibili 一秒最小请求间隔；
- `-352 / 412` 风控退避；
- 真实 impression 定义；
- 现有反馈撤销；
- model_version 追踪；
- v0.3 训练/验证/回滚体系；
- v0.4 封面视觉过滤继续延后。

---

# 4. P0：重复候选修复

## 4.1 目标

解决以下问题：

- 单次刷新内重复视频；
- 连续换一批重复视频；
- 搜索关键词时重复视频明显增加；
- 同一个 BVID 从多个来源 / 多个 query 被重复插入；
- 同一视频因 source 不同而被当作多个候选；
- 搜索分页重复；
- 同一页面多个位置出现同一 BVID。

核心原则：

```text
BVID = 视频 canonical identity
```

同一 BVID 可以有多个来源 / 多个召回理由，但不能在同一个页面或不应重复的窗口中重复展示。

---

## 4.2 需要查看

Agent 必须检查：

- `CandidatePool` 当前唯一键；
- candidates 表唯一约束；
- `(source, bvid)` 是否仍作为主要身份；
- Feed 生成前去重；
- Mixer 去重；
- `served_videos`；
- `recommendation_history`；
- stream / cursor 去重；
- Search 当前候选存储与分页方式；
- 同一个 BVID 被：
  - `related`
  - `up_archive`
  - `follow`
  - `rcmd`
  - `hot`
  - 搜索
  同时召回时的处理；
- 搜索 query 是否各自保存独立候选副本；
- Candidate update / conflict 行为；
- source reason 是否只能保存单值。

---

## 4.3 需要校验

必须确认：

1. 当前重复发生在哪一层：
   - 抓取层；
   - 存储层；
   - 候选读取层；
   - Rank 层；
   - Mixer；
   - Feed；
   - 前端渲染。

2. 同 BVID 多来源时：
   - 是否重复占用配额；
   - 是否重复进入排序；
   - 是否重复写 `recommendation_history`；
   - 是否重复增加 served 次数。

3. Search：
   - 同 query 分页是否重复；
   - 多 query 是否重复；
   - 分区切换是否重复；
   - 前端是否会重复 append 相同 BVID。

4. 去重后：
   - 是否影响来源占比；
   - 是否影响解释理由；
   - 是否影响 Source Mixer 配额。

---

## 4.4 需要修改 / 增加

Agent 应讨论至少两种方案：

### 方案 A：候选层 BVID 唯一

```text
candidate
bvid = 唯一主体

sources = 多来源关系
reasons = 多召回理由
```

### 方案 B：保留 `(source, bvid)` 候选结构

但在进入 Rank / Mixer 前建立 canonical merge：

```text
same BVID
→ 合并来源
→ 合并召回理由
→ 只保留一个排序实体
```

需要比较：

- 数据迁移成本；
- 现有代码侵入程度；
- 来源统计准确性；
- Debug Explain 能力；
- 回滚难度。

---

## 4.5 验收要求

至少覆盖：

```text
同一 BVID 被 3 个来源召回
→ 页面只出现一次

同一 BVID 被多个 vertical query 搜到
→ 页面只出现一次

搜索分页
→ 同 query 内不重复

连续刷新
→ 按当前冷却规则不重复

来源解释
→ 仍能知道该视频由哪些来源召回
```

---

# 5. P0：候选池从项目启动后开始准备

## 5.1 目标

当前候选池准备不应依赖“用户打开网页后才开始”。

期望：

```text
项目启动
↓
登录态可用
↓
后台 Source Scheduler / Ready Pool 开始工作
↓
用户稍后打开网页
↓
已有完整候选可立即展示
```

而不是：

```text
用户打开网页
↓
才开始准备候选
↓
首页初期候选不足
```

---

## 5.2 需要查看

Agent 必须检查：

- `run.py` / Flask 启动流程；
- 登录态恢复；
- `warmup`；
- source scheduler 当前启动条件；
- 前端首次 API 请求是否触发后台启动；
- 用户未打开浏览器时 scheduler 是否运行；
- logout / login；
- app restart；
- Cookie / session 恢复；
- Ready Pool 低水位；
- scheduler stop；
- development mode / reloader 是否会重复启动线程；
- Windows 下 Flask 重载是否产生双 scheduler。

---

## 5.3 需要校验

必须确认：

- 无登录态启动时如何处理；
- 有持久登录态时何时启动；
- 登录成功后是否能立即启动；
- 登出后是否可靠停止；
- Flask debug/reloader 是否重复启动；
- CLI / test 环境是否误启动真实 scheduler；
- 项目刚启动时是否会产生过多 API 请求；
- 是否遵守一秒节流和风控；
- 项目长时间无人访问时是否需要降低后台频率；
- Ready Pool 已充足时是否停止主动补池。

---

## 5.4 需要修改 / 增加

Agent 应设计：

```text
Application Lifecycle
→ Auth Ready
→ Scheduler Start
→ Ready Pool Maintain
→ User Open Web
→ Feed Consume Ready Pool
```

并明确：

- start once；
- stop once；
- restart recovery；
- test disable；
- local dev reloader guard；
- 后台空闲策略；
- 长时间无前端访问时是否降低频率；
- 前台活跃后是否恢复标准补池频率。

---

## 5.5 验收

至少验证：

```text
启动项目后不打开网页
→ scheduler 正常启动
→ Ready Pool 增长

Ready Pool 充足
→ 不持续无意义扩展

登出
→ scheduler 停止

重新登录
→ scheduler 恢复

项目重启
→ 不重复创建 scheduler

测试环境
→ 不访问真实 Bilibili
```

---

# 6. P0：四档选择 UI 改为滑杆

## 6.1 目标

现有：

```text
关闭
仅兜底
少量
标准
```

语义暂时保持不变。

只把 UI 从下拉选择框调整为横向滑杆。

本阶段：

```text
不增加连续数值
不增加第五档
不改变后端配置语义
```

---

## 6.2 需要查看

- `SettingsPage.tsx`；
- 当前来源档位字段；
- API；
- `app_state sources:settings`；
- Select / Toggle 组件；
- 响应式布局；
- Light / Dark；
- 键盘操作；
- 移动端；
- 当前四档显示文本。

---

## 6.3 需要校验

- 滑杆是否严格映射 4 个离散值；
- 拖动过程中是否频繁发 PUT；
- 是否在 release 后提交；
- 键盘方向键；
- 无障碍 label；
- 手机触摸；
- 视觉是否能明确看到当前档位；
- 保存失败是否恢复；
- stream 设置快照是否仍从下一批生效。

---

## 6.4 验收

```text
四档值不变
后端 API 不变
刷新页面设置保持
移动端可用
键盘可调
切档后下一次换批生效
现有 stream 不改变
```

---

# 7. P1：兴趣垂类召回

## 7.1 目标

新增一种真正由 BiliPersonal 主动发起的候选来源：

```text
vertical_search
```

核心目标：

```text
用户兴趣画像
↓
生成兴趣方向
↓
生成查询
↓
主动召回视频
↓
过滤
↓
Rank
↓
Mixer
```

不是继续完全依赖：

```text
Bilibili 首页
热门
related
```

---

## 7.2 需要查看

- 当前 Search API；
- 搜索接口封装；
- 是否已有关键词搜索；
- `history_events`；
- 用户兴趣画像；
- category / tag；
- UP affinity；
- 用户点赞 / 收藏；
- 当前搜索历史；
- filter rules；
- CandidatePool；
- Source Mixer；
- Explain；
- Scheduler；
- Ready Pool；
- Bilibili 搜索分页、风控和频率。

---

## 7.3 需要校验

必须讨论：

- query 如何产生；
- 是否使用：
  - tag；
  - category；
  - UP；
  - 标题关键词；
  - 手动关注兴趣；
  - 用户搜索历史；
- 是否需要 query blacklist；
- query 是否会过度宽泛；
- query 是否会把短期兴趣放大；
- 不同 query 是否重复召回相同 BVID；
- query 多久刷新；
- 每个兴趣方向抓多少；
- 是否后台执行；
- 如何防止 search API 成为新性能瓶颈；
- 如何防止 search API 成为新风控风险；
- vertical_search 配额；
- 是否进入现有 Mixer；
- 是否默认开启。

---

## 7.4 需要增加

Agent 应设计：

```text
interest_topic
query
query_weight
query_source
last_used_at
cooldown
```

候选至少能追溯：

```text
source = vertical_search
interest_id
query_text / query_id
```

如果直接保存 query_text 有隐私或日志风险，需要说明。

---

# 8. P1：长期兴趣 / 短期兴趣

## 8.1 目标

禁止简单实现为：

```text
越新的观看历史
→ 权重越高
```

必须区分：

```text
Long-term Interest
Short-term Interest
```

短期兴趣可以快速上升，也应该自然衰减。

长期兴趣不应因为最近几周没看而消失。

---

## 8.2 需要查看

- `history_events`；
- watched_at；
- feedback_at；
- likes / favorites；
- search；
- UP affinity；
- current interest profile；
- 当前 category 统计；
- 是否已有时间窗口；
- 是否已有模型特征。

---

## 8.3 需要校验

必须分析：

### 长期兴趣可能来自

- 长期重复观看；
- 多个月份持续出现；
- 多次点赞；
- 多次投币；
- 多次收藏；
- 长期关注 UP；
- 特别关注；
- 主动搜索；
- 反复回看。

### 短期兴趣可能来自

- 最近 3～14 天集中观看；
- 突然增加的 search；
- 最近连续完播；
- 短期点击聚集；
- 临时事件。

必须防止：

```text
最近看了 10 个摩托车视频
→ 未来整个首页都变成摩托车
```

也要防止：

```text
过去很喜欢的题材
最近两周没看
→ 完全消失
```

---

## 8.4 Plan 必须讨论

至少比较：

### 方案 A

规则型双画像：

```text
long_term_score
short_term_score
```

### 方案 B

现有 Ranker 外增加 Interest Rerank

### 方案 C

作为 Ranker 新特征

本版本优先考虑：

```text
可解释
可回滚
不要求立刻重训模型
```

---

# 9. P1：特别关注 UP

## 9.1 目标

特别关注不能只等价于普通 follow。

需要作为强显式偏好。

但：

```text
特别喜欢
≠ 无限频繁出现
```

---

## 9.2 需要查看

- 是否能通过 Bilibili API 获取特别关注列表；
- followings 数据结构；
- affinity；
- up_archive；
- follow feed；
- 同 UP 每页上限；
- creator cooldown；
- 当前重复曝光。

---

## 9.3 需要校验

- API 是否真实可用；
- 特别关注状态如何持久化；
- 是否属于用户显式信号；
- 是否进入训练；
- 是否仅用于召回；
- 是否影响 Rank；
- 如何避免同一特别关注 UP 高频刷屏；
- 特别关注 UP 长期不投稿时是否：
  - 深挖旧作；
  - 搜索直播切片；
  - 搜索相关 Tag；
  - 搜索其他 UP 的相关视频。

本阶段不要求所有扩展一次实现，但 Plan 必须拆优先级。

---

## 9.4 建议讨论

增加：

```text
special_follow_bonus
creator_frequency_penalty
```

两者同时存在。

---

# 10. P1：收藏行为语义改进

## 10.1 目标

避免简单：

```text
favorite = 永久强兴趣
```

收藏仍然是强正向行为，但需要把：

```text
喜欢主题
喜欢作者
希望反复观看
暂时保存工具内容
```

分开讨论。

---

## 10.2 需要查看

- favorite 数据来源；
- fav_time；
- history；
- 收藏后是否再次观看；
- 是否存在收藏夹分类；
- 收藏视频是否可识别教程 / 娱乐；
- 当前 label；
- affinity；
- Candidate exclusion。

---

## 10.3 需要校验

必须区分：

### A. Replayable 收藏

例如：

- 精制长视频；
- 电台；
- 二创；
- 音乐；
- 反复观看内容。

### B. Utility 收藏

例如：

- 游戏教学；
- AI 软件教程；
- 课程；
- 编程学习；
- 数学课程。

### C. 行为型收藏

例如：

- 支持 UP；
- 收藏达标更新下一期；
- 一次性行为。

但本轮不允许在没有可靠证据时强制自动分类。

---

## 10.4 Plan 应优先考虑行为推断

例如：

```text
收藏
+
之后多次主动重看
→ replayable probability ↑
```

```text
收藏
+
长期没有再次访问
→ 主题仍为正反馈
→ 但重复推荐价值下降
```

要求：

```text
收藏对作者/主题兴趣的作用
与
收藏对重复推荐的作用

分开
```

---

# 11. P2：推荐 / 探索 / 过滤力度控制

## 11.1 目标

给用户更明确的推荐控制。

建议继续保持离散档位，不做无限连续参数。

至少讨论：

```text
个性化强度
探索强度
过滤强度
旧视频召回强度
```

---

## 11.2 需要查看

- 当前 hot / rcmd 四档；
- exploration；
- unknown UP limit；
- downrank；
- hard filter；
- old archive；
- Source Mixer；
- current settings；
- app_state；
- UI。

---

## 11.3 需要校验

必须明确：

每一个用户可调滑杆到底映射哪些内部参数。

禁止：

```text
“推荐力度 = 70”
```

但内部没有明确语义。

例如：

### 个性化强度

可能控制：

- exploitation ratio；
- vertical_search quota；
- followed / affinity bonus；
- rcmd / hot 比例。

### 探索强度

可能控制：

- unfamiliar UP；
- exploration slot；
- vertical query diversity。

### 过滤强度

可能控制：

- downrank 强度；
- 陌生 UP 质量阈值；
- 是否允许 borderline 内容。

但：

```text
用户明确 hard block
```

不能被“宽松过滤”覆盖。

---

# 12. P2：旧视频召回强度

## 12.1 目标

用户能够控制：

```text
更偏新内容
↔
更愿意看到旧内容
```

但不能简单按视频年龄统一处理。

---

## 12.2 需要查看

- up_archive；
- vertical_search；
- related；
- pubdate；
- 当前 old video 排序；
- 关注 UP；
- evergreen content。

---

## 12.3 需要校验

Agent 必须分析：

```text
资讯
教程
娱乐
音乐
二创
长视频
UP 代表作
```

的时间敏感性是否相同。

本轮不一定需要 AI 判断内容类型。

可以优先考虑：

```text
来源级 + 用户设置
```

而不是复杂模型。

---

# 13. P2：Top Row Precision

## 13.1 目标

首页第一行 / 前几个位置优先保证高置信推荐。

例如 12 条：

```text
Top 1~4
→ 高置信 exploitation

Middle
→ 正常排序

Bottom
→ exploration / unfamiliar
```

---

## 13.2 需要查看

- 当前 Mixer；
- 当前 Rank；
- exploration 插入位置；
- 页面列数；
- 响应式布局；
- 桌面和手机每行数量不同的问题；
- recommendation position；
- impression；
- CTR。

---

## 13.3 重要校验

不能假设：

```text
位置 1~4 永远等于第一行
```

因为：

```text
桌面 6 列
平板 4 列
手机 1~2 列
```

所以 Plan 必须讨论：

### 方案 A

定义固定：

```text
Top-K Precision
```

与视觉行无关。

### 方案 B

前端报告当前列数，后端按 row size 生成。

### 方案 C

只保证：

```text
前 N 个位置
```

本轮建议优先保持后端简单，不让推荐结果依赖客户端视口，除非有充分理由。

---

## 13.4 指标

至少考虑：

```text
Top4 CTR
Top4 Wanted Rate
Top4 Bad Rate

整体 CTR
整体 Wanted Rate
```

---

# 14. P3：来源与兴趣分析面板

## 14.1 目标

不是只做“漂亮统计图”。

面板需要帮助用户和开发者回答：

```text
最近为什么给我推这些？
我的兴趣画像是什么？
哪些来源最有效？
哪些来源让我最不喜欢？
长期兴趣和短期兴趣有什么不同？
```

---

## 14.2 需要查看

- 现有 7 天来源面板；
- recommendation_history；
- source；
- click；
- not_interested；
- block_up；
- impression；
- interest profile；
- settings page；
- debug panel。

---

## 14.3 建议增加

至少讨论：

### 来源分析

```text
source
曝光
点击
CTR
not_interested
block_up
Wanted proxy
```

### 长期兴趣

```text
Topic
Score
Evidence
Last active
```

### 短期兴趣

```text
Topic
Short-term boost
最近变化
预计衰减
```

### UP

```text
特别关注
常看
熟悉
普通
降权
屏蔽
```

### 搜索 / 垂类

```text
哪些 query 在主动召回
每个 query 贡献多少候选
点击表现
```

---

# 15. 数据结构审计要求

Plan 必须先审计现有表是否已经足够。

重点查看：

```text
candidates
recommendation_history
served_videos
feedback
filter_rules
interest_penalties
followings
history_events
app_state
model_runs
```

以及所有 v0.3.1 新表。

不要默认新建大量表。

如果增加数据结构，必须说明：

```text
为什么现有结构无法支持
是否可以增量迁移
是否需要回填
旧数据如何兼容
回滚如何处理
```

---

# 16. API / 风控审计

兴趣垂类召回、特别关注等功能可能增加 Bilibili 请求。

Agent 必须检查：

- 当前可用搜索接口；
- 特别关注数据是否可取；
- API 风控；
- WBI；
- 分页；
- retry；
- Ready Pool；
- Scheduler；
- 每小时新增请求预算；
- 是否能后台执行；
- 是否会重新把外部 API 放回 Feed 前台路径。

强制原则：

```text
普通热 Feed
不得因为 v0.3.2 新功能
重新出现十秒级同步等待
```

---

# 17. 性能约束

v0.3.2 必须继承 v0.3.1 Feed 性能成果。

目标继续保持：

```text
热完整候选新页：
p95 ≤ 1s

缓存页：
p95 ≤ 200ms

普通热 Feed：
同步 detail HTTP = 0
同步候选召回 = 0
```

兴趣垂类搜索应优先：

```text
后台召回
↓
进入 Ready Pool
↓
Feed 消费
```

禁止：

```text
用户换一批
→ 临时生成 query
→ 同步搜索 Bilibili
→ 等待网络
```

除非是用户明确进行“搜索”操作。

---

# 18. 推荐质量验收

v0.3.2 不只看：

```text
API 成功
没有报错
```

需要设计：

### 工程指标

- 重复率；
- Ready Pool；
- 短页率；
- 来源占比；
- search source 占比；
- Feed 延迟；
- API 请求量；
- 风控率。

### 推荐质量

- Wanted Rate；
- Bad Rate；
- CTR；
- not_interested；
- block_up；
- Top-K / Top4；
- 来源 CTR；
- 长期兴趣覆盖；
- 短期兴趣过度占领比例。

---

# 19. 测试要求

Plan 必须列出需要新增的自动测试。

至少包含：

## P0

```text
多来源同 BVID 去重
搜索同 BVID 去重
分页去重
连续 refresh 去重
来源理由合并
```

## Scheduler

```text
项目启动自动准备
无浏览器访问仍补池
登出停止
登录恢复
重复启动保护
测试环境不访问真实 Bilibili
```

## UI

```text
4 档滑杆映射
键盘
触摸
保存失败
stream 不变
```

## P1

```text
长期兴趣稳定
短期兴趣上升/衰减
特别关注权重
Creator frequency penalty
收藏后重看语义
收藏未重看不等于负反馈
vertical query 去重
```

## P2

```text
探索档位
过滤档位
旧视频档位
Top-K Precision
hard block 不被宽松设置覆盖
```

## P3

```text
面板统计分母
无曝光时为空
source 分组正确
long / short interest 展示一致
```

---

# 20. 必须明确不做

本轮明确不执行：

```text
封面视觉模型
AI 封面识别
AI 配音识别
AI 洗稿识别
LLM 主 Feed 实时判定
Agent 对话推荐
Token / RMB 记账
LUFS 分析
Two-Tower
pgvector
PostgreSQL
移动 App
多用户系统
大规模 UI 重构
```

这些可以作为 v0.3.3 / v0.4 后续方向。

---

# 21. Agent 必须回答的问题

最终 Plan 至少回答：

1. 当前重复视频的真实根因是什么？
2. 最适合在哪一层做 canonical BVID 去重？
3. 多来源同 BVID 如何保留来源解释？
4. Search 重复如何处理？
5. Scheduler 当前为何依赖前端打开？
6. 项目启动后如何安全自动启动候选准备？
7. 如何避免 Flask reload 导致双 scheduler？
8. 四档滑杆如何保持后端语义完全不变？
9. vertical_search 的 query 从哪里来？
10. query 如何防止短期兴趣过度放大？
11. vertical_search 如何进入 Ready Pool？
12. 如何避免 vertical_search 重新阻塞 Feed？
13. 长期兴趣与短期兴趣如何分别计算？
14. 两者如何融合？
15. 是否需要重训模型？如果不需要，如何先以规则层实施？
16. 特别关注 UP 的 API / 数据是否真实可获取？
17. 如何提高特别关注权重但避免刷屏？
18. 收藏如何拆成作者兴趣、主题兴趣和重复推荐价值？
19. 推荐 / 探索 / 过滤滑杆各自映射什么？
20. hard block 如何保证永远优先于“宽松过滤”？
21. 旧视频召回强度如何实现，不把所有旧视频一视同仁？
22. Top Row Precision 最适合定义成 Top-K 还是视觉第一行？
23. 来源与兴趣面板需要哪些可解释数据？
24. 哪些数据结构必须新增？
25. 哪些现有表可以直接复用？
26. 哪些 Bilibili API 会增加调用量？
27. 如何保持 v0.3.1 Feed 性能指标？
28. 每个 Phase 的回滚方案是什么？
29. 每个 Phase 如何独立验收？
30. 哪些内容建议延期到 v0.3.3 / v0.4？

---

# 22. Plan 输出结构

最终 `v0.3.2_PLAN.md` 建议按：

```text
1. 仓库现状
2. 当前真实问题
3. 数据结构审计
4. P0 方案
5. P1 方案
6. P2 方案
7. P3 方案
8. API / 风控
9. 性能影响
10. 推荐质量影响
11. 测试
12. 数据迁移
13. 回滚
14. 实施顺序
15. 风险
16. 验收
17. 延后项
```

---

# 23. 四类清单

最终必须整理：

## A. 必须执行

当前预期：

- 重复候选修复；
- 项目启动候选准备；
- 四档 Slider UI；
- 兴趣垂类召回基础框架；
- 长 / 短期兴趣基础建模；
- 特别关注 UP；
- 收藏语义拆分基础；
- 来源 / 兴趣可解释数据。

## B. 建议执行

- Creator frequency penalty；
- Top-K Precision；
- 旧视频召回强度；
- 用户可调探索；
- 用户可调过滤强度；
- vertical_search query 分析。

## C. 有数据后决定

- vertical_search 默认配额；
- 长短期兴趣融合权重；
- 特别关注 bonus；
- 收藏 replayable 阈值；
- Top4 / Top6 哪个更适合作为首页重点指标；
- 是否把兴趣特征正式加入 Ranker。

## D. 本轮不执行

- 视觉模型；
- LLM 主 Feed 实时决策；
- AI 内容识别；
- LUFS；
- Embedding / Two-Tower；
- PostgreSQL；
- App。

---

# 24. 实施建议顺序

Agent 在 Plan 中应给出详细顺序，建议基础方向：

```text
Phase 0
仓库审计 / 数据审计 / API 探针

Phase 1
P0 重复修复
P0 项目启动候选准备
P0 Slider UI

Phase 2
长期 / 短期兴趣数据层

Phase 3
兴趣垂类召回

Phase 4
特别关注 UP
收藏行为语义

Phase 5
推荐 / 探索 / 过滤控制
旧视频召回
Top-K Precision

Phase 6
来源与兴趣分析面板

Phase 7
真实使用验收
```

每个 Phase：

```text
单独修改
单独测试
单独验证
允许回滚
通过后再进入下一阶段
```

---

# 25. 本轮结束条件

Agent 在完成：

```text
查看
校验
分析
方案比较
风险讨论
阶段拆分
验收设计
```

后停止。

不要开始实施。

最终只输出：

```text
v0.3.2_PLAN.md
```

等待用户批准。
