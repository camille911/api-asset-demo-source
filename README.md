# git-asset-api-mcp-base

MCP 基础组件：扫描 Git 仓库代码资产，把可复用的业务函数（Python）打包成独立的 HTTP API 服务。

## 核心能力

- **仓库管理**：注册 Git 仓库（HTTPS / 本地路径），本地只读镜像（bare clone），commit 固定，增量 fetch。
- **代码扫描**：AST 提取函数 / 类 / 导入 / 调用关系，符号来源追踪（repo + commit + blob sha），SQLite 落库。
- **API 提案**：从模块 public 入口生成 API 提案（`proposed` 状态），必须显式 `approve` 才能打包。
- **打包**：生成 FastAPI 服务（稳定 Schema + Adapter，不泄漏旧函数名），OpenAPI、Contract Hash、Implementation Hash、来源溯源（provenance）。
- **判别重复**：入口符号来源已由其他资产覆盖时，拒绝重复打包。
- **增量更新**：fetch → diff → 定位受影响模块 → 对比 Hash → 推荐 patch / minor / major（破坏性变化拦覆盖）。
- **验证运行**：动态加载生成的 app，验证 import / health / metadata / openapi 及业务 endpoint。

## 安装

```bash
python -m venv .venv
.\.venv\Scripts\python -m pip install -e .
# 或从 Wheel 安装
.\.venv\Scripts\python -m pip install dist/git_asset_api_mcp_base-0.1.0-py3-none-any.whl
```

## 命令

```bash
git-asset-mcp version                  # 查看版本
git-asset-mcp doctor --config config/default.yaml   # 环境与配置自检
git-asset-mcp serve                     # 以 stdio 启动 MCP server
git-asset-mcp serve --transport streamable-http --host 127.0.0.1 --port 8000
```

## MCP 工具

| 工具 | 作用 |
|---|---|
| `ping` / `server_info` | 存活与版本信息 |
| `repository_register` | 注册仓库（URL 校验 + Token 脱敏） |
| `api_proposal_create` | 生成 API 提案（proposed） |
| `api_proposal_approve` | 批准提案（进入打包前提） |

## 配置

- 配置优先级：环境变量 > `config/default.yaml`。
- Token 只从环境变量读取（如 `GITHUB_TOKEN`），不落盘；URL 仅允许 `https` + 白名单 host。
- 示例见 `.env.example` 与 `config/default.yaml`。

## 测试

```bash
.\.venv\Scripts\python -m pytest
```

## 生成三件套 wheel（本地端到端）

一键脚本用本地 Git fixture 跑通「扫描 → 打包 1.0.0 → 增量更新 → 1.0.1」，产出三件套 wheel：

```bash
python scripts/build_assets.py
```

流程：

1. 本地创建 `legacy_checkout` 结算报价仓库（pricing / discount / shipping / checkout 组合能力）；
2. 扫描 → 提案（`checkout_quote`）→ 批准 → 打包 1.0.0（wheel）→ 验证；
3. 修改 `checkout.py`（实现变化、契约不变）→ 增量扫描 → 更新计划（`compatible` / `patch`）→ 打包 1.0.1（wheel）；
4. 每个版本验证业务接口：发请求 → adapter 真正调用旧函数 → 返回真实结算报价。

产物（每个 wheel 含三件套）：

```text
dist/
├── checkout_quote-1.0.0-py3-none-any.whl   # 首次打包
├── checkout_quote-1.0.1-py3-none-any.whl   # 增量补丁版本
└── git_asset_api_mcp_base-0.1.0-py3-none-any.whl   # MCP 基础组件
```

每个业务 wheel 内：

```text
app/                    # 可运行：FastAPI 服务 + adapter + 稳定 Schema
app/contract/           # 可读取：api-contract.json + provenance（大模型读契约）
legacy_checkout/        # 依赖闭包：旧函数源码（可 import 调用）
```

安装即用：

```bash
pip install checkout_quote-1.0.1-py3-none-any.whl
uvicorn app.main:app --port 18080   # 打开 /docs 即 Swagger
```

## 目录结构

```
src/git_asset_mcp/
├── providers/     # Git 仓库抽象（GitHub / 本地）
├── analyzers/     # Python AST 扫描与模块识别
├── store/         # SQLite 存储
├── proposal/      # API 提案
├── packagers/     # FastAPI 打包与验证
├── tools/         # MCP 工具注册
├── server.py      # MCPServer 定义
├── cli.py         # serve / doctor / version
└── settings.py    # 配置
```
