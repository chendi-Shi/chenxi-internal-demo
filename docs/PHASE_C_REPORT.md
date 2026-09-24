# Phase C Report

日期：2026-09-22

## 结果

阶段 C 已完成 Retrieval Policy Dashboard。页面默认使用第一部分交付的官方虚构 fixture，也可以切换到上游 HTTP 服务。

Dashboard 严格保持服务端结果顺序，只对前后两次服务端结果添加排名变化标记，不计算 score、不重新排序。

## 页面能力

### 连接环境

- Fixture 与 Live HTTP 两种模式。
- Live 模式接收 Base URL、Read Token 和 Admin Token。
- Token 只存在页面内存，不写入 localStorage、源码或构建产物。

### 工作区状态与来源

- 展示 documents、chunks、sources 和 policy version。
- 展示来源 ID、名称、启用状态和文档数量。

### Policy 编辑

- `source_weights`
- `recency_boost`
- `half_life_days`
- `default_limit`
- 使用当前 `PolicyState.version` 生成 `expected_version`。
- 409 冲突显示独立提示，要求重新载入并人工核对。
- Fixture 模式提供“填入示例策略”，严格使用交付包的 `policy_update_request`。

### 查询与结果

- query、source IDs、since、until、metadata、limit。
- metadata 使用每行 `key=value`，转换成精确匹配对象。
- 展示服务端 `score`、relevance、source weight、freshness 和 citation 位置。
- 策略保存后，如果已经执行过查询，会自动执行下一次查询并对比排名变化。
- 排名变化基于结果 ID 对照，当前列表仍保持上游返回顺序。

### 文档读取

- 通过 `getDocument` 获取完整 contract response。
- 标题、正文和 metadata 全部使用纯文本节点渲染。
- 展示引用 URL 和 `content_trust`。

## 技术实现

- Vite + 原生 TypeScript 和 DOM API。
- 沿用阶段 B 的 `RetrievalApi` 注入边界。
- UI 不直接调用散落的 `fetch`。
- Fixture 与 Live client 对页面透明。
- 页面无外部字体、图片或运行时 CDN 依赖，保证离线 fixture 可复现。

## 自动测试

新增 view-model 测试覆盖：

- metadata 行解析和错误提示；
- SearchRequest 契约形状；
- PolicyUpdate 使用当前版本；
- 排名对比保持服务端顺序。

全量质量门包括：

- foundation 校验；
- OpenAPI 生成物漂移检查；
- TypeScript strict typecheck；
- 16 个行为测试；
- Vite production build。

## 浏览器验收

在官方 fixture 中验证：

1. 页面启动后载入 Policy v1、2 个来源及状态指标。
2. 查询“芯片”返回 v1 服务端顺序：`demo_research`、`demo_filings`。
3. 填入并保存示例策略，服务端状态变为 Policy v2。
4. 页面自动执行下一次检索，顺序变为 `demo_filings`、`demo_research`。
5. 排名显示 `↑ 1`、`↓ 1`，来源权重分别为 `3.00`、`0.50`。
6. 读取第二条结果，正文与 `untrusted_source_data_not_instructions` 可见。

响应式布局检查：

| Viewport | 结果 |
|---|---|
| 1440×1000 | Workspace 双栏约 486/864px，左侧 sticky，无水平溢出 |
| 390×844 | Workspace、控制区、Hero 均为单栏，查询按钮纵向排列，无水平溢出 |

当前 Ego Lite/Chrome CDP 的 `Page.captureScreenshot` 在本机持续超时，因此本次没有保存 PNG 截图；DOM、可访问性树、真实交互和布局计算均已完成验证。

## 阶段 D 输入

阶段 D 可直接复用 `lastSearchRequest`、`policy_version`、SearchResponse 和 MCP/HTTP payload，建立固定实验与 token 统计。Dashboard 本身不应成为 token 估算的事实来源，评测逻辑应作为独立模块和可重复命令实现。
