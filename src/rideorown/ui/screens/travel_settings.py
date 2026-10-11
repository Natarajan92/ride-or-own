"""Create and manage recurring travel plans independently of forecasting."""

import calendar
import re
from decimal import Decimal

import pandas as pd
import streamlit as st

from rideorown.domain.models import TravelGroup
from rideorown.ui.components import (
    amount_input,
    decimal_value,
    money,
    notify,
    page_intro,
    queue_notice,
    rupee_value,
    save,
)


def render(repo, workspace):
    page_intro(
        "02 / Advanced travel settings",
        "Plan the journeys that matter to you.",
        "Set schedules and costs for recurring journeys. Skip this step to forecast from receipts only.",
    )
    _groups(repo, workspace)


def _group_label(workspace, group_id):
    return next((g.name for g in workspace.groups if g.id == group_id), "Unassigned")


def _new_group():
    st.session_state["travel_groups_table"] = {"selection": {"rows": []}}
    st.session_state["travel_group_draft_version"] = (
        st.session_state.get("travel_group_draft_version", 0) + 1
    )
    st.session_state.pop("group_transport_new", None)
    queue_notice("New travel group ready. Add the details and save.", level="info")


def _groups(repo, workspace):
    st.subheader("Your travel groups")
    group_ids = [g.id for g in workspace.groups]
    previous_ids = st.session_state.get("travel_group_row_ids", group_ids)
    rows = st.session_state.get("travel_groups_table", {}).get("selection", {}).get("rows", [])
    selected_id = previous_ids[rows[0]] if rows and 0 <= rows[0] < len(previous_ids) else None
    pending_id = st.session_state.pop("travel_group_pending_selection", None)
    if previous_ids != group_ids or pending_id is not None:
        selected_id = pending_id if pending_id is not None else selected_id
        st.session_state["travel_groups_table"] = {
            "selection": {
                "rows": [group_ids.index(selected_id)] if selected_id in group_ids else []
            }
        }
    st.session_state["travel_group_row_ids"] = group_ids
    group = None
    if workspace.groups:
        st.caption("Select a group to edit.")
        selection = st.dataframe(
            [
                {
                    "Group": g.name,
                    "Category": g.category.title(),
                    "Pattern": f"{g.visits} / {g.frequency}"
                    if not g.parent_id
                    else f"Linked to {_group_label(workspace, g.parent_id)}",
                    "Transport": g.transport_label,
                    "Round-trip km": float(g.round_trip_km)
                    if g.round_trip_km is not None
                    else None,
                    "Fare / visit (including cab tip)": money(g.fare_low)
                    if g.fare_low == g.fare_high
                    else f"{money(g.fare_low)} – {money(g.fare_high)}",
                    "With ownership": g.replacement_mode.replace("_", " ").title(),
                }
                for g in workspace.groups
            ],
            hide_index=True,
            width="stretch",
            key="travel_groups_table",
            on_select="rerun",
            selection_mode="single-row",
        )
        rows = selection.selection.rows
        if rows and 0 <= rows[0] < len(workspace.groups):
            group = workspace.groups[rows[0]]
    st.button("New travel group", icon="➕", on_click=_new_group)
    st.subheader(f"Edit {group.name}" if group else "New travel group")
    _group_form(repo, workspace, group)
    if group:
        _overrides(repo, workspace, group)
        _delete_group(repo, workspace, group)


def _group_form(repo, workspace, group):
    current = group or TravelGroup(name="New travel group")
    transports = ["cab", "bus"]
    transport = st.selectbox(
        "Travel mode",
        transports,
        index=transports.index(current.transport_mode),
        format_func=lambda value: {"cab": "Cab", "bus": "Public transport (bus)"}[value],
        key=f"group_transport_{group.id if group else 'new'}",
        help="Choose how you travel before owning a car. Save the travel group to apply the selection.",
    )
    bus_selected = transport == "bus"
    form_id = group.id if group else f"new_{st.session_state.get('travel_group_draft_version', 0)}"
    with st.form(f"group_{form_id}"):
        name = st.text_input(
            "Group name", value=current.name if group else "", placeholder="e.g. Office commute"
        )
        a, b = st.columns(2)
        with a:
            categories = ["local", "outstation", "other"]
            category = st.selectbox(
                "Driving category",
                categories,
                index=categories.index(current.category),
                format_func=str.title,
                help="Local uses city efficiency; outstation uses highway efficiency.",
            )
            parents = [None] + [
                g.id for g in workspace.groups if g.id != current.id and not g.parent_id
            ]
            parent = st.selectbox(
                "Link visits to another group",
                parents,
                index=parents.index(current.parent_id) if current.parent_id in parents else 0,
                format_func=lambda gid: (
                    "Independent schedule" if gid is None else _group_label(workspace, gid)
                ),
                help="A linked group follows its parent group's frequency and monthly overrides.",
            )
            frequencies = ["weekly", "monthly", "quarterly", "annual"]
            frequency = st.selectbox(
                "Visit frequency (ignored when linked)",
                frequencies,
                index=frequencies.index(current.frequency),
            )
            visits = amount_input(
                "Visits per period / outings per parent visit",
                current.visits,
                help="Independent: 3 weekly visits means three round trips each week. Linked: 1 means one budget for all outings per parent visit; larger values multiply that budget and distance.",
            )
            first_month = st.selectbox(
                "First visit month for quarterly / annual travel",
                list(range(1, 13)),
                index=current.first_month - 1,
                format_func=lambda m: calendar.month_name[m],
            )
        with b:
            km = amount_input(
                "Distance per complete visit / round trip (km)",
                current.round_trip_km,
                help="Leave blank if unknown. Linked-group distance is additional to the parent journey's distance.",
            )
            if bus_selected:
                bus_fare = amount_input(
                    "Bus fare per day / complete visit (₹)",
                    current.bus_cost_per_visit,
                    help="₹200 is the total daily fare for both directions, charged once per office day / visit.",
                )
                st.caption(
                    "One visit includes both directions. Monthly attendance changes the number of visits."
                )
            else:
                low = amount_input("Cab fare per complete visit — lower (₹)", current.cab_cost_low)
                high = amount_input(
                    "Cab fare per complete visit — upper (₹)", current.cab_cost_high
                )
                tip = amount_input("Separately paid tip per visit (₹)", current.tip_per_visit)
            modes = ["self_drive", "driver", "retain", "mixed"]
            mode = st.selectbox(
                "What happens if you own a car?",
                modes,
                index=modes.index(current.replacement_mode),
                format_func=lambda value: {
                    "self_drive": "Drive my car",
                    "driver": "Use my car with a hired driver",
                    "retain": "Keep taking the bus" if bus_selected else "Keep taking cabs",
                    "mixed": "Replace some visits; retain the bus for the rest"
                    if bus_selected
                    else "Replace some visits; retain cabs for the rest",
                }[value],
            )
            share = st.slider(
                "Visits replaced by your car (%) — mixed mode",
                0,
                100,
                int(current.replacement_share * 100),
                step=5,
            )
        a, b, c = st.columns(3)
        with a:
            driver = amount_input(
                "Driver budget / replaced visit (₹)",
                current.driver_cost_per_visit,
                help="Applied for hired-driver mode.",
            )
        with b:
            tolls = amount_input("Own-car tolls / replaced visit (₹)", current.tolls_per_visit)
        with c:
            parking = amount_input(
                "Own-car parking / replaced visit (₹)", current.parking_per_visit
            )
        with st.expander("Assumptions and notes"):
            notes = st.text_area("Assumptions and notes", value=current.notes)
            st.caption(
                "Fares are estimates. Check driver, toll and parking budgets; 0 means no cost."
            )
        if st.form_submit_button("Save travel group", type="primary"):
            try:
                if not name.strip():
                    raise ValueError("Enter a travel group name.")
                if len(name.strip()) > 100:
                    raise ValueError("Keep the travel group name to 100 characters or fewer.")
                if parent and any(g.parent_id == current.id for g in workspace.groups):
                    raise ValueError(
                        "A group with linked children must remain independent. Unlink its children first."
                    )
                values = current.model_dump()
                if bus_selected:
                    values["bus_cost_per_visit"] = rupee_value(bus_fare, "daily bus fare")
                else:
                    values.update(
                        cab_cost_low=rupee_value(low, "lower cab fare"),
                        cab_cost_high=rupee_value(high, "upper cab fare"),
                        tip_per_visit=rupee_value(tip, "tip"),
                    )
                values.update(
                    name=name.strip(),
                    category=category,
                    parent_id=parent,
                    frequency=frequency,
                    visits=decimal_value(visits, "visits"),
                    first_month=first_month,
                    round_trip_km=decimal_value(km, "distance", optional=True),
                    transport_mode=transport,
                    replacement_mode=mode,
                    replacement_share=Decimal(share) / 100,
                    driver_cost_per_visit=rupee_value(driver, "driver budget"),
                    tolls_per_visit=rupee_value(tolls, "tolls"),
                    parking_per_visit=rupee_value(parking, "parking"),
                    notes=notes,
                )
                updated = TravelGroup.model_validate(values)
                if group:
                    workspace.groups = [
                        updated if g.id == group.id else g for g in workspace.groups
                    ]
                else:
                    workspace.groups.append(updated)
                save(repo, workspace)
                st.session_state["travel_group_pending_selection"] = updated.id
                queue_notice(f'Travel group "{updated.name}" saved.')
                st.rerun()
            except ValueError as exc:
                notify(exc, level="error")


def _overrides(repo, workspace, group):
    with st.expander("Adjust attendance / visits for individual months"):
        if group.parent_id:
            st.info(
                f"This group's visits follow {_group_label(workspace, group.parent_id)}. Edit attendance on the parent group."
            )
            return
        st.caption("Enter total visits for each month. Use 0 for leave or a skipped visit.")
        rows = [
            {"Month (YYYY-MM)": month, "Visits": float(visits)}
            for month, visits in sorted(group.monthly_overrides.items())
        ]
        edited = st.data_editor(
            pd.DataFrame(rows, columns=["Month (YYYY-MM)", "Visits"]),
            num_rows="dynamic",
            hide_index=True,
            key=f"overrides_{group.id}",
            column_config={"Visits": st.column_config.NumberColumn(min_value=0, step=1)},
            width="stretch",
        )
        if st.button("Save monthly overrides", key=f"save_override_{group.id}"):
            try:
                values = {}
                for row in edited.to_dict("records"):
                    month = str(row["Month (YYYY-MM)"]).strip()
                    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month):
                        raise ValueError("Every override needs a month in YYYY-MM format.")
                    if month in values:
                        raise ValueError(f"{month} has more than one override.")
                    values[month] = decimal_value(row["Visits"], "visits")
                group.monthly_overrides = values
                save(repo, workspace)
                queue_notice(
                    "Monthly attendance saved."
                    if values
                    else "Monthly attendance overrides cleared."
                )
                st.rerun()
            except ValueError as exc:
                notify(exc, level="error")


def _delete_group(repo, workspace, group):
    with st.expander("Remove this travel group"):
        children = [g for g in workspace.groups if g.parent_id == group.id]
        if children:
            st.warning(
                "Unlink or remove these child groups before removing their parent: "
                + ", ".join(g.name for g in children)
            )
            return
        count = sum(r.group_id == group.id for r in workspace.receipts) + sum(
            e.group_id == group.id for e in workspace.monthly_entries
        )
        st.caption(f"{count} historical records will be kept and reassigned.")
        choices = [None] + [g.id for g in workspace.groups if g.id != group.id]
        replacement = st.selectbox(
            "Reassign existing records to",
            choices,
            format_func=lambda gid: _group_label(workspace, gid),
            key=f"reassign_{group.id}",
        )
        confirmed = st.checkbox(
            f"Remove {group.name} and its future pattern", key=f"delete_confirm_{group.id}"
        )
        if st.button("Remove travel group", disabled=not confirmed, key=f"delete_{group.id}"):
            workspace.groups = [g for g in workspace.groups if g.id != group.id]
            if workspace.cab_forecast.selected_group_ids is not None:
                workspace.cab_forecast.selected_group_ids = [
                    gid for gid in workspace.cab_forecast.selected_group_ids if gid != group.id
                ]
            for record in workspace.receipts + workspace.monthly_entries:
                if record.group_id == group.id:
                    record.group_id = replacement
            save(repo, workspace)
            queue_notice(f'Travel group "{group.name}" removed.')
            st.rerun()
