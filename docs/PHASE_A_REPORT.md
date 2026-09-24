# Phase A Report

日期：2026-09-22

## 结果

阶段 A 已将第一部分接口包纳入架构，并建立了后续 coding 的统一地基。未读取或处理系统原始内容，未安装依赖，未连接真实服务、ChatGPT 账号或公网环境。

## 已交付

- 项目使命、范围和完成定义；
- A–F 路线图与阶段退出条件；
- 第一部分/本仓库责任边界；
- OpenAPI、examples、上游架构和 Dashboard handoff 原样归档；
- 集成后的系统架构、Policy 生效链路和 MCP 映射；
- TypeScript、契约、版本冲突、安全和测试规范；
- 项目级 Codex Skill；
- 零依赖 foundation 校验脚本。

## 采用的关键假设

- 本仓库是 Retrieval Policy 的 Dashboard、实验和观测层。
- 第一部分的 Retrieval Hub 是唯一权威检索实现。
- 原始内容由第一部分负责，本项目全部使用虚构 fixture。
- metadata filter 首先作为单次查询能力，不冒充持久化 policy。

## 风险与待决项

| ID | 事项 | 阶段 A 结论 | 后续动作 |
|---|---|---|---|
| A-R1 | `fetch` 返回完整正文 | 可能显著增加 token，客户端不得静默截断 | 阶段 D/E 实测后决定是否提上游接口变更 |
| A-R2 | metadata filter 不属于 `RetrievalPolicy` | 无法保存后自动影响标准 MCP `search` | 阶段 C 确认是否只需 query filter |
| A-R3 | 引用 URL 本地或需鉴权 | 远程 ChatGPT 可能不可访问 | 阶段 E 验证部署与认证 |
| A-R4 | FTS 全词项匹配 | 自然语言问题可能召回不足 | 阶段 D 使用受控查询并记录失败样例 |
| A-R5 | 上游与 Dashboard 尚未联调 | 当前只能做契约级保证 | 阶段 B mock，阶段 C/D 接本地服务 |

## 验收证据

- OpenAPI 和 examples 均为有效 JSON。
- 自动检查核心 endpoint 和 schema。
- 归档文件有 SHA-256 基线。
- Skill 通过结构验证。
- `ROADMAP.md` 将下一步明确限定为阶段 B：契约客户端与 mock。
