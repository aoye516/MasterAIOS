# 贡献指南

感谢你对 Native AI OS (AIOS) 项目的关注！

## 🚀 快速开始

### 环境要求

- Python 3.11+
- PostgreSQL 15+ (可选，开发环境可用SQLite)
- Redis 7+ (可选)

### 本地开发

1. **克隆仓库**
```bash
git clone https://github.com/aoye516/AIOS.git
cd AIOS
```

2. **安装依赖**
```bash
python3 -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

3. **配置环境变量**
```bash
cp .env.example .env
# 编辑 .env 文件，填入必要配置
```

4. **初始化数据库**
```bash
# SQLite (开发环境)
python scripts/init_sqlite.py

# 或 PostgreSQL (生产环境)
psql -f scripts/init_db.sql
```

5. **运行应用**
```bash
# WebSocket 模式（推荐）
python run_ws.py

# 或 HTTP 模式
uvicorn app.main:app --reload
```

## 📝 开发规范

### 代码风格

- 遵循 PEP 8
- 使用类型注解
- 函数/方法名：`snake_case`
- 类名：`PascalCase`
- 常量：`UPPER_SNAKE_CASE`

### 提交规范

使用语义化提交消息：

```
feat: 添加新功能
fix: 修复bug
docs: 更新文档
style: 代码格式调整
refactor: 重构代码
test: 添加测试
chore: 构建/工具变更
```

### 测试

所有新功能必须包含测试：

```bash
# 运行所有测试
pytest

# 运行特定测试
pytest tests/test_agents/test_life_manager.py

# 生成覆盖率报告
pytest --cov=app tests/
```

## 🏗️ 项目结构

```
AIOS/
├── app/
│   ├── agents/         # Agent层
│   ├── services/       # 服务层
│   ├── channels/       # 渠道层
│   ├── api/           # API路由
│   └── core/          # 核心配置
├── tests/             # 测试文件
├── docs/              # 文档
├── scripts/           # 脚本
└── run_ws.py         # WebSocket启动脚本
```

## 📚 文档

- [架构文档](docs/architecture.md) - 系统架构设计
- [API文档](docs/api_reference.md) - API接口定义
- [开发计划](docs/development_plan.md) - 开发路线图
- [项目演化](docs/project_evolution.md) - 历史变更记录

## 🐛 报告问题

在 [Issues](https://github.com/aoye516/AIOS/issues) 中报告问题时，请包含：

- 问题描述
- 复现步骤
- 期望行为
- 实际行为
- 环境信息（Python版本、操作系统等）

## 🔄 提交 Pull Request

1. Fork 本仓库
2. 创建功能分支：`git checkout -b feature/your-feature`
3. 提交更改：`git commit -m 'feat: 添加某功能'`
4. 推送分支：`git push origin feature/your-feature`
5. 创建 Pull Request

## 📖 开发流程

### 添加新 Agent

1. 在 `app/agents/` 创建新文件
2. 继承 `BaseAgent` 类
3. 实现 `execute()` 方法
4. 在 `app/main.py` 注册 Agent
5. 编写测试

### 添加新服务

1. 在 `app/services/` 创建新文件
2. 在 `app/core/container.py` 注册服务
3. 通过依赖注入使用服务
4. 编写测试

## ⚠️ 注意事项

- 不要提交 `.env` 文件
- 不要提交包含 API Key 的代码
- 测试必须通过才能提交
- 保持代码风格一致

## 联系方式

- Issues: https://github.com/aoye516/MasterAIOS/issues

---

感谢你的贡献！🎉
