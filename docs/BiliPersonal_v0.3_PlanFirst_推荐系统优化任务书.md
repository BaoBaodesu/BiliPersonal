# BiliPersonal v0.3 推荐系统优化任务书（Plan-First）

> 本任务书用于下一阶段推荐系统优化。  
> **第一阶段只允许 Agent 进入 Plan 模式：先审计当前项目、确认真实状态、梳理“要做 / 不做 / 延后做”的内容，不得直接修改代码。**

---

# 0. v0.3 的定位

v0.2 / v0.2.1 已经完成的方向主要是：

```text
React Web
Feed Engine
缓存
去重
反馈
过滤
推荐历史
模型缓存
候选池
SQLite 持久化
```

v0.3 不再以 UI 和产品功能为主。

v0.3 的核心目标：

> **提升推荐数据质量、训练方式、标签构造、评估方式和最终排序质量。**

本阶段优先解决已经确认的问题：

```text
训练样本过少
Label 构造不合理
训练集自评导致 AUC 虚高
Quality Score 饱和
反馈数据没有真正参与推荐学习
历史数据没有时间衰减
推荐结果缺少多样性控制
候选池与排序之间缺少更清晰的分层
```

---

# 1. 第一阶段：Plan Mode（只分析，不执行）

Agent 首先必须进入 Plan 模式。

## 1.1 Plan 模式禁止事项

在 Plan 阶段：

**禁止：**

```text
修改任何源码
修改 SQLite schema
写入数据库
安装新依赖
修改 requirements
重新训练模型
删除缓存
删除历史
改前端
改 API
创建迁移
commit
push
```

允许：

```text
读取代码
读取配置
读取数据库 schema
读取非敏感样本数据
统计数量
检查现有模型产物
检查日志
分析调用链
分析历史数据格式
分析 feedback / recommendation_history 数据
分析候选池
输出方案
```

如果 Plan 阶段发现必须执行某条命令才能确认信息：

只允许执行**只读命令**。

---

# 2. Plan 阶段必须先确认当前真实项目状态

不要根据旧任务书假设当前项目一定长什么样。

必须实际检查。

## 2.1 Git 状态

确认：

```text
当前 branch
当前 commit
是否有未提交修改
是否已有 v0.2 / v0.2.1 commit
```

输出：

```text
branch:
HEAD:
dirty files:
```

如果工作区不是 clean：

只记录。

Plan 阶段不要自动提交。

## 2.2 当前目录结构

重点确认是否存在：

```text
backend/
frontend/
legacy/
saved_model/
data/
docs/
```

以及实际后端入口。

不要假设文件名完全一致。

## 2.3 当前推荐核心

重点找到并阅读：

```text
recommendation_service.py
training_service.py
Recommender.py
model.py
feature processor
candidate service
feedback service
filter service
database layer
```

如果文件路径不同，以实际代码为准。

必须画出当前真实调用链：

```text
Feed Request
↓
候选池
↓
过滤
↓
推荐模型
↓
排序
↓
去重
↓
Feed Cache
↓
React
```

并标明：

```text
每一步所在文件
关键函数
输入
输出
```

---

# 3. Plan 阶段必须检查当前数据库

当前已知历史上有：

```text
candidates
feed_cache
served_videos
recommendation_history
feedback
blocked_ups
blocked_keywords
app_state
```

但 v0.2.1 可能已经增加：

```text
filter_rules
```

或者其他表。

因此必须读取当前真实 schema，并输出：

```text
表名
行数
主键
索引
主要字段
```

重点：

```text
feedback
recommendation_history
served_videos
candidates
```

---

# 4. Plan 阶段必须确认训练数据来源

必须回答：

> 当前模型到底使用哪些数据训练？

例如：

```text
historyVideo.json
SQLite
收藏
历史
feedback
recommendation_history
```

必须明确：

```text
真实参与训练的数据
只是存着但没参与训练的数据
```

## 4.1 统计训练数据

必须输出：

```text
训练样本总数
正样本数
负样本数
正负比例
唯一 BVID
唯一 UP
唯一 Tag
历史时间范围
```

如果当前还是约 30 条，必须明确指出。

---

# 5. Plan 阶段必须分析 Label

找到当前 Label 逻辑。

例如旧版：

```python
interest_score = max(interaction_score / 2, progress_ratio)
label = 1 if interest_score > 0.5 else 0
```

必须回答：

```text
点赞如何影响
收藏如何影响
投币是否参与
观看进度如何影响
点击是否参与
不感兴趣是否参与
block_up 是否参与
秒退是否参与
```

## 5.1 必须生成 Label 风险报告

至少检查：

```text
收藏但未点赞
点赞但未收藏
看完但未互动
观看一半
短时间退出
明确 not_interested
明确 block_up
```

分别会得到什么 Label。

如果存在：

```text
收藏 → 负样本
```

必须明确标记为 P0。

---

# 6. Plan 阶段必须检查 Quality 特征

确认当前是否仍使用：

```python
quality_score
```

并检查：

```text
min
max
mean
std
unique count
```

如果大量为：

```text
1.0
```

必须确认 Wide 分支是否仍然失效。

## 6.1 必须检查模型输入特征

输出：

```text
Tag
Author
Quality
其他字段
```

并说明：

```text
哪些字段真的进入模型
哪些字段只是存在 JSON 中但没有参与
```

---

# 7. Plan 阶段必须检查评估方式

确认：

```text
训练集
验证集
测试集
```

是否存在。

如果：

```text
Train = Eval
```

必须列为 P0。

## 7.1 必须检查模型保存逻辑

确认当前 best model 依据：

```text
training loss
validation loss
其他指标
```

不能根据旧报告猜。

---

# 8. Plan 阶段必须检查现有 feedback 是否可用于训练

统计：

```text
click
not_interested
block_up
like
watched
watch_later
其他 action
```

分别有多少条。

明确当前：

```text
哪些反馈只影响过滤
哪些反馈已经参与排序
哪些反馈可以纳入 v0.3 训练
```

---

# 9. Plan 阶段必须检查 recommendation_history

分析当前是否已经记录：

```text
曝光
排名
rating
feed_type
clicked
feedback
model_version
served_at
```

必须判断它能否作为：

```text
曝光日志
```

用于训练。

---

# 10. Plan 阶段必须检查候选池

统计：

```text
hot 候选
rcmd 候选
其他 source
```

并回答：

```text
候选从哪里来
多久刷新
候选池大小
重复率
失效策略
未知 Tag 如何处理
探索内容如何插入
```

---

# 11. Plan 阶段必须检查 Feed 最终重排

确认当前是否存在：

```text
多样性重排
UP 去重
Tag 去重
分区去重
探索比例
新鲜度
时间衰减
```

不要假设。

---

# 12. Plan 阶段最后必须输出“四类清单”

Agent 必须把所有发现分成：

## A. v0.3 必须执行

只放：

```text
会直接影响推荐质量
当前已经证实存在问题
实现成本可控
不会引入过多变量
```

## B. v0.3 建议执行

不是硬阻塞，但收益明显。

## C. 延后到 v0.4+

例如：

```text
Embedding
pgvector
Two-Tower
PostgreSQL
多用户
移动端
```

## D. 明确不要执行

必须说明原因。

---

# 13. v0.3 推荐范围（默认建议）

除非 Plan 审计证明当前代码已经完成某一项，否则 v0.3 默认只做以下内容。

---

# 14. P0：扩大训练历史

目标：

```text
30
→
至少 500
```

推荐目标：

```text
1000~2000
```

不要一次无限制抓全部历史。

优先：

```text
最近 N 条
```

## 14.1 历史数据结构化

不要长期继续只靠：

```text
historyVideo.json
```

Plan 阶段需要判断是否应该新增：

```text
history_events
```

或者等价表。

建议字段：

```text
bvid
watched_at
progress
duration
isliked
isfaved
coined
source
created_at
```

但是否新建表必须经过 Plan 判断。

---

# 15. P0：重做兴趣分数 / Label

不要继续只做：

```text
max(...)
→ 0 / 1
```

建议改成连续 feedback score。

默认参考：

```text
block_up            -5
not_interested      -5
短时间退出           -3
观看 <20%           -2
观看 20~50%          0
观看 50~80%         +1
观看 >80%           +2
完播                 +3
click                +1
like                 +3
coin                 +4
favorite             +4
```

但最终权重必须基于 Plan 对当前真实数据字段的检查后确定。

---

# 16. P0：Train / Validation 时间切分

不要优先随机切分。

推荐：

```text
较早 80%
→ Train

最近 20%
→ Validation
```

理由：

```text
推荐系统真正关心：
过去能否预测未来
```

## 16.1 模型保存

改为：

```text
最低 Validation Loss
```

或者更合适的验证指标。

不再使用：

```text
最低 Training Loss
```

作为唯一依据。

---

# 17. P0：真实反馈进入推荐学习

必须评估是否把：

```text
click
not_interested
block_up
```

纳入训练。

注意：

```text
block_up
```

更像长期强负反馈。

不要仅当作 UI 过滤。

---

# 18. P1：修复 Quality 特征

不再把所有质量信息压成容易饱和的：

```text
quality_score ∈ [0,1]
```

建议拆成：

```text
log_views
like_rate
favorite_rate
coin_rate
reply_rate
age_hours
```

实际字段以当前候选数据为准。

---

# 19. P1：时间衰减

加入：

```text
recent_weight
```

默认参考：

```text
7 天以内       1.00
30 天以内      0.85
90 天以内      0.65
180 天以内     0.45
更早           0.25
```

Plan 必须先确认：

```text
历史 API 是否有可靠时间字段
```

没有可靠时间字段就不要硬做。

---

# 20. P1：多样性重排

推荐模型算出：

```text
raw ranking
```

之后再做轻量重排。

目标：

```text
减少同 UP
减少同 Tag
减少同分区扎堆
```

不要改变模型本体。

## 20.1 默认约束建议

例如一页 12 个：

```text
同一 UP ≤ 2
同一主 Tag 不要连续大量出现
探索内容保留一定比例
```

最终策略由 Plan 阶段根据当前 Feed 结构决定。

---

# 21. P1：候选池质量

不要只追求：

```text
候选数量更多
```

必须统计：

```text
有效候选比例
过滤后剩余
模型可评分比例
最终推荐比例
```

---

# 22. v0.3 明确不做 Embedding

除非 Plan 发现项目实际上已经依赖 Embedding。

否则：

```text
Qwen Embedding
BGE-M3
pgvector
FAISS
Two-Tower
```

全部延后到：

```text
v0.4
```

原因：

v0.3 先修：

```text
数据
Label
Validation
Feedback
Quality
Ranking
```

否则 Embedding 上线后无法判断提升来自哪里。

---

# 23. v0.3 不换 PostgreSQL

继续：

```text
SQLite
```

除非 Plan 阶段发现：

```text
当前 SQLite 已经成为真实性能瓶颈
```

仅仅因为：

```text
训练数据从 30 → 2000
```

不是换 PostgreSQL 的理由。

---

# 24. v0.3 不大改 React

前端只允许做算法调试相关的最小修改：

```text
显示 model_version
显示推荐原因
显示 Debug Score
显示训练状态
```

不要再做：

```text
大规模 UI 重构
新主题
新布局
移动端 App
```

---

# 25. v0.3 不接 LLM 内容审核

已有：

```text
hard block
keyword
blocked UP
```

足够。

LLM Filter 延后。

---

# 26. v0.3 推荐评估体系

不能只看：

```text
Loss
AUC
```

至少同时保留：

```text
Validation Loss
AUC-ROC
Average Precision
Precision@K
Recall@K
```

另外增加产品指标：

```text
人工评分 3/2/1/0/-1
点击率
不感兴趣率
block_up 率
重复推荐率
```

---

# 27. 人工评分继续保留

推荐：

```text
3 = 很想看
2 = 感兴趣
1 = 一般
0 = 不感兴趣
-1 = 明显不喜欢
```

v0.3 发布前后分别抽样：

```text
至少 50~100 条
```

对比：

```text
平均分
3 分比例
≥2 分比例
0 分比例
-1 分比例
```

---

# 28. A/B 对比

必须保留：

```text
v0.2 model
v0.3 model
```

至少开发阶段可切换。

不要覆盖后就无法回滚。

建议：

```text
model_version
```

明确写入：

```text
recommendation_history
```

---

# 29. 模型版本

推荐：

```text
baseline-v0.2
ranker-v0.3
```

每条推荐记录：

```text
model_version
```

必须真实可追溯。

---

# 30. Plan 阶段必须给出数据库改造建议

只提出方案。

不要执行。

必须回答：

```text
是否需要 history_events
是否需要 training_samples
是否需要 model_runs
是否需要新增索引
现有 feedback 是否需要扩字段
recommendation_history 是否足够做曝光日志
```

---

# 31. 如果需要新增表

默认可考虑：

```text
history_events
model_runs
```

但：

```text
training_samples
```

不一定需要永久保存。

如果可以训练时动态生成，就不要制造冗余表。

---

# 32. model_runs 建议

如果 Plan 认为有必要：

```text
id
model_version
started_at
finished_at
train_samples
validation_samples
positive_samples
negative_samples
train_loss
validation_loss
auc
average_precision
precision_at_k
recall_at_k
config_json
```

方便后续比较。

---

# 33. 训练流程目标

v0.3 理想流程：

```text
Bilibili History
+
收藏 / 点赞 / 投币
+
BiliPersonal Feedback
        ↓
数据清洗
        ↓
时间权重
        ↓
Interest Score
        ↓
Train / Validation
        ↓
Model Training
        ↓
Validation Evaluation
        ↓
Best Model
        ↓
Candidate Ranking
        ↓
Diversity Rerank
        ↓
Feed
```

---

# 34. 执行阶段建议拆分

Plan 通过后，执行阶段再拆。

## Phase 1

```text
训练数据扩容
历史数据结构化
统计报告
```

## Phase 2

```text
Label / Interest Score
真实 Feedback 合并
```

## Phase 3

```text
Train / Validation
Model Run Metrics
Best Validation Model
```

## Phase 4

```text
Quality Features
```

## Phase 5

```text
Diversity Rerank
```

## Phase 6

```text
A/B
人工评价
回归测试
```

---

# 35. 每个 Phase 后都必须可回滚

不要一次完成全部再测。

每个阶段：

```text
测试
记录指标
确认无回归
再继续
```

---

# 36. v0.3 验收标准

最终至少满足：

```text
[ ] 训练样本 ≥ 500
[ ] 数据来源清楚可追溯
[ ] 收藏不会再被错误视为负样本
[ ] not_interested 进入负反馈
[ ] block_up 进入强负反馈或等价机制
[ ] Train / Validation 分离
[ ] 模型保存依据不再是纯 Training Loss
[ ] Quality 特征不再全部饱和
[ ] Validation 指标真实存在
[ ] recommendation_history 写入真实 model_version
[ ] 原 v0.2 模型可回滚
[ ] Feed 缓存 / 去重 / 过滤不被破坏
[ ] -352 fallback 不被破坏
[ ] SQLite 继续可用
[ ] React 主 UI 不大改
[ ] 推荐人工评分完成前后对比
```

---

# 37. v0.3 成功标准

不要用：

```text
AUC = 1.0
```

作为成功。

真正成功标准：

```text
3 分视频更多
≥2 分视频更多
0 分减少
-1 分减少
不感兴趣率下降
block_up 率下降
```

同时：

```text
Feed 稳定性不能变差
```

---

# 38. v0.3 失败标准

如果出现：

```text
模型指标更好
但人工推荐更差
```

视为失败。

如果：

```text
推荐更准
但重复率明显上升
```

也不能视为完全成功。

---

# 39. Plan Mode 最终交付格式

Agent 在任何代码修改之前，必须先提交一份：

```text
v0.3_PLAN.md
```

内容必须包含：

## 39.1 当前项目状态

```text
Git
目录
数据库
模型
Feed
Feedback
```

## 39.2 当前问题

按：

```text
P0
P1
P2
```

排序。

每项必须附：

```text
证据
代码位置
数据证据
```

## 39.3 必须执行

每项：

```text
目标
涉及文件
涉及表
风险
预计影响
验证方法
```

## 39.4 建议执行

同上。

## 39.5 延后执行

例如：

```text
Embedding
PostgreSQL
pgvector
Android
iOS
LLM
```

必须说明为什么延后。

## 39.6 明确不执行

必须列出来。

## 39.7 数据库方案

明确：

```text
哪些表保留
哪些表新增
哪些字段新增
哪些索引新增
哪些完全不动
```

## 39.8 推荐模型改造方案

必须说明：

```text
当前模型
保留什么
修改什么
不修改什么
```

## 39.9 训练数据方案

明确：

```text
来源
数量
时间范围
去重
权重
Label
```

## 39.10 Validation 方案

明确：

```text
切分方法
指标
best model 规则
```

## 39.11 回归测试

至少包含：

```text
登录
Feed
换一批
去重
缓存
反馈
过滤
推荐历史
模型加载
-352 fallback
```

## 39.12 执行顺序

必须分 Phase。

---

# 40. Plan 阶段结束条件

Agent 输出：

```text
v0.3_PLAN.md
```

之后：

**停止。**

不要开始改代码。

等待人工确认：

```text
批准执行
修改范围
删除某项
增加某项
```

之后才进入执行阶段。

---

# 41. 给 Agent 的最终指令

```text
现在只进入 Plan Mode。

先基于当前仓库真实代码、数据库和模型实现完成审计。

不要修改任何文件，不要迁移数据库，不要安装依赖，不要重新训练，不要 commit。

先输出 v0.3_PLAN.md。

重点判断：
1. 哪些内容已经存在，不需要重复开发；
2. 哪些问题确实存在，必须解决；
3. 哪些内容应该放在 v0.3；
4. 哪些内容应该延后到 v0.4；
5. 哪些内容明确不应该执行。

计划必须基于当前项目实际状态，而不是旧任务书假设。

Plan 输出后停止，等待确认。
```
