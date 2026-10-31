"""Offline tests for normal email-password login page interactions."""

import unittest

from reauthentication import (
    LoginCancelled, LoginRejected, SIGNIN_URL, login_with_password,
)


class Element:
    def __init__(self, text="", on_click=None):
        self.text = text
        self.value = ""
        self.on_click = on_click

    def input(self, value, clear=False):
        self.value = value

    def click(self):
        if self.on_click:
            self.on_click()


class Page:
    def __init__(self, stepped=False, invalid=False):
        self.stepped = stepped
        self.invalid = invalid
        self.loaded = ""
        self.email_input = Element()
        self.password_input = Element()
        self.password_visible = not stepped
        self.submitted = False

    def get(self, url):
        self.loaded = url

    def ele(self, selector, timeout=0):
        if "email" in selector:
            return self.email_input
        if "password" in selector:
            return self.password_input if self.password_visible else None
        return None

    def eles(self, selector):
        assert selector == "tag:button"
        return [
            Element("Next", on_click=lambda: setattr(self, "password_visible", True)),
            Element("Sign In", on_click=lambda: setattr(self, "submitted", True)),
        ]

    def cookies(self, all_domains=False, all_info=False):
        return [{"name": "sso", "value": "fresh-session"}] if self.submitted and not self.invalid else []

    def run_js(self, script):
        return "invalid credentials" if self.invalid else ""


class LoginPageTests(unittest.TestCase):
    def test_one_page_login_uses_normal_inputs_and_session_cookie(self):
        page = Page()
        token = login_with_password(
            "user@example.com", "password", page, timeout=2,
        )
        self.assertEqual(token, "fresh-session")
        self.assertEqual(page.loaded, SIGNIN_URL)
        self.assertEqual(page.email_input.value, "user@example.com")
        self.assertEqual(page.password_input.value, "password")

    def test_two_step_login_uses_next_button(self):
        page = Page(stepped=True)
        token = login_with_password("user@example.com", "password", page, timeout=2)
        self.assertEqual(token, "fresh-session")
        self.assertTrue(page.password_visible)

    def test_invalid_credentials_leave_no_session(self):
        with self.assertRaises(LoginRejected):
            login_with_password(
                "user@example.com", "wrong", Page(invalid=True), timeout=2,
            )

    def test_cancel_does_not_continue_signin(self):
        page = Page()
        with self.assertRaises(LoginCancelled):
            login_with_password(
                "user@example.com", "password", page,
                cancelled=lambda: True, timeout=2,
            )
        self.assertFalse(page.submitted)

    def test_missing_credentials_are_rejected_before_navigation(self):
        page = Page()
        with self.assertRaises(ValueError):
            login_with_password("", "", page)
        self.assertFalse(page.loaded)


if __name__ == "__main__":
    unittest.main()
