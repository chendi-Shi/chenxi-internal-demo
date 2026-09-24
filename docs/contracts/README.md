# Interface Contracts

## 来源与状态

本目录归档第一部分同事于 2026-09-22 交付的 `MCP架构与接口.zip`。导入时未修改文件内容。

| 文件 | 角色 | 导入时 SHA-256 |
|---|---|---|
| `openapi.json` | 机器可读 API 事实来源，版本 1.0.0 | `3d1cc010447574415a73362166da7fb6b2b4705ff2ebfc05a2fbe69367c6079e` |
| `examples.json` | 虚构联调 fixture 与行为样例 | `a8691a7e2471c20241a11b49148653eb8bf0c09da3e6c57586087b82d54199c9` |
| `../upstream/MCP_ARCHITECTURE.md` | 第一部分架构与协作语义 | `157c171920cfe33651286b81779a299f8716c33d46d308d860886a19fb7e3cd2` |
| `../upstream/DASHBOARD_HANDOFF.md` | 上游启动和 Dashboard 联调说明 | `40f297aeef673618b7b0263ce86167ec827918c76e66c3458c759542e7d95249` |

## 优先级

发生冲突时按以下顺序处理：

1. `openapi.json` 决定字段、类型、状态码和认证声明。
2. `examples.json` 提供已知 fixture，不扩展 schema。
3. 上游架构文档解释语义和责任边界。
4. 本仓库 `docs/ARCHITECTURE.md` 解释如何消费接口，不改变上游协议。

如果机器契约与叙述语义冲突，停止实现相关功能并向第一部分确认，不自行猜测。

## 当前关键契约

- API prefix：`/api`
- API version：`1.0.0`
- 认证：HTTP Bearer；读与管理权限分离
- Policy 更新：`expected_version` 乐观锁
- 标准 MCP：`search`、`fetch`
- 高级 MCP：`search_documents`
- JSON：`snake_case`，多数模型拒绝未知字段

## 更新流程

收到新的正式接口包时：

1. 保留新包来源和版本说明。
2. 替换四个上游文件，不手工拼接局部字段。
3. 运行 `npm test`。
4. 比较 path、schema、required、error code 和认证变化。
5. 更新本文件 hash、`docs/ARCHITECTURE.md`、客户端类型和相关测试。
6. 在 `ROADMAP.md` 或阶段报告记录迁移影响。

禁止为了让前端先跑而直接修改归档的 `openapi.json`。需要新能力时提交接口变更提案。
