# QMS Agent Backend (功能1+2)

本扩展优先实现后台能力，尽量少改 RAGFlow 源码。

## 已实现能力

### 功能1：SSME DX & SSME US QMS流程智能问答
- 接口：`POST /v1/qms/ask-procedure`
- 输出包含：
  - 可执行步骤
  - 关键控制点
  - 常见错误与规避建议
  - 检索到模板/表单时的 `template_hints`

### 功能2：体系知识学习与深度问答
- 接口：`POST /v1/qms/learn-module`
- 输出包含：
  - 模块总览
  - 原则与术语
  - 流程框架（输入-活动-输出）
  - 岗位落地建议
  - 自测题与下一轮建议

## 记忆机制（多轮增强）

采用本地 SQLite 的“类型化记忆”：
- `profile`：用户角色与职责
- `preference`：回答风格偏好
- `objective`：当前目标
- `glossary`：术语映射
- `history`：近期关键对话

每轮问答后自动提取并回写记忆，下一轮自动注入到提示词。

## 长期持久记忆（跨会话画像）

已接入 SQLite 长期画像：
- 新增表：`user_profile`
- 每轮问答后自动从 `typed_memory + conversation_log` 重建用户画像
- 画像包含：
  - `persona`（角色画像）
  - `preferences`（长期偏好）
  - `long_term_objectives`（长期目标）
  - `glossary`（术语映射）
  - `recurring_topics`（高频关注主题）

可通过接口查看：

```http
GET /v1/qms/memory/profile?user_id=u001
```

## 反馈学习（用户纠正 -> 权重更新）

新增接口：

```http
POST /v1/qms/feedback
```

请求示例：

```json
{
  "user_id": "u001",
  "session_id": "optional-session-id",
  "question": "根据SSME QMS，不合格品报告NCM流程步骤是什么？",
  "original_answer": "模型原回答...",
  "corrected_answer": "纠正：请按识别-隔离-评估-处置-复核-归档六步输出，并包含CAPA@Med记录要求与条款证据。",
  "note": "后续统一六步结构+证据优先",
  "score": 1.6
}
```

行为：
- 自动生成 `eval_signal` 作为可学习信号
- 把纠正内容提取为 typed memory 并增权写回
- 对命中的负向关键词相关旧记忆做降权
- 刷新 `user_profile`，用于后续问答提示词注入

## 启动方式

在项目根目录执行（Windows PowerShell 示例）：

1. 设置环境变量
- `RAGFLOW_API_KEY`：必填
- `RAGFLOW_BASE_URL`：默认 `http://127.0.0.1:9380`
- `QMS_CHAT_ID`：可选（已有聊天助手 ID）
- `QMS_CHAT_NAME`：可选，默认 `QMS Assistant`
- `QMS_DATASET_IDS`：可选，逗号分隔（创建新助手时使用）
- `QMS_MEMORY_DB`：可选，默认 `./extensions/qms_agent_backend/data/qms_memory.sqlite3`

2. 启动服务

```bash
python -m extensions.qms_agent_backend.server
```

默认监听：`http://0.0.0.0:9390`

## 调用示例

### 1) 流程问答

```json
POST /v1/qms/ask-procedure
{
  "user_id": "u001",
  "question": "如何发起NCM不合格品报告？"
}
```

### 2) 体系学习

```json
POST /v1/qms/learn-module
{
  "user_id": "u001",
  "module": "风险管理",
  "question": "请从日常执行角度详细讲一下"
}
```

## 说明

- 本实现复用了 RAGFlow SDK 的 `chat/session` 能力，知识检索来源于你已配置的知识库。
- 前端可后续再接；当前接口已可直接用于后端联调。

## 一键生成“3核心场景”智能体 Flow（代码方式）

你可以直接用脚本在“智能体界面”创建/更新一个可视化 Agent（无需手动拖拽）：

- 脚本：`extensions/qms_agent_backend/create_qms_agent_flow.py`
- 覆盖场景：
  1. QMS流程智能问答
  2. 体系知识学习与深度问答
  3. 文件符合性智能预审

### 环境变量

- `RAGFLOW_API_KEY`：必填
- `RAGFLOW_BASE_URL`：默认 `http://ragflow.local`
- `QMS_DATASET_IDS`：建议填写（逗号分隔）
- `QMS_AGENT_LLM_ID`：可选，默认 `qwen-plus@Tongyi-Qianwen`
- `QMS_AGENT_TITLE`：可选，默认 `QMS Copilot - 3 Core Flows`

### 执行

```bash
python -m extensions.qms_agent_backend.create_qms_agent_flow
```

执行后：
- 若同名 Agent 已存在：自动更新 DSL。
- 若不存在：自动创建新 Agent。

然后在 RAGFlow 智能体列表里刷新即可看到该 Agent。

## 外部门户（Flask + HTML/CSS/JS）

已新增“可外部打开”的门户网关，支持：

- 登录鉴权（演示账号）
- 部门隔离访问（MP/Q/PLM/AP/MC）
- 公共资源与部门私有资源可见性控制
- 公共知识库 A 类与部门知识库隔离展示
- 智能体列表 -> 创建会话 -> 问答
- 审计日志落库

新增文件：

- `extensions/qms_agent_backend/portal_server.py`
- `extensions/qms_agent_backend/portal_store.py`
- `extensions/qms_agent_backend/portal_web/index.html`
- `extensions/qms_agent_backend/portal_web/styles.css`
- `extensions/qms_agent_backend/portal_web/app.js`

### 依赖

```bash
pip install flask
```

### 启动

```bash
python -m extensions.qms_agent_backend.portal_server
```

默认监听：`http://0.0.0.0:9391`

浏览器访问：`http://<你的服务器IP>:9391/`

### 演示登录账号（开发环境）

- 用户名：`MP`
- 密码：`12345678`

> 首次启动会自动写入 `portal_auth.sqlite3`。

### 环境变量

- `RAGFLOW_API_KEY`：必填
- `RAGFLOW_BASE_URL`：默认 `http://127.0.0.1:9380`
- `PORTAL_DB_PATH`：默认 `./extensions/qms_agent_backend/data/portal_auth.sqlite3`
- `PORTAL_AUTH_SECRET`：可选，不填则基于 API Key 派生
- `PORTAL_TOKEN_TTL_SECONDS`：默认 `28800`

### 当前权限规则（P0）

- `visibility=public`：所有部门可见
- `visibility=dept`：仅 `owner_dept_id` 对应部门可见
- 若策略表无记录，系统会按智能体标题自动推断并落库：
  - 标题含 `公共/公用` 或以 `MP` 开头 -> `public`
  - 其他按标题关键字推断归属部门（Q/PLM/AP/MC，默认 MP）

### 前端调用的网关接口（P0）

- `POST /portal/v1/auth/login`
- `GET /portal/v1/me`
- `GET /portal/v1/resources?resource_type=agent&dept_id=...`
- `POST /portal/v1/sessions`
- `POST /portal/v1/chat`
- `GET /portal/v1/sessions/{session_id}/messages`

> 已扩展：`resource_type` 支持 `agent | kb | all`

### P1 增强能力（已落地）

1) 会话持久化（服务重启后可回放）

- 新增表：`portal_sessions`、`portal_messages`
- 创建会话与问答消息都会落库
- 重启后可通过 `session_id` 继续读取历史与继续问答

2) 策略管理接口（管理员）

- `GET /portal/v1/policies/resources?resource_type=agent|kb`
  - 查询资源权限策略（支持按 owner_dept_id / visibility / category 过滤）
- `PUT /portal/v1/policies/resources/{resource_type}/{resource_id}`
  - 修改资源策略：`owner_dept_id`、`visibility(public/dept)`、`allow_dept_ids`、`deny_dept_ids`、`category`

兼容保留：

- `GET /portal/v1/policies/agents`
- `PUT /portal/v1/policies/agents/{agent_id}`

3) 审计查询接口

- `GET /portal/v1/audit/logs`
  - 支持按 `dept_id/user_id/action/status/page/page_size` 查询
  - 非管理员仅可查询自己部门

### 管理策略请求示例

```json
PUT /portal/v1/policies/agents/{agent_id}
{
  "owner_dept_id": "dept_q",
  "visibility": "dept",
  "allow_dept_ids": ["dept_mp"],
  "deny_dept_ids": [],
  "allow_roles": ["admin", "member"]
}
```

### 批量把已有知识库设为“MP 公共 A 类”（管理员）

```http
POST /portal/v1/admin/bootstrap/public-kb-a
```

行为：

- 扫描当前 RAGFlow 所有已上传知识库
- 写入/更新策略为：`owner_dept_id=dept_mp`、`visibility=public`、`category=A`
- 管理员可后续再逐个调整

### 外部访问（非服务器账号）

如果普通部门同事无法直接访问 `http://127.0.0.1:9391`，请把门户服务发布到内网可达域名/地址：

- 方式1：在服务器上使用内网 IP + 防火墙放行 9391
- 方式2：用 Nginx/网关做反向代理（推荐）
- 方式3：挂到公司已有 API Gateway

门户代码本身已支持 `0.0.0.0` 监听，重点是网络与访问策略放通。
