"""Unified, on-demand diagnostics for local and optional runtime dependencies."""

from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
import re
import urllib.parse


def _result(key, name, status, required, message, connection_tested=False):
    return {
        "key": key,
        "name": name,
        "status": status,
        "required": bool(required),
        "message": str(message),
        "connection_tested": bool(connection_tested),
    }


def _safe_error(error, config):
    message = str(error or type(error).__name__)
    for key in (
        "duckmail_api_key", "yyds_api_key", "yyds_jwt",
        "cloudflare_api_key", "cloudmail_public_token",
        "grok2api_remote_app_key", "grok2api_remote_admin_password",
    ):
        secret = str(config.get(key) or "")
        if secret:
            message = message.replace(secret, "[REDACTED]")
    message = re.sub(r"(?i)(https?://)[^/@\s]+:[^/@\s]+@", r"\1[REDACTED]@", message)
    return message[:300]


def _check_configuration(config):
    from app_config import DEFAULT_CONFIG, validate_config_structure

    validation_config = dict(config)
    validation_config["cpa_base_url"] = DEFAULT_CONFIG["cpa_base_url"]
    validation_config["grok2api_remote_base"] = ""
    try:
        validate_config_structure(validation_config)
    except Exception as exc:
        return _result(
            "configuration", "Configuration", "ERROR", True,
            _safe_error(exc, config),
        )
    return _result(
        "configuration", "Configuration", "OK", True,
        "配置字段类型、取值范围和枚举校验通过",
    )


def _check_browser_runtime():
    missing = [
        package for package in ("DrissionPage", "curl_cffi")
        if importlib.util.find_spec(package) is None
    ]
    if missing:
        return _result(
            "browser", "Browser Runtime", "ERROR", True,
            "缺少运行依赖: " + ", ".join(missing),
        )
    return _result(
        "browser", "Browser Runtime", "OK", True,
        "DrissionPage 和 curl_cffi 已安装；浏览器启动测试尚未执行",
    )


def _check_proxy_config(config):
    from proxy_bridge import parse_proxy_url

    mode = str(config.get("proxy_mode") or "auto").lower()
    required = mode in {"single", "pool"}
    proxy = str(config.get("proxy") or "").strip()

    if mode == "single" or (mode == "auto" and proxy):
        parsed = parse_proxy_url(proxy)
        if not parsed or not parsed.hostname:
            return _result("proxy", "Proxy", "ERROR" if required else "WARN", required,
                           "代理地址格式无效")
    if mode == "pool":
        pool_file = str(config.get("proxy_pool_file") or "").strip()
        subscription = str(config.get("proxy_pool_subscription_url") or "").strip()
        if pool_file:
            path = Path(pool_file).expanduser()
            if not path.is_absolute():
                path = Path(__file__).resolve().parent / path
            if not path.is_file() or path.stat().st_size == 0:
                return _result("proxy", "Proxy", "ERROR", True,
                               "代理池文件不存在或为空")
        elif not subscription:
            return _result("proxy", "Proxy", "ERROR", True,
                           "代理池文件和订阅地址均未配置")
    if mode in {"direct", "auto"} and not proxy:
        return _result("proxy", "Proxy", "OK", False,
                       "当前使用直连网络，不需要代理节点")
    return _result(
        "proxy", "Proxy", "OK", required,
        "代理配置通过；联网探测尚未执行",
    )


def _check_mailbox_config(config):
    provider = str(config.get("email_provider") or "duckmail").lower()
    required_fields = {
        "duckmail": (),
        "yyds": ("yyds_api_key", "yyds_jwt"),
        "cloudflare": ("cloudflare_api_base",),
        "cloudmail": ("cloudmail_api_base", "cloudmail_public_token", "cloudmail_domains"),
        "outlook": ("outlook_accounts_file",),
    }
    missing = []
    if provider == "yyds":
        if not any(config.get(key) for key in required_fields[provider]):
            missing.append("yyds_api_key 或 yyds_jwt")
    else:
        missing = [key for key in required_fields.get(provider, ()) if not config.get(key)]
    if missing:
        return _result(
            "mailbox", "Mailbox", "ERROR", True,
            "邮箱服务配置缺少: " + ", ".join(missing),
        )
    if provider == "outlook":
        try:
            from outlook_mailbox_pool import get_outlook_mailbox_pool_capacity
            count = get_outlook_mailbox_pool_capacity(config["outlook_accounts_file"])
        except Exception as exc:
            return _result("mailbox", "Mailbox", "ERROR", True,
                           _safe_error(exc, config))
        if count < 1:
            return _result("mailbox", "Mailbox", "ERROR", True,
                           "Outlook 邮箱池没有有效账号")
        return _result("mailbox", "Mailbox", "OK", True,
                       "Outlook 邮箱池格式有效，共 %s 个账号；OAuth/IMAP/Graph 尚未测试" % count)
    return _result(
        "mailbox", "Mailbox", "OK", True,
        "%s 配置字段齐全；邮箱服务连接尚未测试" % provider,
    )


def _directory_writable(path):
    target = Path(path).expanduser()
    if target.exists():
        return target.is_dir() and os.access(str(target), os.W_OK)
    parent = target.parent
    return parent.is_dir() and os.access(str(parent), os.W_OK)


def _check_cpa_config(config):
    if not config.get("cpa_export_enabled"):
        return _result("cpa", "CPA", "OK", False, "CPA/OIDC 导出已关闭")
    from cpa_export import CpaExportSettings, _load_mint_and_export

    try:
        settings = CpaExportSettings.from_config(config)
        parsed = urllib.parse.urlsplit(settings.base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("CPA Base URL 无效")
        _load_mint_and_export(settings.tools_dir)
    except Exception as exc:
        return _result("cpa", "CPA", "WARN", False,
                       "CPA 模块不可用: " + _safe_error(exc, config))
    if not _directory_writable(settings.auth_dir):
        return _result("cpa", "CPA", "WARN", False,
                       "CPA 输出目录不可写")
    if settings.copy_to_hotload and (
        settings.hotload_dir is None or not _directory_writable(settings.hotload_dir)
    ):
        return _result("cpa", "CPA", "WARN", False,
                       "CPA 热加载目录不可用或不可写")
    return _result(
        "cpa", "CPA", "OK", False,
        "CPA 模块、输出目录和可选热加载目录配置通过；服务连接尚未测试",
    )


def _check_grok2api_config(config):
    local_enabled = bool(config.get("grok2api_auto_add_local"))
    remote_enabled = bool(config.get("grok2api_auto_add_remote"))
    if not local_enabled and not remote_enabled:
        return _result("grok2api", "Grok2API", "OK", False,
                       "本地和远端自动入池均已关闭")

    warnings = []
    if local_enabled:
            configured_path = str(config.get("grok2api_local_token_file") or "").strip()
        path = Path(configured_path or (Path(__file__).resolve().parent / "token.json"))
        path = path.expanduser().resolve()
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(data, dict):
                    raise ValueError("token 文件根节点必须是 JSON object")
                pool = data.get(config.get("grok2api_pool_name", "ssoBasic"), [])
                if not isinstance(pool, list):
                    raise ValueError("目标 token 池必须是 JSON array")
            except Exception as exc:
                warnings.append("本地 token 文件不可用: " + _safe_error(exc, config))
        elif not os.access(str(path.parent), os.W_OK):
            warnings.append("本地 token 文件所在目录不可写")

    if remote_enabled:
        base = str(config.get("grok2api_remote_base") or "").strip()
        parsed = urllib.parse.urlsplit(base)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            warnings.append("远端 Base URL 无效")
        has_app_key = bool(config.get("grok2api_remote_app_key"))
        has_username = bool(config.get("grok2api_remote_admin_username"))
        has_password = bool(config.get("grok2api_remote_admin_password"))
        if (has_username != has_password) or (has_app_key and (has_username or has_password)):
            warnings.append("远端认证配置不完整或互相冲突")
        elif not (has_app_key or (has_username and has_password)):
            warnings.append("远端认证信息不完整")

    if warnings:
        return _result("grok2api", "Grok2API", "WARN", False,
                       "；".join(warnings))
    return _result(
        "grok2api", "Grok2API", "OK", False,
        "本地 token 文件和远端入池配置通过；远端服务连接尚未测试",
    )


def _http_reachable(url, proxies=None):
    from curl_cffi import requests

    response = requests.get(
        url, proxies=proxies or {}, timeout=10, allow_redirects=False, verify=True
    )
    status = int(response.status_code)
    if status >= 500:
        raise RuntimeError("服务返回 HTTP %s" % status)
    return status


def _check_proxy_connection(config, log_callback=None):
    mode = str(config.get("proxy_mode") or "auto").lower()
    if mode in {"single", "pool"}:
        from proxy_pool import get_manager

        manager = get_manager(config=config, log=log_callback)
        manager.reload_sources(force=True)
        results = manager.probe_all(force=True)
        healthy = sum(1 for item in results if item.get("status") == "healthy")
        if not results or not healthy:
            raise RuntimeError("代理节点探测失败：0/%s 个节点健康" % len(results))
        return "代理节点健康: %s/%s" % (healthy, len(results))

    proxy = ""
    if mode == "auto":
        proxy = str(config.get("proxy") or "").strip()
    proxies = {"http": proxy, "https": proxy} if proxy else {}
    status = _http_reachable("https://example.com/", proxies=proxies)
    return "网络可达（HTTP %s）" % status


def _check_browser_connection():
    import registration_browser

    try:
        registration_browser.start_browser(use_proxy=False)
        if registration_browser.browser is None or registration_browser.page is None:
            raise RuntimeError("Chromium 未创建有效页面")
    finally:
        registration_browser.stop_browser()
    return "Chromium 启动和页面创建成功"


def _check_mailbox_connection(config):
    import mail_service

    previous_config = mail_service.config
    mail_service.config = config
    try:
        provider = str(config.get("email_provider") or "duckmail").lower()
        if provider == "outlook":
            from outlook_mailbox_pool import probe_outlook_mailbox_pool

            summary = probe_outlook_mailbox_pool(config["outlook_accounts_file"])
            if summary["healthy"] < 1:
                raise RuntimeError("Outlook 邮箱池中没有可用账号")
            return "Outlook 可用邮箱: %s/%s" % (summary["healthy"], summary["count"])
        if provider == "duckmail":
            domains = mail_service.get_domains()
        elif provider == "yyds":
            domains = mail_service.yyds_get_domains()
        elif provider == "cloudflare":
            domains = mail_service.cloudflare_get_domains(
                mail_service.get_cloudflare_api_base(),
                api_key=mail_service.get_cloudflare_api_key(),
            )
        elif provider == "cloudmail":
            result = mail_service.cloudmail_preflight(
                auth_timeout=0, defer_until_slot=False
            )
            if result is False:
                raise RuntimeError("Cloud Mail 鉴权预检未通过")
            return "Cloud Mail Public Token 验证通过"
        else:
            raise ValueError("未知邮箱服务商: %s" % provider)
        if not domains:
            raise RuntimeError("服务未返回可用邮箱域名")
        return "服务连接成功，可用域名: %s" % len(domains)
    finally:
        mail_service.config = previous_config


def _check_cpa_connection(config):
    from cpa_export import CpaExportSettings

    settings = CpaExportSettings.from_config(config)
    status = _http_reachable(settings.base_url, proxies={
        "http": settings.proxy, "https": settings.proxy,
    } if settings.proxy else {})
    return "CPA 服务端点可达（HTTP %s；未执行 OAuth 授权）" % status


def _check_grok2api_connection(config):
    base = str(config.get("grok2api_remote_base") or "").strip().rstrip("/")
    if not base:
        raise RuntimeError("远端 Base URL 未配置")
    status = _http_reachable(base)
    return "远端服务可达（HTTP %s；未验证管理凭据）" % status


def run_health_checks(config, connectivity=False, log_callback=None):
    """Run offline checks, optionally followed by explicit read-only connectivity probes."""
    cfg = dict(config or {})
    checks = [
        _check_configuration(cfg),
        _check_browser_runtime(),
        _check_proxy_config(cfg),
        _check_mailbox_config(cfg),
        _check_cpa_config(cfg),
        _check_grok2api_config(cfg),
    ]
    probes = {
        "browser": _check_browser_connection,
        "proxy": lambda: _check_proxy_connection(cfg, log_callback=log_callback),
        "mailbox": lambda: _check_mailbox_connection(cfg),
        "cpa": lambda: _check_cpa_connection(cfg),
        "grok2api": lambda: _check_grok2api_connection(cfg),
    }
    if connectivity:
        for check in checks:
            if check["status"] == "ERROR" or (
                check["key"] == "cpa" and not cfg.get("cpa_export_enabled")
            ) or (
                check["key"] == "grok2api"
                and not cfg.get("grok2api_auto_add_remote")
            ):
                continue
            if check["key"] not in probes:
                continue
            try:
                check["message"] += "；" + probes[check["key"]]()
                check["connection_tested"] = True
            except Exception as exc:
                check["status"] = "ERROR" if check["required"] else "WARN"
                check["message"] = _safe_error(exc, cfg)
                check["connection_tested"] = True

    errors = sum(1 for item in checks if item["required"] and item["status"] == "ERROR")
    warnings = sum(1 for item in checks if item["status"] == "WARN")
    ready = errors == 0
    return {
        "ok": ready,
        "ready": ready,
        "result": "Ready" if ready else "Needs attention",
        "connectivity": bool(connectivity),
        "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "error_count": errors,
        "warning_count": warnings,
        "checks": checks,
    }
