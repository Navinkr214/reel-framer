"""Password gate for a hosted copy of the app.

- $APP_PASSWORD set: each browser session must enter it before anything else
  is drawn (the script stops before any tab or widget exists, so nothing can be
  triggered without it). The comparison is constant-time.
- Hosted (hosting.hosted()) but no APP_PASSWORD: the app refuses to start, so
  a deployment can never be left open by mistake.
- Neither (running on your own computer): no gate.

The password is asked again in each new browser session (a reload starts one);
browsers can save it like any other login form.

Not in here: where APP_PASSWORD comes from (render.yaml generates one).
Called by: app.py.
"""
from __future__ import annotations

import hmac
import os

import streamlit as st

from .. import hosting

_PASSED = "password_gate_passed"


def password_matches(given: str, expected: str) -> bool:
    return hmac.compare_digest(given.encode("utf-8"), expected.encode("utf-8"))


def require_password() -> None:
    """Return when the visitor may use the app; otherwise draw the gate and stop the script."""
    expected = os.environ.get("APP_PASSWORD", "")
    if not expected:
        if hosting.hosted():
            st.error("This server has no APP_PASSWORD set, so the app stays closed. "
                     "Set APP_PASSWORD in the host's environment settings and restart.")
            st.stop()
        return
    if st.session_state.get(_PASSED):
        return
    with st.form("password_gate"):
        given = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Open", type="primary")
    if submitted:
        if password_matches(given, expected):
            st.session_state[_PASSED] = True
            st.rerun()
        st.error("Wrong password.")
    st.stop()
