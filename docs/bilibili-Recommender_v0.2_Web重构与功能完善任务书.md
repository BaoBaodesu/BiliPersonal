# bilibili-Recommender v0.2 Web 重构与功能完善任务书

## 0. 当前结论

当前版本已经完成了最重要的“链路验证”：

```text
Bilibili 登录
→ 获取历史 / 收藏
→ 生成训练数据
→ 训练模型
→ 获取候选视频
→ 排序
→ Web 展示
```

但目前只能视为 **可运行原型**，还不能视为可长期使用的推荐客户端。

根据当前页面和现有源码，主要问题有：

1. 前端仍是 Flask + Jinja2，页面结构偏 Demo。
2. 推荐内容通过弹窗展示，不适合作为长期浏览的信息流。
3. 首页有效信息密度低，和 YouTube 这类成熟视频首页差距明显。
4. 每次点击“热门精选 / 兴趣探索”都需要等待，缺少预加载、缓存和骨架屏。
5. 重复点击后推荐内容基本不变化。
6. 缺少“刷新推荐 / 换一批 / 不感兴趣 / 不看这个 UP / 屏蔽关键词”等基础功能。
7. 缺少推荐历史、已展示去重、用户反馈记录。
8. 推荐模型状态和 Web UI 耦合较紧，后续 Android / iOS 不容易直接复用。
9. 当前 Rating 更适合作为调试指标，不适合直接作为主视觉信息展示。
10. 页面没有形成“首页信息流”的使用习惯，用户仍然需要主动点击按钮才生成推荐。

本阶段目标不是继续优化模型，而是把它先改造成一个真正能日常使用的 **Personal Bilibili Web Client**。

---

# 1. v0.2 目标

将现有 Web 原型升级为：

```text
React Web
    ↓
统一 JSON API
    ↓
Flask Backend
    ↓
Recommendation Service
    ↓
Bilibili API / Model / Cache / Feedback
```

重点完成：

- React 化
- YouTube 风格首页布局
- 推荐流常驻首页
- 推荐缓存与预加载
- “换一批”
- 推荐去重
- 无限滚动 / 加载更多
- 用户反馈
- 屏蔽规则
- 推荐历史
- 后台生成推荐
- 模型状态显示
- 为 Android / iOS 预留统一 API

**本阶段不重写 Wide & Deep + Attention 模型。**

先把产品层和推荐调用链做好，再进入 v0.3 的推荐算法改造。

---

# 2. 一个已经确认的重复推荐原因

当前 `Recommender.recommend()` 中虽然注释写着：

> 返回后会 pop 掉这些视频

但实际代码只是：

```python
top_videos = results[:video_cnt]
return top_videos
```

并没有从候选池中移除已经返回的视频。

因此：

```text
第一次请求
→ 排序
→ 返回 Top 8

第二次请求
→ 同一个候选池
→ 同一个模型
→ 同一种排序
→ 仍然返回 Top 8
```

这就是目前“生成的内容还是一样”的直接原因之一。

v0.2 必须增加：

```text
served_bvids
```

或者等价机制，保证同一批候选中已经展示过的视频不会立即再次返回。

---

# 3. 技术栈

## 前端

采用：

```text
React
TypeScript
Vite
React Router
TanStack Query
Tailwind CSS
Lucide Icons
```

可选：

```text
Zustand
```

只用于少量全局 UI 状态，不要把所有服务端数据塞进 Zustand。

TanStack Query 负责：

- 推荐列表
- 用户信息
- 模型状态
- 兴趣标签
- 推荐历史
- 加载状态
- 缓存
- 请求重试

---

## 后端

暂时继续使用：

```text
Flask
Python 3.11
TensorFlow 2.18
```

本阶段 **不要同时把 Flask 改成 FastAPI**。

原因：

当前后端已经可以正常调用 Bilibili API 和模型。

如果同时进行：

```text
Jinja → React
Flask → FastAPI
推荐调用逻辑改造
数据持久化改造
```

会一次改变太多变量。

v0.2 先完成：

```text
Flask 页面服务
→ Flask JSON API
```

等 Web 稳定后，如果 Android / iOS 开始开发，再决定是否迁移 FastAPI。

---

# 4. 新目录结构

建议改为：

```text
bilibili-Recommender/
│
├─ backend/
│  ├─ app.py
│  ├─ routes/
│  │  ├─ auth.py
│  │  ├─ feed.py
│  │  ├─ user.py
│  │  ├─ feedback.py
│  │  └─ system.py
│  │
│  ├─ services/
│  │  ├─ bilibili_service.py
│  │  ├─ recommendation_service.py
│  │  ├─ training_service.py
│  │  └─ cache_service.py
│  │
│  ├─ recommender/
│  │  ├─ model.py
│  │  ├─ recommender.py
│  │  └─ feature_processor.py
│  │
│  ├─ storage/
│  │  ├─ database.py
│  │  └─ migrations.py
│  │
│  └─ data/
│
├─ frontend/
│  ├─ src/
│  │  ├─ api/
│  │  ├─ components/
│  │  ├─ layouts/
│  │  ├─ pages/
│  │  ├─ hooks/
│  │  ├─ stores/
│  │  ├─ types/
│  │  └─ styles/
│  ├─ public/
│  ├─ package.json
│  └─ vite.config.ts
│
├─ legacy/
│  └─ 原 Jinja 页面
│
├─ saved_model/
├─ requirements.txt
└─ README.md
```

不要一开始直接删除旧页面。

旧版放入：

```text
legacy/
```

或者保留旧路由直到 React 版验收完成。

---

# 5. 首页改造

## 5.1 页面结构

取消现在这种：

```text
首页
→ 点击“热门精选”
→ 弹窗
→ 等待
→ 查看 8 个视频
```

改成打开首页后直接看到推荐：

```text
┌─────────────────────────────────────────────┐
│ Logo      搜索框                刷新   头像 │
├──────┬──────────────────────────────────────┤
│      │ [全部] [游戏] [音乐] [动画] [科技] │
│ 左侧 │                                      │
│ 导航 │ 视频1   视频2   视频3   视频4        │
│      │                                      │
│      │ 视频5   视频6   视频7   视频8        │
│      │                                      │
│      │ 视频9   视频10  视频11  视频12       │
│      │                                      │
│      │             继续加载                 │
└──────┴──────────────────────────────────────┘
```

推荐视频本身就是首页主体。

---

# 6. YouTube 风格视觉规范

不是照搬 YouTube Logo 或品牌元素，而是参考其信息架构和密度。

## Header

高度：

```text
56px
```

包含：

```text
Logo
搜索框
刷新推荐
模型状态
头像
设置
```

Header 固定在顶部。

---

## Sidebar

桌面端：

```text
展开：220~240px
收起：72px
```

导航：

```text
首页
热门
兴趣探索
推荐历史

观看历史
收藏
稍后再看

不感兴趣
屏蔽的 UP
屏蔽关键词

兴趣画像
设置
```

---

## Category Chips

Header 下方显示当前兴趣分类：

```text
全部
游戏
音乐
动画
科技
影视
知识
数码
近期兴趣
发现新内容
```

标签可以根据用户历史动态生成。

样式类似 YouTube：

```text
圆角矩形
单行横向滚动
当前选择高亮
```

---

# 7. 视频卡片

采用 16:9。

桌面宽屏使用 CSS Grid：

```css
grid-template-columns:
repeat(auto-fill, minmax(280px, 1fr));
```

单卡片结构：

```text
┌───────────────────────────┐
│                           │
│         Thumbnail         │
│                     12:34 │
└───────────────────────────┘

标题最多两行
UP 主
播放量 · 发布时间
```

不要像当前版本只显示：

```text
RT: 2.423
```

Rating 默认隐藏。

在：

```text
设置 → 调试 → 显示推荐评分
```

开启后才显示。

---

# 8. 视频卡片菜单

每张卡片右侧增加：

```text
⋮
```

菜单：

```text
不感兴趣
不看这个 UP
屏蔽此关键词
稍后再看
复制 BV 号
为什么推荐给我
```

“为什么推荐给我”先做简单版：

```text
因为你最近观看了：
- 怪物猎人
- 动作游戏
- 某 UP

匹配标签：
- 游戏
- 动作
- 狩猎
```

不要求使用 LLM。

---

# 9. 推荐流类型

主页不再只保留两个按钮。

设计为三个 Feed：

## For You

默认首页。

候选来源可以混合：

```text
热门
+
Bilibili 推荐
```

再交给当前模型排序。

---

## 热门

对应原：

```text
热门精选
```

---

## 探索

对应原：

```text
兴趣探索
```

允许出现用户过去很少看的内容。

---

# 10. “换一批”与去重

新增：

```text
换一批
```

行为：

```text
当前 Feed
↓
记录已展示 BVID
↓
从候选池排除
↓
返回下一组
```

至少当前 Session 内不能重复。

数据结构可类似：

```python
served_bvids = {
    "for_you": set(),
    "hot": set(),
    "explore": set(),
}
```

如果候选池耗尽：

```text
重新抓候选
→ 去除本 Session 已展示视频
→ 再排序
```

如果实在没有新内容：

允许重新使用较早的视频，但必须等候选池真正耗尽后。

---

# 11. 无限滚动

首页第一次返回：

```text
12~16 个
```

用户向下滚动接近底部时：

```text
自动请求下一页
```

建议：

```text
limit = 12
```

API：

```http
GET /api/feed?type=for_you&cursor=xxx&limit=12
```

响应：

```json
{
  "items": [],
  "next_cursor": "...",
  "has_more": true
}
```

不要一次向前端发送几百条视频。

---

# 12. 解决“每次等待几秒”

目前的等待来自：

```text
点击推荐
→ 初始化 Recommender
→ 训练
→ 获取候选
→ 评分
→ 下载图片
→ 返回
```

这是不合理的交互。

改为：

```text
登录成功
↓
进入 Dashboard
↓
后台启动训练 / 加载模型
↓
同时准备第一页推荐
↓
前端先显示 Skeleton
↓
推荐就绪后自动替换
```

---

# 13. 模型缓存

当前不要每次都重新训练 30 Epoch。

新增：

```text
history_hash
```

例如根据：

```text
BVID + progress + isliked + isfaved
```

生成数据指纹。

启动时：

```text
history_hash 没变
+
saved_model 存在
        ↓
直接加载模型
```

只有：

```text
历史记录变化明显
或
用户主动点击重新训练
```

才重新训练。

---

# 14. 推荐结果缓存

使用 SQLite。

原因：

本项目已经不再只是 Demo，需要保存：

```text
已展示视频
用户反馈
屏蔽 UP
屏蔽关键词
推荐历史
Feed Cache
训练状态
```

SQLite 对单用户本地应用足够。

建议：

```text
data/app.db
```

表：

```text
feed_cache
served_videos
feedback
blocked_ups
blocked_keywords
recommendation_history
app_state
```

Cookie 不写数据库，继续单独保存在本地安全文件中。

---

# 15. 页面加载策略

打开首页：

```text
优先读取 Feed Cache
↓
立即显示上一批推荐
↓
后台刷新新 Feed
↓
新数据完成后无感更新
```

这意味着用户下一次打开页面时：

不应该看到：

```text
正在生成推荐列表……
```

持续几秒。

应该立即出现内容。

---

# 16. Skeleton

第一次确实没有缓存时：

显示 YouTube 风格 Skeleton：

```text
[████████████]
██████████
██████

[████████████]
████████
██████
```

不要使用当前巨大的弹窗 Spinner。

页面仍然应该可以：

```text
打开侧边栏
查看设置
查看历史
```

---

# 17. 用户反馈系统

这是后续推荐算法升级的核心基础。

视频卡片支持：

```text
喜欢
不感兴趣
不看这个 UP
看过了
稍后再看
```

后端统一：

```http
POST /api/feedback
```

例如：

```json
{
  "bvid": "BV...",
  "action": "not_interested"
}
```

action：

```text
like
not_interested
block_up
watched
watch_later
```

v0.2 暂时只记录，不要求立刻重训模型。

但：

```text
not_interested
block_up
```

应立即影响当前 Feed。

---

# 18. 屏蔽功能

增加设置页面。

## 屏蔽关键词

例如：

```text
明星八卦
饭圈
短剧
营销
引战
```

支持：

```text
添加
删除
启用 / 禁用
```

---

## 屏蔽 UP

保存：

```text
mid
name
```

推荐生成后首先过滤。

---

## AI 内容过滤

BilibiliContentFilter 的思路暂时作为 **可选模块**。

v0.2 不要求直接接 LLM。

先完成普通规则过滤：

```text
标题关键词
UP
Tag
```

以后再加入：

```text
LLM Dislike Filter
```

---

# 19. 搜索

Header 增加搜索框。

v0.2 可以先直接调用 Bilibili 搜索。

页面：

```text
/search?q=怪物猎人
```

搜索结果仍使用统一 VideoCard。

不用重新发明完整 Bilibili 搜索系统。

---

# 20. 推荐历史

新增页面：

```text
/history/recommendation
```

记录：

```text
推荐时间
Feed 类型
BVID
标题
Rating
是否点击
用户反馈
```

用途：

以后比较推荐算法版本。

---

# 21. 兴趣画像页面

现在的 Tag Cloud 可以保留，但不要继续占首页近一半空间。

移动到：

```text
/profile/interests
```

展示：

```text
Top Tags
常看 UP
常看分区
最近兴趣
长期兴趣
观看时长分布
```

首页只保留顶部 Category Chips。

---

# 22. 当前 Tag Cloud

当前 3D Tag Cloud：

```text
视觉效果 > 实际信息价值
```

因此不要作为首页核心。

可以在“兴趣画像”页面作为辅助视觉。

默认使用更清晰的：

```text
Tag 列表
+
权重条
```

例如：

```text
怪物猎人       █████████  92
游戏音乐       ███████    71
UE5            ██████     65
动画           █████      53
```

---

# 23. 新 API

统一放在：

```text
/api/v1/
```

## Auth

```http
POST /api/v1/auth/qrcode
GET  /api/v1/auth/qrcode/status
GET  /api/v1/auth/status
POST /api/v1/auth/logout
```

Cookie 永远不能返回给 React。

---

## User

```http
GET /api/v1/user/profile
GET /api/v1/user/interests
GET /api/v1/user/history
```

---

## Feed

```http
GET  /api/v1/feed
POST /api/v1/feed/refresh
```

参数：

```text
type
cursor
limit
category
```

---

## Feedback

```http
POST /api/v1/feedback
GET  /api/v1/feedback/history
```

---

## Filters

```http
GET    /api/v1/filters
POST   /api/v1/filters/keywords
DELETE /api/v1/filters/keywords/:id

POST   /api/v1/filters/ups
DELETE /api/v1/filters/ups/:id
```

---

## System

```http
GET  /api/v1/system/status
POST /api/v1/system/retrain
```

status 返回：

```json
{
  "model": "ready",
  "training": false,
  "feed_cache": true
}
```

---

# 24. React 页面

至少包含：

```text
/
首页推荐

/trending
热门

/explore
兴趣探索

/search
搜索

/history
观看历史

/history/recommendation
推荐历史

/profile/interests
兴趣画像

/settings/filters
过滤规则

/settings
设置
```

---

# 25. React Components

至少拆：

```text
AppShell
Header
Sidebar
CategoryChips

VideoGrid
VideoCard
VideoCardMenu
VideoSkeleton

FeedToolbar
LoadMoreTrigger
EmptyState
ErrorState

ModelStatus
UserAvatar

FilterEditor
InterestPanel
```

不要把首页所有 JSX 写在一个文件里。

---

# 26. UI 状态

必须完整处理：

```text
Loading
Success
Empty
Error
Offline
Model Training
Bilibili Rate Limited
Login Expired
```

例如 Bilibili `-352`：

不要直接显示空白页。

显示：

```text
Bilibili 暂时限制了候选请求
正在使用缓存推荐
```

并继续显示缓存内容。

---

# 27. 推荐生成机制

建议：

```text
Candidate Pool
    ↓
去重复 BVID
    ↓
屏蔽 UP / 关键词
    ↓
排除 served_bvids
    ↓
当前模型 Rating
    ↓
排序
    ↓
Feed
```

v0.2 不调整模型公式。

---

# 28. 候选池生命周期

当前池子长期保留会导致旧内容。

增加：

```text
created_at
last_refresh_at
```

满足任一条件刷新：

```text
候选池不足
超过 TTL
用户手动刷新
```

建议初始 TTL：

```text
15~30 分钟
```

不要频繁打 Bilibili API。

---

# 29. 去重

至少三层：

```text
同一页不重复
同一 Session 尽量不重复
最近 N 条推荐不重复
```

推荐历史中保留：

```text
last_served_at
```

可设置：

```text
24 小时内不重复
```

以后再调。

---

# 30. “刷新推荐”

Header 增加刷新按钮。

点击后：

```text
保留当前页面
↓
显示轻量 loading
↓
请求下一批
↓
旧列表淡出
↓
新列表替换
```

不要再弹出 Modal。

---

# 31. 图片策略

不要继续把所有 Bilibili 图片下载进：

```text
app/static/user_img
```

作为主要显示方案。

React 优先直接使用 Bilibili 图片 URL。

只有遇到防盗链或跨域问题再增加后端图片代理。

避免本地不断堆积封面文件。

---

# 32. 响应式布局

Desktop：

```text
Sidebar + 4~5 列
```

Laptop：

```text
Sidebar + 3~4 列
```

Tablet：

```text
2~3 列
```

Mobile Web：

```text
1~2 列
```

即使以后做原生 Android/iOS，Web 本身也应该可以在手机浏览器正常使用。

---

# 33. 深色模式

支持：

```text
System
Light
Dark
```

通过 CSS Variable 实现。

不要给每个组件写独立硬编码颜色。

---

# 34. 视觉风格

总体方向：

```text
简洁
高信息密度
低装饰
视频内容优先
```

避免当前页面：

```text
大面积背景图
巨型渐变
3D Tag Cloud 占主页面
大 Modal
彩色 Rating 按钮
```

主页的视觉焦点应该是：

```text
视频封面
标题
UP
```

而不是 UI 自身。

---

# 35. 推荐评分

当前 Rating：

```text
0.215
0.218
0.242
```

区分度很低。

因此默认 UI：

```text
不显示 Rating
```

Debug Mode 才显示：

```text
Raw Rating
Candidate Source
Matched Tags
Rank Position
```

这样便于继续研究模型，同时不污染正常体验。

---

# 36. Debug Panel

开发模式加入：

```text
Debug
```

显示：

```text
模型状态
历史样本数量
候选池数量
有效候选数量
served 数量
当前 Feed Cache
最近 API 错误
Bilibili -352 次数
```

方便 Agent 调试。

正式 UI 默认隐藏。

---

# 37. 安全要求

继续保持：

```text
Cookie 只在 Python 后端
```

React 不能看到：

```text
SESSDATA
bili_jct
DedeUserID
完整 Cookie
```

API 不能返回。

Console 不能打印。

数据库不能存完整 Cookie。

---

# 38. 开发顺序

## Phase 1 — 前后端解耦

完成：

```text
frontend/
React + TypeScript + Vite

Flask
→ /api/v1/
```

先让 React 能显示：

```text
用户状态
兴趣
推荐视频
```

---

## Phase 2 — 首页

完成：

```text
Header
Sidebar
Category Chips
Video Grid
Video Card
Skeleton
Dark Mode
```

取消推荐 Modal。

---

## Phase 3 — Feed Engine

完成：

```text
served_bvids
去重
换一批
分页
无限滚动
Feed Cache
候选 TTL
```

这是解决“推荐永远一样”的关键阶段。

---

## Phase 4 — Feedback

完成：

```text
不感兴趣
不看这个 UP
关键词屏蔽
推荐历史
```

SQLite 落盘。

---

## Phase 5 — 模型生命周期

完成：

```text
保存 history_hash
模型直接加载
后台重训
训练状态 API
缓存推荐
```

解决每次等待的问题。

---

## Phase 6 — Polish

完成：

```text
搜索
错误页
空状态
Rate Limit 状态
响应式
键盘操作
UI 动画
```

---

# 39. 本阶段禁止事项

暂时不要：

```text
更换推荐模型
加入大型 LLM
接入 Qwen Embedding
改 XGBoost
改 Two-Tower
做 Android
做 iOS
做 Chrome Extension
部署公网服务器
```

原因：

现在必须先把产品层做稳定。

否则推荐算法、UI、数据采集、客户端一起变，会无法判断问题来自哪里。

---

# 40. v0.2 验收标准

完成后必须满足：

- [ ] 首页使用 React
- [ ] Flask 不再负责主 UI 渲染
- [ ] 打开首页直接看到推荐视频
- [ ] 推荐不再使用 Modal
- [ ] 页面视觉接近现代视频平台的信息流
- [ ] 支持 Light / Dark
- [ ] 支持响应式
- [ ] 支持 Category Chips
- [ ] 支持换一批
- [ ] 连续换一批不会立即出现完全相同的 8 个视频
- [ ] 支持无限滚动 / 加载更多
- [ ] 已展示视频有去重
- [ ] 支持不感兴趣
- [ ] 支持不看此 UP
- [ ] 支持屏蔽关键词
- [ ] 支持推荐历史
- [ ] 推荐状态持久化
- [ ] 页面刷新后不丢失主要配置
- [ ] 有 Feed Cache
- [ ] 第二次打开页面能立即看到缓存推荐
- [ ] 模型无需每次请求重新训练
- [ ] 可以查看模型训练状态
- [ ] Bilibili -352 时不会直接变成空页面
- [ ] Cookie 不暴露给前端
- [ ] 原 Wide & Deep + Attention 结构保持不变

---

# 41. v0.2 完成后的下一阶段

完成这个版本之后，再开始：

```text
v0.3 Recommendation Engine
```

届时重点解决已经确认的模型问题：

```text
30 条样本过少
收藏可能被标成负样本
Wide quality_score 全部饱和为 1
训练集自评导致 AUC 虚高
未知 Tag 直接被淘汰
Rating 区分度过低
缺少长期 / 短期兴趣
缺少真正负反馈
```

v0.3 再考虑：

```text
500~5000 条历史
时间衰减
Embedding
负反馈模型
多样性重排
探索比例
兴趣画像
更大的候选池
```

---

# 42. 最终目标架构

```text
                    Bilibili
                       │
                       ↓
                Candidate Service
                       │
              ┌────────┴────────┐
              ↓                 ↓
         Hot Candidate     Explore Candidate
              └────────┬────────┘
                       ↓
                  Filters
                       ↓
              Recommendation
                       ↓
                 Feed Cache
                       ↓
              Unified REST API
                       │
          ┌────────────┼────────────┐
          ↓            ↓            ↓
       React Web    Android       iOS
```

v0.2 只做：

```text
React Web
+
统一 API
+
Feed 生命周期
+
用户反馈基础设施
```

先把它从“课程 Demo”变成真正可以每天打开使用的 Web 产品。
