"""Session-local navigation and persistence."""

from pathlib import Path
from sqlite3 import Error as SQLiteError

import streamlit as st

from rideorown.services.application import get_repository, initialize_workspace
from rideorown.services.backup import export_backup, restore_backup
from rideorown.ui.components import notify, queue_notice, render_notice
from rideorown.ui.screens import capture, comparison, patterns, scenario, travel_settings

SCREENS = {
    "1 · Cab receipts": capture.render,
    "2 · Advanced travel settings": travel_settings.render,
    "3 · Cab forecast": patterns.render,
    "4 · New car plan": scenario.render,
    "5 · Comparison & decision": comparison.render,
}

LEGACY_SCREENS = {
    "2 · Cab forecast": "3 · Cab forecast",
    "3 · Car costs": "4 · New car plan",
    "4 · Five-year decision": "5 · Comparison & decision",
}


def _go_to(screen):
    st.session_state["journey_step"] = screen
    queue_notice(f"{screen.split(' · ')[1]} opened.", level="info")


def _screen_changed():
    _go_to(st.session_state["journey_step"])


def _reload_saved_workspace(repo):
    try:
        saved = repo.load()
    except (ValueError, OSError, SQLiteError) as exc:
        st.session_state["workspace_reload_error"] = str(exc)
        return
    screen = st.session_state.get("journey_step")
    st.session_state.clear()
    st.session_state["workspace"] = saved
    st.session_state["_saved_workspace"] = saved.model_copy(deep=True)
    if screen in SCREENS:
        st.session_state["journey_step"] = screen
    st.session_state["workspace_reload_notice"] = True


def _journey_controls(screen):
    screens = list(SCREENS)
    position = screens.index(screen)
    with st.container(key="journey_controls"):
        back, forward = st.columns(2)
        if position:
            back.button(
                f"Back to {screens[position - 1].split(' · ')[1]}",
                icon=":material/arrow_back:",
                on_click=_go_to,
                args=(screens[position - 1],),
                width="stretch",
            )
        else:
            back.markdown(
                '<p class="journey-note">Next: set up your travel groups.</p>',
                unsafe_allow_html=True,
            )
        if position < len(screens) - 1:
            forward.button(
                f"Continue to {screens[position + 1].split(' · ')[1]}",
                type="primary",
                icon=":material/arrow_forward:",
                on_click=_go_to,
                args=(screens[position + 1],),
                width="stretch",
            )
        st.caption("Save changes before continuing.")


THEME_PATH = Path(__file__).with_name("theme.css")

BRAND = """
<div class="brand">
  <div class="brand-mark" aria-hidden="true">
    <svg width="25" height="25" viewBox="0 0 28 28" fill="none">
      <path d="M6 22V10a4 4 0 0 1 4-4h10M16 2l4 4-4 4M12 22h10M18 18l4 4-4 4"
        stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
      <circle cx="6" cy="22" r="2" fill="currentColor"/>
    </svg>
  </div>
  <div class="brand-name">Ride<span>Or</span>Own</div>
</div>
<div class="brand-tagline">Make room for a better<br>travel decision.</div>
"""


def run():
    st.set_page_config(
        page_title="RideOrOwn · Car or cabs over five years?",
        page_icon=":material/alt_route:",
        layout="wide",
    )
    st.markdown(
        "<style>" + THEME_PATH.read_text(encoding="utf-8") + "</style>", unsafe_allow_html=True
    )
    repo = get_repository()
    if "workspace" not in st.session_state:
        st.session_state["workspace"] = initialize_workspace(repo)
    workspace = st.session_state["workspace"]
    if "_saved_workspace" not in st.session_state:
        st.session_state["_saved_workspace"] = workspace.model_copy(deep=True)
    if previous_screen := LEGACY_SCREENS.get(st.session_state.get("journey_step")):
        st.session_state["journey_step"] = previous_screen

    with st.sidebar:
        st.markdown(BRAND, unsafe_allow_html=True)
        st.markdown(
            '<div class="sidebar-label">Your decision, in five steps</div>', unsafe_allow_html=True
        )
        screen = st.radio(
            "Your journey",
            list(SCREENS),
            key="journey_step",
            label_visibility="collapsed",
            on_change=_screen_changed,
        )
        position = list(SCREENS).index(screen) + 1
        st.progress(position / len(SCREENS))
        st.caption(f"Step {position} of {len(SCREENS)} · {screen.split(' · ')[1]}")
        st.divider()
        st.markdown(
            '<div class="workspace-card">'
            '<div class="sidebar-label">Your workspace</div>'
            f'<div class="workspace-count">{len(workspace.receipts)} receipts</div>'
            '<div class="workspace-detail">Forecast begins '
            f"{workspace.forecast_start:%b %Y}<br>60 months · Bengaluru</div></div>",
            unsafe_allow_html=True,
        )
        st.button(
            "Reload saved workspace",
            on_click=_reload_saved_workspace,
            args=(repo,),
            help="Loads the last saved data into this session and discards unsaved form edits. Save your edits first.",
            width="stretch",
        )
        if error := st.session_state.pop("workspace_reload_error", None):
            notify(f"Saved workspace could not be reloaded: {error}", level="error")
        if st.session_state.pop("workspace_reload_notice", False):
            notify("Saved workspace reloaded.")
        with st.expander("Backup & restore"):
            st.caption("Includes receipts and personal locations. Keep your backup private.")
            if st.button("Prepare workspace backup"):
                st.session_state.pop("prepared_backup", None)
                try:
                    st.session_state["prepared_backup"] = export_backup(repo, workspace)
                    notify("Backup ready to download.")
                except (ValueError, OSError, SQLiteError) as exc:
                    notify(f"Backup could not be prepared: {exc}", level="error")
            if "prepared_backup" in st.session_state:
                st.download_button(
                    "Download workspace backup",
                    data=st.session_state["prepared_backup"],
                    file_name="rideorown-backup.zip",
                    mime="application/zip",
                    on_click=queue_notice,
                    args=("Download requested: workspace backup.",),
                    kwargs={"level": "info"},
                )
            backup = st.file_uploader(
                "Restore a backup", type=["zip"], key="restore_file", max_upload_size=500
            )
            replace = st.checkbox(
                "Replace my current workspace with this backup", key="restore_confirm"
            )
            if st.button("Restore workspace", disabled=backup is None or not replace):
                try:
                    restored = restore_backup(repo, backup.getvalue())
                    for key in list(st.session_state):
                        if key != "journey_step":
                            del st.session_state[key]
                    st.session_state["workspace"] = restored
                    st.session_state["_saved_workspace"] = restored.model_copy(deep=True)
                    queue_notice("Backup restored.")
                    st.rerun()
                except (ValueError, OSError, SQLiteError) as exc:
                    notify(f"Backup could not be restored: {exc}", level="error")
        st.markdown(
            '<div class="privacy-note"><strong>Private by design</strong>'
            "Runs on your computer. Receipts are processed locally and never sent to an AI service.</div>",
            unsafe_allow_html=True,
        )
        st.caption("Petrol cars · Bengaluru · ACKO insurance")
    st.markdown(
        '<div class="workspace-topbar"><span>Personal travel planner</span>'
        '<span class="context-pill">Bengaluru · Five-year outlook</span></div>',
        unsafe_allow_html=True,
    )
    render_notice()
    SCREENS[screen](repo, workspace)
    _journey_controls(screen)
