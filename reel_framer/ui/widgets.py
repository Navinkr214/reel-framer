"""Small Streamlit helpers shared by the tabs.

- choice():  a selectbox or radio over {value: label} that starts on the saved value
- slider():  a slider whose saved value is clamped into its range first (a value
             saved by an older version must not crash the page)
- human_size(): bytes as KB / MB / GB for display

Not in here: any tab's content (create_tab.py, settings_tab.py).
Called by: create_tab.py, settings_tab.py.
"""
from __future__ import annotations

from typing import Any

import streamlit as st

_BYTE_UNITS = ("B", "KB", "MB", "GB", "TB")
_UNIT_STEP = 1000  # SI units, as macOS Finder shows sizes


def choice(label: str, options: dict[Any, str], current: Any, key: str, *, radio: bool = False, **kwargs) -> Any:
    values = list(options)
    index = values.index(current) if current in values else 0
    if radio:
        return st.radio(label, values, index=index, format_func=options.get, key=key, horizontal=True, **kwargs)
    return st.selectbox(label, values, index=index, format_func=options.get, key=key, **kwargs)


def slider(label: str, low: float, high: float, value: float, step: float, key: str, **kwargs) -> float:
    return st.slider(label, low, high, min(high, max(low, value)), step, key=key, **kwargs)


def human_size(size: float) -> str:
    for unit in _BYTE_UNITS:
        if size < _UNIT_STEP or unit == _BYTE_UNITS[-1]:
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= _UNIT_STEP
    return f"{size:.1f} {_BYTE_UNITS[-1]}"
