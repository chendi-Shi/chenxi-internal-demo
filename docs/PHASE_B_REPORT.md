# Phase B Report

日期：2026-09-22

## 结果

阶段 B 已完成契约客户端和 fixture adapter 基线。开发与测试不需要真实上游服务、原始内容或网络连接。

与项目无关的占位文件 `index.js` 已按要求删除，`package.json` 不再声明该入口。

## 实现

### OpenAPI 派生契约

- `scripts/generate-contracts.mjs` 从 `docs/contracts/openapi.json` 生成唯一 TypeScript DTO。
- 同一生成物内携带运行时 schema 和官方虚构 examples。
- `npm run check:generated` 检测 OpenAPI 更新后未重新生成的漂移。
- `src/api/validation.ts` 执行所需 JSON Schema 子集，包含 required、unknown field、类型、范围、枚举、pattern、array 和 `$ref`。

生成物 `src/api/generated/contracts.ts` 不允许手工修改。

### 单一 API client

`RetrievalApiClient` 覆盖：

- `GET /api/status`
- `GET /api/sources`
- `POST /api/search`
- `GET /api/documents/{document_id}`
- `GET /api/policy`
- `PUT /api/policy`

读操作使用 read Token，策略更新使用 admin Token。请求和响应都经过契约验证；未知响应字段会转成协议错误，不会静默接受接口漂移。

错误响应统一映射为带 `status/code/fields` 的 `ApiError`，409 保持为显式 `policy_version_conflict`。

### Fixture adapter

`FixtureRetrievalApi` 与 HTTP client 实现同一个 `RetrievalApi` 接口，并直接回放 `examples.json` 中的上游结果。

它支持演示：

```text
读取 v1 policy
  → 返回 v1 搜索顺序
  → 携带 expected_version=1 保存样例 policy
  → 返回 v2 policy
  → 下一次搜索返回上游提供的 v2 排序
```

Fixture adapter 不计算 score、不应用 source weight，也不实现本地 ranking。不存在对应 fixture 的查询会显式失败，避免产生看似真实的模拟结果。

## 测试覆盖

- status、sources、search、document、policy HTTP 路径；
- read/admin Token 选择；
- 搜索与策略更新请求体；
- 401、403、404、409、413、422 映射；
- 未知响应字段拒绝；
- 缺少 admin Token 时不发送请求；
- `read policy → save → next search` 完整闭环；
- stale policy update 返回 409；
- fixture document 的不可信内容标记。

质量门：

```text
npm run check:foundation
npm run check:generated
npm run typecheck
npm run test:unit
```

## 依赖

只增加开发依赖：

- `typescript`
- `@types/node`

没有安装 UI 框架、HTTP client、schema validator 或测试框架；运行时使用平台 `fetch`，测试使用 Node 内置 test runner。

## 当前限制

- Fixture adapter 只有官方样例 query 和一次 policy transition，不能替代真实检索服务。
- 尚未建立 Dashboard UI，这是阶段 C。
- 尚未连接上游本地服务或 MCP，这是阶段 C/E。
- 尚未进行 token 估算，这是阶段 D。

## 阶段 C 输入

阶段 C 可直接依赖 `RetrievalApi`，在开发时注入 `FixtureRetrievalApi`，联调时替换为 `RetrievalApiClient`。组件不得直接调用 `fetch` 或重新定义 API DTO。
