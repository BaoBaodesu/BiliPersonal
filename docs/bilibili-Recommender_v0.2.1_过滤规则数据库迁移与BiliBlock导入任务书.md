# bilibili-Recommender v0.2.1 过滤规则数据库迁移与旧 BiliBlock 规则导入任务书

## 一、任务目标

当前 SQLite 已有 8 张表：

| 表 | 当前职责 |
| --- | --- |
| `candidates` | 候选池 |
| `feed_cache` | Feed 分页缓存 |
| `served_videos` | 已展示去重 |
| `recommendation_history` | 推荐流水 |
| `feedback` | 用户反馈 |
| `blocked_ups` | 精确屏蔽 UP（mid + name） |
| `blocked_keywords` | 旧关键词屏蔽 |
| `app_state` | 状态键值 |

本任务不要重做数据库，不删除现有数据。

目标是把旧 BiliBlock 中的大量“标题关键词”和“UP 主名称关键词”正式迁入当前 SQLite，并把过滤能力升级成可长期维护的规则系统。

需要完成：

1. 备份现有 SQLite。
2. 新增统一过滤规则表 `filter_rules`。
3. 保留现有 `blocked_ups`，继续负责按 `mid` 精确屏蔽 UP。
4. 将现有 `blocked_keywords` 数据迁移为标题规则。
5. 导入本任务提供的 65 条标题关键词。
6. 导入 32 条 UP 主名称关键词。
7. 统一做 trim、大小写归一化、去重。
8. 所有本次导入规则默认 `hard_block`。
9. Feed 生成前执行规则过滤。
10. 规则修改后立即使缓存失效，避免旧 Feed 继续出现已屏蔽内容。
11. 设置页支持标题关键词 / UP 关键词 / 精确屏蔽 UP 三类管理。
12. 增加命中次数和最近命中时间。
13. 不修改当前推荐模型结构与评分公式。

---

# 二、重要区分

当前已经有：

```text
blocked_ups(mid, name)
```

这个表用于：

```text
精确屏蔽某一个已知 UP
```

不要把下面的“UP 主关键词”直接塞进 `blocked_ups`。

例如：

```text
数码
推荐
教学
张雪峰
```

它们是“UP 名称包含关键词即屏蔽”，并不一定对应唯一 mid。

因此：

```text
blocked_ups
= exact UP block

filter_rules(target_type='uploader')
= UP 名称关键词过滤
```

两者并存。

---

# 三、数据库迁移原则

执行迁移前：

1. 停止 Flask 后端或确保没有写事务。
2. 找到当前真实 SQLite 文件路径。
3. 创建时间戳备份，例如：

```text
app.db
app.backup-YYYYMMDD-HHMMSS.db
```

4. 使用事务执行 schema + 数据迁移。
5. 任一步失败必须 rollback。
6. 不删除：
   - `recommendation_history`
   - `feedback`
   - `served_videos`
   - `feed_cache`
   - `candidates`
   - `blocked_ups`
7. 暂时不要 drop `blocked_keywords`，先保留兼容。

---

# 四、新增 `filter_rules`

建议 schema：

```sql
CREATE TABLE IF NOT EXISTS filter_rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    target_type TEXT NOT NULL,
    -- title / uploader / tag

    keyword TEXT NOT NULL,
    keyword_norm TEXT NOT NULL,

    match_mode TEXT NOT NULL DEFAULT 'contains',
    -- contains / exact / regex

    action TEXT NOT NULL DEFAULT 'hard_block',
    -- hard_block / downrank

    enabled INTEGER NOT NULL DEFAULT 1,

    source TEXT NOT NULL DEFAULT 'manual',
    -- manual / legacy_blocked_keywords / biliblock_import / system

    hit_count INTEGER NOT NULL DEFAULT 0,
    last_hit_at TEXT,

    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
```

唯一索引：

```sql
CREATE UNIQUE INDEX IF NOT EXISTS idx_filter_rules_unique
ON filter_rules(target_type, keyword_norm, match_mode);
```

查询索引：

```sql
CREATE INDEX IF NOT EXISTS idx_filter_rules_enabled_target
ON filter_rules(enabled, target_type);
```

---

# 五、关键词归一化

导入前统一：

```python
keyword = keyword.strip()
keyword_norm = keyword.casefold()
```

必须：

- 去除前导/尾部空格。
- 去除空字符串。
- 同一 `target_type + keyword_norm + match_mode` 去重。
- 保留 `keyword` 原始显示文本。
- 匹配时字符串同样使用 `casefold()`。
- 中文正常按包含关系匹配。
- 英文大小写不敏感。

旧数据中：

```text
蓝牙耳机推荐
蓝牙耳机推荐[尾部空格]
```

必须合并成一条。

---

# 六、迁移现有 `blocked_keywords`

读取当前 `blocked_keywords`：

```text
id keyword enabled created_at
```

全部迁入：

```text
target_type = 'title'
match_mode = 'contains'
action = 'hard_block'
source = 'legacy_blocked_keywords'
enabled = 原 enabled
```

使用 INSERT OR IGNORE / upsert，不能产生重复规则。

迁移后：

- 旧表暂时保留。
- 新代码以 `filter_rules` 为主。
- 如果旧 API 仍被前端调用，做兼容层，不要同时维护两套独立数据源。

---

# 七、本次导入数据

## 7.1 标题关键词

共 **65 条去重后规则**。

全部导入：

```text
target_type = title
match_mode = contains
action = hard_block
enabled = 1
source = biliblock_import
```

数据：

```text
你长大了 | 大一 | 以防你不知道 | 大冰 | 揭露 | 本质 | 训狗 | 建议收藏 | 为啥 | 风俗简史 | 智商税 | 小伙 | 鬼叔 | 感谢您 | 寻找童年 | 食录 | 良子 | 我真的很需要 | 为什么现在的 | 事态升级 | 大型纪录片 | 徐静雨 | 瓶子 | 只有我一个人觉得 | 补档 | 爆典 | 坚持不下去 | 直播切片 | 骗局 | 张雪峰 | vlog | 通俗易懂 | 终于把 | 蓝牙耳机推荐 | 降噪全能选手 | 高颜值高性价比 | 学生党 | 考公 | 广告 | 挑战 | 推广 | 真相 | 演都不演 | 曼波 | 哈基米 | 被网暴 | 为什么很多人 | 指南 | epz | 游戏耳机 | 开放式耳机 | 国补 | 以防你没有 | 假期 | 劳动法 | 考研 | 上岸 | 程序员 | 绘画 | 理想 | 为什么很少 | 百元神器 | 电竞神器 | 留学 | 日语教程
```

---

## 7.2 UP 主名称关键词

共 **32 条规则**。

全部导入：

```text
target_type = uploader
match_mode = contains
action = hard_block
enabled = 1
source = biliblock_import
```

数据：

```text
记录生活 | 大冰 | 疯话 | 真相 | 赛博食录 | 鸭魂 | 文化遗产 | 平台特惠 | 浪子 | 小岳来了 | 食来话长 | 在生活 | 子文又饿了 | 会魔法的头哥 | 差评君 | 王师傅の日记 | 罐头哥 | 咸鱼梦想家 | 桥半舫 | Java面试分享官 | AI绘画 | ai动画 | 李什么闯啊 | 推荐 | 教学 | 说科技 | 数码 | 码士集团 | Python教程 | 是莫叔吗 | 马督工 | 张雪峰
```

---

# 八、不要擅自弱化规则

虽然其中一些词比较宽，例如：

```text
广告
挑战
指南
程序员
绘画
推荐
教学
数码
```

但这是用户以前已经实际使用过的强屏蔽列表。

因此本次：

```text
全部按 hard_block 导入
```

不要擅自改成 downrank。

数据库只需要预留：

```text
action = downrank
```

供用户以后手动调整。

---

# 九、过滤顺序

Feed 生成时按照：

```text
候选视频
↓
精确 blocked_ups(mid)
↓
filter_rules: target_type=uploader
↓
filter_rules: target_type=title
↓
未来的 tag rules
↓
推荐模型 Rating / 排序
↓
served_videos 去重
↓
Feed
```

对于 `hard_block`：

```text
命中任何一条
→ 直接不进入最终 Feed
```

不要让模型 Rating 覆盖 hard block。

---

# 十、匹配字段

标题：

```python
video["title"]
```

UP：

优先使用稳定字段：

```python
video["author"]
```

如果候选 JSON 后续存在：

```text
owner.name
```

统一在 service 层先标准化，不要在过滤器里写多套字段判断。

---

# 十一、命中统计

每条 `filter_rules`：

```text
hit_count
last_hit_at
```

当视频因为该规则被实际过滤时：

```text
hit_count += 1
last_hit_at = CURRENT_TIMESTAMP
```

注意：

同一个 Feed 生成过程中，同一个视频命中同一条规则只记 1 次。

如果一个视频同时命中多个规则：

```text
每条命中规则各 +1
```

允许。

---

# 十二、不要删除候选池数据

规则命中后：

```text
不要从 candidates 删除视频
```

原因：

以后用户可能关闭某条规则。

正确方式：

```text
candidates
保持完整

Feed 生成阶段
动态过滤
```

---

# 十三、规则修改后的缓存处理

这是必须做的。

以下操作发生后：

```text
新增规则
删除规则
启用规则
禁用规则
修改 action
新增 blocked_up
解除 blocked_up
```

必须让现有 Feed Cache 失效。

推荐实现：

```text
增加 filter_version
```

存在：

```text
app_state
```

例如：

```text
key = filters:version
value = 12
```

规则发生变化：

```text
filters:version += 1
```

`feed_cache` 需要关联/检查过滤版本。

如果当前实现不方便加字段，也可以 v0.2.1 暂时：

```text
规则变更后清理 feed_cache
```

但不要清：

```text
recommendation_history
served_videos
feedback
candidates
```

---

# 十四、设置页修改

现有 React 设置中的过滤页面升级为三个 Tab：

```text
标题关键词
UP 主关键词
精确屏蔽 UP
```

标题关键词和 UP 主关键词列表至少显示：

```text
关键词
匹配方式
行为
来源
命中次数
最近命中
启用状态
删除
```

默认：

```text
match_mode = contains
action = hard_block
```

支持：

```text
搜索
单条添加
单条删除
启用/禁用
批量导入
```

---

# 十五、批量导入

增加一个批量导入输入框。

支持：

```text
关键词1|关键词2|关键词3
```

以及：

```text
关键词1
关键词2
关键词3
```

解析：

```text
按 | 或换行拆分
→ trim
→ 去空
→ casefold 去重
→ upsert
```

导入结果返回：

```text
输入数量
新增数量
重复数量
无效数量
```

---

# 十六、API

建议统一到现有 `/api/v1/filters` 下。

至少支持：

```http
GET    /api/v1/filters/rules
POST   /api/v1/filters/rules
PATCH  /api/v1/filters/rules/:id
DELETE /api/v1/filters/rules/:id

POST   /api/v1/filters/rules/import
```

查询支持：

```text
target_type=title
target_type=uploader
enabled=true
q=关键词
```

保留现有 blocked UP API。

---

# 十七、过滤器实现位置

不要把 SQL 查询和过滤逻辑散落在：

```text
FeedPage.tsx
route handler
Recommender.py
```

新增统一服务，例如：

```text
backend/services/filter_service.py
```

职责：

```text
load_rules()
normalize()
match_title()
match_uploader()
filter_candidates()
record_hits()
invalidate_cache()
```

React 不参与实际过滤判断。

---

# 十八、性能

当前候选约数百条，规则约百条，直接 Python contains 已足够。

不要为了这批规则引入：

```text
Elasticsearch
Redis
全文检索服务
LLM
向量数据库
```

先保持轻量。

可在 service 内缓存已启用规则：

```text
规则版本没变
→ 复用内存规则

规则版本变化
→ 重新从 SQLite 加载
```

---

# 十九、导入后预期数据量

本次输入：

```text
标题规则：65
UP 名称规则：32
合计：97
```

实际数据库新增量可能少于 97，因为：

```text
现有 blocked_keywords 中可能已有重复词
```

最终报告必须说明：

```text
旧 blocked_keywords 原有条数
成功迁移条数
标题规则新增条数
UP 规则新增条数
重复跳过条数
filter_rules 最终总数
```

---

# 二十、验证

必须实际验证以下场景：

```text
1. 标题包含“大冰” → 不进入新 Feed
2. 标题包含“蓝牙耳机推荐” → 不进入新 Feed
3. 标题包含“为什么很多人” → 不进入新 Feed
4. UP 名包含“差评君” → 不进入新 Feed
5. UP 名包含“数码” → 不进入新 Feed
6. 已存在 blocked_ups 的 MID 仍然有效
7. 禁用一条规则后，该规则不再生效
8. 再启用后立即恢复
9. 删除规则后不会继续命中
10. 新增规则后不需要重启服务即可生效
11. 规则变更后旧 feed_cache 不再把已屏蔽内容吐回来
12. recommendation_history 不被清空
13. candidates 不被删除
14. 旧 feedback 保留
15. Cookie 不进入新表和 API
```

---

# 二十一、数据库完整性检查

修改前后执行：

```sql
PRAGMA integrity_check;
PRAGMA foreign_key_check;
```

预期：

```text
ok
```

并记录各表行数前后变化。

---

# 二十二、最终交付报告

完成后给出：

```text
数据库文件路径
备份文件路径

迁移前 8 张表现有行数
迁移后所有表行数

filter_rules:
  title:
  uploader:
  enabled:
  hard_block:
  source=biliblock_import:

旧 blocked_keywords:
  是否仍保留:
  是否还有代码直接依赖:

导入:
  标题输入 65
  UP 输入 32
  新增:
  重复:
  失败:

缓存失效机制:
  实现方式:

实测过滤:
  标题:
  UP:
  blocked MID:
  开关:
  删除:
```

同时列出实际修改文件。

---

# 二十三、禁止事项

本任务不要：

```text
修改 Wide&Deep + Attention
修改 Rating 公式
扩大历史数据
改变探索比例
删除原推荐历史
删除候选池
把 SQLite 换 PostgreSQL
加入 LLM 过滤
改 Android/iOS
```

本次只完成：

```text
旧屏蔽数据正式入库
+
统一过滤规则层
+
React 规则管理
+
缓存即时失效
```

完成后停止，等待人工验收。
