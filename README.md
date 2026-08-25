# git-asset-api-mcp-base

MCP 基础组件：扫描 Git 仓库代码资产（Python / C / C++ / CUDA / Dockerfile），把可复用的业务函数打包成独立 HTTP API（wheel），并提供**契约语义检索（RAG）**让大模型"先检索定位、命中即复用、未命中再打包"。

## 核心能力

| 能力 | 说明 |
|---|---|
| 仓库管理 | 注册 Git 仓库（GitHub HTTPS / 本地路径），只读 bare 镜像，commit 固定，增量 fetch；认证走 Basic auth（兼容 `ghp_` / `github_pat_` / `gho_`） |
| 代码扫描 | 多语言分析器提取符号/导入/调用关系（Python AST；C/C++/CUDA tree-sitter；Dockerfile 指令），符号来源追踪（repo + commit + blob sha），SQLite 落库 |
| API 提案 | 从模块 public 入口生成提案（`proposed`），支持 `entry_symbol` 指定入口函数；必须显式 `approve` 才能打包（人在回路确认点） |
| API 打包 | **文件级闭包**（入口文件 + `__init__.py` + AST 同包 import 递归）→ 生成 FastAPI 服务（稳定 Schema + Adapter）→ wheel；wheel 内置最小 `__init__.py`，避免原包 re-export 触发全量依赖；含 OpenAPI、Contract Hash、Implementation Hash、来源溯源（provenance） |
| 判别重复 | 入口符号来源已由其他资产覆盖时，拒绝重复打包 |
| 增量更新 | fetch → diff → 定位受影响模块 → 对比 Hash → 推荐 patch / minor / major（破坏性变化拦覆盖） |
| **契约 RAG** | 契约单独抽取 → API 级 chunk 切块 → 语义向量索引；`asset_rag_search` 自然语言检索（含中文），命中直接溯源到 wheel 路径 |
| 验证运行 | 动态加载生成 app，验证 import / health / metadata / openapi 及业务 endpoint |

## 工作流：先检索，后打包

```
提问（自然语言）
  ├─ asset_rag_search 命中  → 返回契约 + wheel 路径，直接复用
  └─ 未命中                 → repository_scan → 提案 → 打包 wheel
```

RAG 是旁路加速：**wheel 打包逻辑不变**，只是把"每次复用都重新打包"优化为"索引命中即取现成产物"。

## 支持语言

| 语言 | 文件类型 | 分析器 | 提取内容 |
|---|---|---|---|
| Python | `.py` | 标准库 `ast` | 函数/类/方法、import 边、调用边 |
| C / C++ / CUDA | `.c` `.h` `.cpp` `.cc` `.cxx` `.hpp` `.hh` `.hxx` `.cu` `.cuh` | tree-sitter-cpp | 函数/方法/类/结构体/命名空间/枚举、`#include` 边、调用边 |
| Dockerfile | `Dockerfile` `Dockerfile.*` `*.dockerfile` | 行解析器（无额外依赖） | 每条构建指令（FROM/RUN/COPY/CMD 等）作为符号 |

- 扫描时按文件名自动路由到对应分析器，`files_total` 统计所有支持语言的文件；
- 测试文件（`test_*` / `*_test.py` / `tests/` 等）与生成物（`__pycache__` / `build/` / `dist/` 等）自动跳过；
- RAG 契约索引覆盖全部支持语言，`language` 字段记录真实来源语言。

## MCP 工具（13 个）

| 工具 | 作用 |
|---|---|
| `ping` / `server_info` | 存活与版本信息 |
| `repository_register` | 注册仓库（URL 校验 + Token 脱敏，Basic auth 克隆） |
| `repository_scan` | 扫描符号/模块，**自动构建 RAG 索引**（模型缺失降级不阻断） |
| `repository_update_check` / `update_plan` | 增量变更检测与升级计划 |
| `module_list` | 列出已扫描模块与入口符号 |
| `api_proposal_create` | 生成 API 提案（`entry_symbol` 可选，缺省取模块第一个公开函数） |
| `api_proposal_approve` | 批准/拒绝提案（批准后才能打包） |
| `api_package_build` | 文件级闭包打包 wheel（构建隔离于 OS temp，兼容沙箱环境） |
| `api_package_verify` | 验证制品（import / health / metadata / openapi） |
| `asset_rag_search` | **语义检索**契约索引，返回 top-k + 全链溯源（契约/artifact/wheel/commit） |
| `asset_rag_status` | RAG 索引统计（contracts / chunks / repositories） |

## 契约 RAG

- **契约来源**：扫描符号表（public 函数 = 1 个 API 契约），关联已打包制品（artifact / wheel / contract_hash）形成溯源。
- **切块**：API 级——每个契约 2+ 块（摘要块：名称/模块/签名/来源；docstring 块：功能细节），全部可溯源。
- **检索**：`sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`（384 维，多语言）本地模型，归一化余弦 top-k；中文提问可命中英文契约。
- **溯源链**：`chunk → contract → artifact → wheel_path → source commit / blob`，检索结果直接给出可安装的 wheel 路径。
- **索引生命周期**：`repository_scan` 自动重建该 commit 的索引（幂等）；`asset_rag_status` 可查规模。

## 安装

```bash
python -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[rag]"     # 源码安装（含 RAG 依赖）
# 或从 Wheel 安装（RAG 依赖单独安装）
.\.venv\Scripts\python -m pip install dist/git_asset_api_mcp_base-0.1.0-py3-none-any.whl
.\.venv\Scripts\python -m pip install sentence-transformers
```

**使用注意**：wheel 内的包目录与 API 名绑定，同一 Python 环境不要安装两个含同名包目录的 wheel（同名 API 资产请分环境使用）。

**Embedding 模型**（语义检索必需，首次使用前准备）：

- 默认模型 `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`；
- 放到 `~/.git-asset/models/`（或 `RAG_MODELS_DIR` 指定目录）即可离线加载；
- 也可用 `RAG_EMBED_MODEL` 指定其他模型（本地路径或 HF 仓库名）；
- 模型未就绪时，扫描/检索自动降级（`rag_indexed=False`），其余功能不受影响。

## 命令

```bash
git-asset-mcp version                  # 查看版本
git-asset-mcp doctor --config config/default.yaml   # 环境与配置自检
git-asset-mcp serve                     # 以 stdio 启动 MCP server
git-asset-mcp serve --transport streamable-http --host 127.0.0.1 --port 8000
```

## 配置

- 优先级：环境变量 > `config/default.yaml`。
- Token 只从环境变量读取（如 `GITHUB_TOKEN`），不落盘；URL 仅允许 `https` + 白名单 host。
- 常用环境变量：

| 变量 | 说明 | 默认 |
|---|---|---|
| `DATA_DIR` | 数据目录（SQLite + 仓库镜像） | `./data` |
| `GENERATED_DIR` | 业务制品目录（FastAPI 应用 + 契约 + 源码闭包）；wheel 统一输出到其父目录 `dist/` | `./generated` |
| `LOG_DIR` | 日志目录 | `./logs` |
| `GITHUB_TOKEN` | GitHub 访问 Token（公开仓库可留空，走系统凭据链） | 空 |
| `RAG_EMBED_MODEL` | embedding 模型（本地路径或 HF 仓库名） | `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` |
| `RAG_MODELS_DIR` | 本地模型目录 | `~/.git-asset/models` |

## 测试

```bash
.\.venv\Scripts\python -m pytest
```

56 个用例：扫描/提案/打包/验证/增量更新/RAG（抽取、切块、索引、检索、溯源、幂等）。

端到端验证（本地 Git fixture 跑通「扫描 → 打包 1.0.0 → 增量更新 → 1.0.1」，产物输出到 `dist/`，不依赖远程仓库）：

```bash
.\.venv\Scripts\python scripts/build_assets.py
```

## 目录结构

```
src/git_asset_mcp/
├── providers/     # Git 仓库抽象（GitHub / 本地）
├── analyzers/     # 多语言分析器（python / cpp / dockerfile）与模块识别
├── rag/           # 契约 RAG：抽取 / 切块 / embedding / 索引 / 检索
├── store/         # SQLite 存储（含 rag_contracts / rag_chunks 表）
├── proposal/      # API 提案
├── packagers/     # FastAPI 打包与验证
├── tools/         # MCP 工具注册（含 rag_tools）
├── server.py      # MCPServer 定义
├── cli.py         # serve / doctor / version
└── settings.py    # 配置
```
