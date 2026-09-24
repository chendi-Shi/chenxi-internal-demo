# Engineering Standards

## 技术边界

- Dashboard 和评测工具使用 TypeScript、ESM、严格类型检查。
- Dashboard 当前采用 Vite + 原生 DOM，不引入组件框架；需要复杂交互后再评估升级。
- 第一部分上游保持语言无关，通过 OpenAPI/HTTP 和 MCP 交互。
- 阶段 B 再确定并锁定 UI 框架、Node 版本和依赖，不在阶段 A 提前安装。

## 接口代码

- 所有 endpoint、认证 header、超时和错误解析集中在 API client。
- DTO 以 OpenAPI 为来源并保持 `snake_case`，不为“前端习惯”改变线上字段。
- 如果内部 UI model 使用不同命名，只允许在一个 mapper 层转换。
- 对 `additionalProperties: false` 的对象不发送额外字段。
- 日期在展示前解析；空字符串表示未知时间，不替换为导入时间。

## Policy 状态

- Policy 编辑从服务端快照开始，保存时发送完整 policy。
- 本地 dirty state、服务端 version 和最后成功保存 version 分开维护。
- 409 时保留用户编辑，重新读取远端版本并要求人工核对。
- 搜索结果必须显示或记录其 `policy_version`，防止把旧结果误认为新策略结果。

## UI 与安全

- 标题、snippet、正文只作为文本节点渲染，不使用未净化 HTML。
- 管理 Token 不写入源码、localStorage、日志或浏览器构建产物。
- 错误 UI 依赖稳定 `error.code`，不解析后端 message 文案。
- 分数显示为排序值和组成项，不显示百分比置信度。

## 测试层级

1. Foundation：契约 JSON、核心 path/schema、必需文档。
2. Contract：请求和响应 fixture 与 OpenAPI 对齐。
3. Client：成功响应及 401/403/404/409/413/422。
4. Workflow：读取策略、编辑、保存、再次搜索。
5. Evaluation：固定 query、policy version、排名与 token 指标。
6. E2E：真实上游 HTTP，再到 MCP/ChatGPT。

测试不得依赖真实系统内容；使用 `docs/contracts/examples.json` 或明确标记的虚构 fixture。

## Token 测量

- 分开记录请求、search 响应、fetch 响应和工具定义。
- 同时记录 UTF-8 字节、字符数和指定 tokenizer 的估算 token。
- 每条实验记录 query、policy version、result IDs 和测量方法。
- 优化前先保存基线；任何缓存 key 必须包含 policy version 或在策略更新时失效。
- `npm run evaluate` 生成评测产物，`npm run check:evaluation` 阻止代码与基线漂移。
- 启发式 token 必须同时报告方法和限制，不能冒充模型账单 token。

## 提交前质量门

- `npm test` 通过。
- 新接口使用已在 OpenAPI 中存在的字段。
- 新行为在 `ROADMAP.md` 当前阶段范围内。
- 架构、契约或阶段状态发生变化时同步更新对应文档。
- 没有真实正文、凭据或临时公网地址进入仓库。
