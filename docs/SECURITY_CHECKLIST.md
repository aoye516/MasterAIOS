# 安全检查清单

本文档用于确保敏感信息不会泄露到版本控制系统。

## ✅ 已实施的安全措施

### 1. 配置管理机制
- ✅ 使用 Pydantic Settings 管理配置
- ✅ 所有敏感配置从环境变量读取
- ✅ 配置文件：`app/core/config.py`
- ✅ 默认值为空字符串或示例值

### 2. 环境变量文件
- ✅ `.env` 已加入 `.gitignore`
- ✅ `.env.example` 提供配置模板（脱敏）
- ✅ 测试环境使用环境变量或mock值

### 3. 文档安全
- ✅ 服务器密码已从文档中删除
- ✅ API密钥已从文档中删除
- ✅ 使用示例值代替真实配置

## 🔐 敏感信息清单

以下信息**绝不能**提交到版本控制：

### LLM配置
- `SILICONFLOW_API_KEY`
- 其他 LLM 提供商的 API Key

### 飞书配置
- `FEISHU_APP_SECRET`
- （App ID 可以公开，不算敏感）

### 服务器配置
- SSH密码
- 数据库密码

### 其他
- `SECRET_KEY`（生产环境）
- `SMTP_PASSWORD`
- `QWEATHER_API_KEY`

## 📋 提交前检查

每次提交代码前运行以下检查：

```bash
# 1. 检查是否有硬编码的API key
grep -rE "sk-[a-zA-Z0-9]{30,}" . --include="*.py" --include="*.md" --exclude-dir=.git --exclude-dir=venv

# 2. 检查是否有密码泄露
grep -rE "password.*=.*['\"][^'\"]{5,}" . --include="*.py" --include="*.md" --exclude-dir=.git | grep -v "smtp_password.*=.*\"\"\|test\|mock"

# 3. 确认.env未加入版本控制
git ls-files | grep "\.env$"

# 4. 检查staged文件
git diff --cached --name-only
```

## 🛡️ 最佳实践

### 开发环境
1. 复制 `.env.example` 为 `.env`
2. 填入真实配置（本地开发用）
3. **永远不要**提交 `.env` 文件

### 测试环境
1. 使用 `.env.test` 或环境变量
2. 使用 mock 值（如 `test-api-key`）
3. 真实集成测试从环境变量读取

### 生产环境
1. 在服务器上直接编辑 `/claude/aios/.env`
2. 配置真实的 API Key 和密钥
3. 部署时使用 `--exclude=.env` 排除环境文件

## 🚨 泄露应急响应

如果敏感信息已推送到GitHub：

1. **立即更换密钥**
   - LLM API Key
   - 飞书 App Secret
   - 服务器密码

2. **删除GitHub仓库并重建**（推荐）
   - 删除包含敏感信息的仓库
   - 清理本地commit历史
   - 重新推送干净的代码

3. **更新服务器配置**
   - SSH到服务器
   - 更新 `/claude/aios/.env`
   - 重启服务

---

**最后更新**: 2026-04-11
