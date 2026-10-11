"""Small presentation helpers; financial rules live in the domain package."""

from decimal import Decimal, InvalidOperation
from html import escape
from sqlite3 import Error as SQLiteError
from uuid import uuid4

import streamlit as st

from rideorown.domain.currency import format_inr, indian_number, parse_rupees

INR_AXIS_LABEL = r"'₹' + replace(format(datum.value, '.0f'), /\B(?=(\d{2})*\d{3}(?!\d))/g, ',')"


def queue_notice(message, *, level="success"):
    """Carry one action result through the next Streamlit rerun."""
    st.session_state["_action_notice"] = {"message": str(message), "level": level}


def notify(message, *, level="success"):
    """Show an accessible inline result and a dismissible notice at any scroll position."""
    if hasattr(message, "errors"):
        message = " ".join(
            dict.fromkeys(error["msg"].removeprefix("Value error, ") for error in message.errors())
        )
    icons = {"success": "✓", "info": "i", "warning": "!", "error": "!"}
    level = level if level in icons else "info"
    message = str(message)
    # A fresh parent also reopens a dismissed notice when the same action repeats.
    with st.container(key=f"action_notice_{uuid4().hex}"):
        st.markdown(
            f'<details class="action-notification action-notification--{level}" open>'
            '<summary aria-label="Dismiss notification" title="Dismiss notification">×</summary>'
            f'<div role="{"alert" if level == "error" else "status"}" '
            f'aria-live="{"assertive" if level == "error" else "polite"}" aria-atomic="true">'
            f'<span class="action-notification-icon" aria-hidden="true">{icons[level]}</span>'
            f'<span class="action-notification-message">{escape(message)}</span>'
            "</div></details>",
            unsafe_allow_html=True,
        )
    getattr(st, level)(message)


def render_notice():
    if notice := st.session_state.pop("_action_notice", None):
        notify(notice["message"], level=notice["level"])


def money(value: Decimal | int | float | None, *, places=0) -> str:
    if value is None:
        return "Not entered"
    return format_inr(value, places=places)


def amount_input(label, value=None, *, key=None, help=None):
    currency = "₹" in label
    if currency:
        help = (help + " " if help else "") + (
            "Enter rupees, for example 5,00,000 or 500000.50. Up to two decimal places (paise)."
        )
    rendered = "" if value is None else str(value)
    if currency and value is not None:
        places = max(0, -Decimal(str(value)).as_tuple().exponent)
        rendered = indian_number(value, places=places)
    return st.text_input(
        label,
        value=rendered,
        key=key,
        help=help,
        placeholder=("e.g. 5,00,000" if currency else "Enter a value") if value is None else None,
    )


rupee_value = parse_rupees


def inr_frame(frame, columns):
    """Format displayed currency without changing numeric chart/export data."""
    import pandas as pd

    return frame.style.format(
        {
            column: lambda value: money(value, places=2) if pd.notna(value) else "Not entered"
            for column in columns
        }
    )


def decimal_value(value, label, *, optional=False, positive=False, allow_negative=False):
    cleaned = str(value).strip().replace(",", "")
    if not cleaned:
        if optional:
            return None
        raise ValueError(f"Enter {label}.")
    try:
        result = Decimal(cleaned)
    except InvalidOperation as exc:
        raise ValueError(f"{label} must be a number.") from exc
    if not result.is_finite() or (result < 0 and not allow_negative) or (positive and result <= 0):
        qualifier = (
            "greater than zero"
            if positive
            else "a finite number"
            if allow_negative
            else "zero or greater"
        )
        raise ValueError(f"{label} must be {qualifier}.")
    return result


def save(repo, workspace, *, stop_on_error=True):
    from rideorown.services.application import save_workspace

    try:
        save_workspace(repo, workspace)
    except (ValueError, OSError, SQLiteError) as exc:
        st.session_state.pop("_action_notice", None)
        st.session_state.pop("prepared_backup", None)
        try:
            st.session_state["workspace"] = repo.load()
        except (ValueError, OSError, SQLiteError):
            if previous := st.session_state.get("_saved_workspace"):
                st.session_state["workspace"] = previous.model_copy(deep=True)
        else:
            st.session_state["_saved_workspace"] = st.session_state["workspace"].model_copy(
                deep=True
            )
        message = f"Your changes could not be saved: {exc}"
        if stop_on_error:
            notify(message, level="error")
            st.stop()
        queue_notice(message, level="error")
        return False
    st.session_state["workspace"] = workspace
    st.session_state["_saved_workspace"] = workspace.model_copy(deep=True)
    st.session_state.pop("prepared_backup", None)
    return True


def page_intro(kicker: str, title: str, description: str):
    with st.container(key="page_intro"):
        st.markdown(f'<div class="eyebrow">{escape(kicker)}</div>', unsafe_allow_html=True)
        st.title(title)
        st.markdown(
            f'<p class="page-description">{escape(description)}</p>', unsafe_allow_html=True
        )


def metric_card(label, value, hint="", *, tone="default"):
    variant = tone if tone in {"default", "teal", "slate"} else "default"
    st.markdown(
        f'<div class="metric-card metric-card--{variant}">'
        f'<div class="metric-label">{escape(str(label))}</div>'
        f'<div class="metric-value">{escape(str(value))}</div>'
        f'<div class="metric-hint">{escape(str(hint))}</div></div>',
        unsafe_allow_html=True,
    )
