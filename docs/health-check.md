# 依赖健康检查

Grok Register 提供统一的依赖健康报告，覆盖配置、浏览器运行环境、代理、邮箱服务、CPA 和 Grok2API。

## CLI

静态检查完全离线，不会启动浏览器或访问外部服务：

```bash
python grok_register_ttk.py check
```

只有明确要求时才执行联网检查：

```bash
python grok_register_ttk.py check --connectivity
```

所有必需依赖检查通过时，命令退出码为 `0`；存在必需依赖错误时，退出码为 `1`。

## GUI 和 WebUI

- GUI：点击“静态依赖检查”或“联网依赖检查”。
- WebUI：点击顶部的 **Static check** 或 **Connectivity check**。
- API：向 `POST /api/health-check` 发送 `{"connectivity": false}` 执行静态检查，或发送 `{"connectivity": true}` 执行联网检查。

API 仅允许同源 WebUI 请求，响应包含 `Cache-Control: no-store`。注册任务或其他维护操作运行期间，检查请求会被拒绝。

## 状态含义

每项检查返回 `OK`、`WARN` 或 `ERROR)，并标记该依赖是必需还是可选。

- 必需依赖出现 `ERROR` 时，整体结果变为 `Needs attention`。
- 可选依赖出现 `WARN` 不会阻断整体 `Ready`。CPA 和 Grok2API 属于可选后处理集成。
- 静态检查只读取本地配置、已安装依赖、本地文件和目录状态，不会发起网络请求。
- 联网检查只在用户主动触发后执行，检查浏览器启动、代理连通性、所选邮箱服务，以及已启用的可选服务端点。
- CPA 联网检查不会启动 OAuth 授权；Grok2API 联网检查只验证服务端点可达，不验证远端管理凭据。
- 健康检查不会启动注册、创建邮箱、导入 Token 或写入账号数据，也不会自动插入注册流程。

健康报告不会输出邮箱密码、Refresh Token、API Key、代理凭据或访问令牌。
