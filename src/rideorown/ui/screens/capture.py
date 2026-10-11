"""Import and review cab-receipt evidence."""

from datetime import datetime, time
from decimal import Decimal
from sqlite3 import Error as SQLiteError

import pandas as pd
import streamlit as st

from rideorown.domain.models import Receipt
from rideorown.services.application import import_receipts
from rideorown.ui.components import (
    amount_input,
    inr_frame,
    metric_card,
    money,
    notify,
    page_intro,
    queue_notice,
    rupee_value,
    save,
)


def render(repo, workspace):
    page_intro(
        "01 / Cab receipts",
        "Start with six months of cab spending.",
        "Upload receipts, then check dates and distinguish selected prices from final payments.",
    )
    _receipts(repo, workspace)


def _group_label(workspace, group_id):
    return next((g.name for g in workspace.groups if g.id == group_id), "Unassigned")


def _import_receipts(repo, workspace):
    st.subheader("Import Rapido receipts")
    st.caption("Up to 500 PDFs per batch · 5 MB each · 50 MB total.")
    upload_type = st.radio(
        "Choose upload method", ["Select PDFs", "Upload a folder"], horizontal=True
    )
    files = st.file_uploader(
        "PDF receipts",
        type=["pdf"],
        accept_multiple_files="directory" if upload_type == "Upload a folder" else True,
        key=f"pdfs_{upload_type}",
        max_upload_size=5,
    )
    if st.button("Import receipts", type="primary", disabled=not files):
        st.session_state.pop("last_import", None)
        if len(files) > 500 or sum(f.size for f in files) > 50 * 1024 * 1024:
            notify("Split this batch into at most 500 PDFs and 50 MB combined.", level="error")
            st.stop()
        progress = st.progress(0, text="Reading receipts…")

        def on_progress(*args, **kwargs):
            numbers = [v for v in args if isinstance(v, int)]
            if len(numbers) >= 2 and numbers[1]:
                progress.progress(
                    min(numbers[0] / numbers[1], 1.0),
                    text=f"Processed {numbers[0]} of {numbers[1]} PDFs · {args[2]} · {args[3]}"
                    if len(args) >= 4
                    else f"Processed {numbers[0]} of {numbers[1]} PDFs",
                )

        try:
            with st.spinner("Extracting receipts on this computer…"):
                result = import_receipts(
                    repo, workspace, [(f.name, f.getvalue()) for f in files], progress=on_progress
                )
            progress.empty()
            save(repo, workspace)
            st.session_state["last_import"] = result
            _import_notice(result)
        except (ValueError, OSError, SQLiteError) as exc:
            progress.empty()
            notify(f"Receipts could not be imported: {exc}", level="error")
    result = st.session_state.get("last_import")
    if result:
        with st.expander(
            "Last import details", expanded=bool(result["errors"] or result["conflicts"])
        ):
            st.caption(
                f"{result['success']} imported · {result['duplicates']} duplicates skipped · {len(result['conflicts'])} conflicts · {len(result['errors'])} errors"
            )
            if result["errors"]:
                st.dataframe(result["errors"], hide_index=True, width="stretch")
            if result["conflicts"]:
                st.warning(
                    "Conflicting ride IDs were skipped. Review both PDFs before correcting the existing receipt."
                )
                st.dataframe(result["conflicts"], hide_index=True, width="stretch")


def _import_notice(result):
    imported, duplicates = result["success"], result["duplicates"]
    errors, conflicts = len(result["errors"]), len(result["conflicts"])
    if errors or conflicts:
        issues = " and ".join(
            f"{count} {label}{'s' if count != 1 else ''}"
            for count, label in [(errors, "error"), (conflicts, "conflict")]
            if count
        )
        notify(
            f"{imported} receipts imported. Review {issues} in the import details."
            if imported
            else f"No receipts imported. Review {issues} in the import details.",
            level="warning" if imported or not errors else "error",
        )
    elif imported:
        notify(f"{imported} receipts imported. {duplicates} duplicates skipped.")
    else:
        notify(f"No new receipts. {duplicates} duplicates skipped.", level="info")


def _receipts(repo, workspace):
    with st.container(border=True, key="receipt_import"):
        _import_receipts(repo, workspace)

    if workspace.receipts:
        total = sum(
            (receipt.spending for receipt in workspace.receipts if receipt.spending is not None),
            Decimal(0),
        )
        review_count = sum(
            receipt.ride_at is None or receipt.final_paid is None for receipt in workspace.receipts
        )
        count, spending, review = st.columns(3)
        with count:
            metric_card(
                "Receipts in your workspace",
                f"{len(workspace.receipts):,}",
                "Your imported travel history",
            )
        with spending:
            metric_card(
                "Available receipt amounts",
                money(total),
                f"{sum(r.final_paid is not None for r in workspace.receipts)} user-entered final payments; other amounts are provisional selected prices",
            )
        with review:
            metric_card(
                "Receipts to review",
                f"{review_count:,}",
                "Missing a ride date or known final payment",
            )

    st.subheader("Review your evidence")
    st.caption(
        "Selected Price is the initial agreed price, not proof of final payment. Final payment and tips remain unknown until entered. Provisional forecasts use available amounts without inventing a tip."
    )
    if not workspace.receipts:
        st.markdown(
            '<div class="empty-state">'
            '<div class="empty-state-icon" aria-hidden="true">↥</div>'
            "<h3>Your clearer picture starts here</h3>"
            "<p>Add your Rapido receipts above to bring your cab spending into focus.</p>"
            "</div>",
            unsafe_allow_html=True,
        )
        return
    rows = [
        {
            "File": r.filename,
            "Date": r.ride_at.date().isoformat() if r.ride_at else "Missing",
            "Mode": r.mode,
            "Travel group": _group_label(workspace, r.group_id),
            "Selected price (source / legacy correction)": float(r.selected_price)
            if r.selected_price is not None
            else None,
            "User-entered final payment": float(r.final_paid) if r.final_paid is not None else None,
            "Tip paid (unknown if blank)": float(r.tip_paid) if r.tip_paid is not None else None,
            "Evidence": r.spending_status,
            "Original extraction warnings": "; ".join(r.warnings),
        }
        for r in workspace.receipts
    ]
    st.dataframe(
        inr_frame(
            pd.DataFrame(rows),
            [
                "Selected price (source / legacy correction)",
                "User-entered final payment",
                "Tip paid (unknown if blank)",
            ],
        ),
        hide_index=True,
        width="stretch",
    )
    with st.expander("Assign several receipts to a travel group"):
        selected = st.multiselect(
            "Receipts to assign",
            [r.id for r in workspace.receipts],
            format_func=lambda rid: next(r.filename for r in workspace.receipts if r.id == rid),
            key="bulk_receipts",
        )
        groups = [None] + [g.id for g in workspace.groups]
        group = st.selectbox(
            "Travel group",
            groups,
            format_func=lambda gid: _group_label(workspace, gid),
            key="bulk_group",
        )
        if st.button("Assign selected receipts", disabled=not selected):
            for r in workspace.receipts:
                if r.id in selected:
                    r.group_id = group
            save(repo, workspace)
            queue_notice(f"{len(selected)} receipts assigned to {_group_label(workspace, group)}.")
            st.rerun()

    selected_id = st.selectbox(
        "Receipt to correct",
        [r.id for r in workspace.receipts],
        format_func=lambda rid: next(r.filename for r in workspace.receipts if r.id == rid),
    )
    receipt = next(r for r in workspace.receipts if r.id == selected_id)
    with st.expander("Original extraction", expanded=False):
        st.json(receipt.original)
        try:
            pdf = repo.get_pdf(receipt)
        except (ValueError, OSError) as exc:
            st.warning(
                f"The original PDF could not be read: {exc}. Extracted evidence and corrections are still available."
            )
            pdf = None
        if pdf:
            st.download_button(
                "Download original PDF",
                pdf,
                file_name=receipt.filename,
                mime="application/pdf",
                key=f"pdf_{receipt.id}",
                on_click=queue_notice,
                args=("Download requested: original receipt PDF.",),
                kwargs={"level": "info"},
            )
        else:
            st.caption(
                "Original PDF unavailable. A complete backup requires the original file; keep your source receipts."
            )
        if receipt.corrections:
            st.caption("User corrections, retained separately from original extraction")
            st.json(receipt.corrections)
    _receipt_form(repo, workspace, receipt)


def _receipt_form(repo, workspace, receipt):
    with st.form(f"receipt_{receipt.id}"):
        st.subheader("Check this receipt")
        a, b = st.columns(2)
        with a:
            ride_id = st.text_input("Ride ID", value=receipt.ride_id or "")
            ride_day = st.date_input(
                "Ride date", value=receipt.ride_at.date() if receipt.ride_at else None
            )
            mode = st.text_input("Vehicle mode", value=receipt.mode)
            st.caption(
                f"Documented Selected Price: {money(receipt.selected_price)}. Original extraction remains available above."
            )
            final = amount_input(
                "Final payment you made (₹, total paid)",
                receipt.final_paid,
                help="Leave blank if unknown. Enter the total actually paid, including any tip you paid. Saving this amount records your confirmation; it does not change the extracted Selected Price.",
            )
            tip = amount_input(
                "Tip paid (₹, optional)",
                receipt.tip_paid,
                help="Leave blank if unknown. Enter zero only if you know no tip was paid. This is part of the final payment, never added again.",
            )
        with b:
            group_ids = [None] + [g.id for g in workspace.groups]
            group = st.selectbox(
                "Travel group",
                group_ids,
                index=group_ids.index(receipt.group_id) if receipt.group_id in group_ids else 0,
                format_func=lambda gid: _group_label(workspace, gid),
            )
            tiers = ["Unknown", "Normal", "Premium", "Premium AC", "Priority", "Other"]
            tier = st.selectbox(
                "Service tier (user confirmed)",
                tiers,
                index=tiers.index(receipt.service_tier) if receipt.service_tier in tiers else 0,
            )
        pickup = st.text_input("Pickup", value=receipt.pickup)
        drop = st.text_input("Drop", value=receipt.drop)
        if st.form_submit_button("Save receipt corrections"):
            try:
                values = receipt.model_dump()
                values.update(
                    ride_id=ride_id.strip() or None,
                    mode=mode.strip() or "Unknown",
                    pickup=pickup,
                    drop=drop,
                    final_paid=rupee_value(final, "final paid amount", optional=True),
                    tip_paid=rupee_value(tip, "tip paid", optional=True),
                    group_id=group,
                    service_tier=tier,
                )
                values["ride_at"] = (
                    datetime.combine(
                        ride_day, receipt.ride_at.time() if receipt.ride_at else time()
                    )
                    if ride_day
                    else None
                )
                if values["ride_id"] and any(
                    r.id != receipt.id and r.ride_id == values["ride_id"]
                    for r in workspace.receipts
                ):
                    raise ValueError("This ride ID already belongs to another receipt.")
                corrected = Receipt.model_validate(values)
                changes = dict(receipt.corrections)
                for field in [
                    "ride_id",
                    "ride_at",
                    "mode",
                    "pickup",
                    "drop",
                    "final_paid",
                    "tip_paid",
                    "group_id",
                    "service_tier",
                ]:
                    if getattr(receipt, field) != getattr(corrected, field):
                        changes[field] = corrected.model_dump(mode="json")[field]
                corrected.corrections = changes
                history_changed = (
                    corrected.ride_at != receipt.ride_at or corrected.spending != receipt.spending
                )
                if history_changed:
                    workspace.cab_forecast.coverage_confirmed = False
                workspace.receipts = [
                    corrected if r.id == receipt.id else r for r in workspace.receipts
                ]
                save(repo, workspace)
                queue_notice(
                    "Receipt corrections saved. Review and confirm your history in Cab forecast."
                    if history_changed
                    else "Receipt corrections saved."
                )
                st.rerun()
            except ValueError as exc:
                notify(exc, level="error")
