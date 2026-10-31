"""Normal, user-authorized account sign-in; verification challenges remain manual."""

import time


SIGNIN_URL = "https://accounts.x.ai/sign-in"


class LoginRejected(RuntimeError):
    pass


class LoginCancelled(RuntimeError):
    pass


def _wait_for_input(page, selector, deadline, cancelled):
    while time.monotonic() < deadline:
        if cancelled():
            raise LoginCancelled("重新登录已停止")
        element = page.ele(selector, timeout=0.2)
        if element is not None:
            return element
        time.sleep(0.2)
    return None


def _click_action(page, labels):
    for button in page.eles("tag:button"):
        label = str(button.text or "").strip().lower()
        if label in labels:
            button.click()
            return True
    return False


def _session_cookie(page):
    cookies = page.cookies(all_domains=True, all_info=True) or []
    if isinstance(cookies, dict):
        cookies = [{"name": name, "value": value} for name, value in cookies.items()]
    for name in ("sso", "sso-rw"):
        for cookie in cookies:
            if cookie.get("name") == name and cookie.get("value"):
                return str(cookie["value"])
    return ""


def login_with_password(email, password, page, cancelled=lambda: False, log=lambda _text: None, timeout=120):
    """Sign in through the official page and wait for its session cookie."""
    if not email or "@" not in email or not password:
        raise ValueError("账号缺少有效邮箱或密码")

    page.get(SIGNIN_URL)
    deadline = time.monotonic() + timeout
    email_input = _wait_for_input(page, 'css:input[type="email"]', time.monotonic() + 8, cancelled)
    if email_input is None:
        if not _click_action(page, {"login with email", "continue with email", "使用邮箱登录"}):
            raise RuntimeError("登录页没有邮箱登录入口")
        email_input = _wait_for_input(page, 'css:input[type="email"]', deadline, cancelled)
    if email_input is None:
        raise RuntimeError("登录页未出现邮箱输入框")
    email_input.input(email, clear=True)

    password_input = page.ele('css:input[type="password"]', timeout=0.2)
    if password_input is None:
        if not _click_action(page, {"next", "continue", "下一步"}):
            raise RuntimeError("登录页没有继续按钮")
        password_input = _wait_for_input(page, 'css:input[type="password"]', deadline, cancelled)
    if password_input is None:
        raise RuntimeError("登录页未出现密码输入框")
    password_input.input(password, clear=True)
    if not _click_action(page, {"sign in", "log in", "login", "登录", "continue"}):
        raise RuntimeError("登录页没有提交按钮")

    log("[*] 已提交登录表单，等待官方验证和 SSO Cookie；如需人工验证请在浏览器完成")
    while time.monotonic() < deadline:
        if cancelled():
            raise LoginCancelled("重新登录已停止")
        token = _session_cookie(page)
        if token:
            return token
        page_text = str(page.run_js("return document.body ? document.body.innerText : ''") or "").lower()
        if any(message in page_text for message in (
            "incorrect password", "invalid credentials", "password is incorrect",
            "密码错误", "账号或密码错误",
        )):
            raise LoginRejected("官方页面拒绝了邮箱或密码")
        time.sleep(0.25)
    raise TimeoutError("等待官方登录成功超时")
