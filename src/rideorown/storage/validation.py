"""Cross-record checks shared by persistence and untrusted backup imports."""

import re

from rideorown.domain.models import Workspace

HASH_RE = re.compile(r"^[0-9a-f]{64}$")
MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


def validate_hash(file_hash: str) -> str:
    if not HASH_RE.fullmatch(file_hash):
        raise ValueError("A receipt has an invalid SHA-256 file hash.")
    return file_hash


def validate_workspace(workspace: Workspace) -> Workspace:
    """Revalidate mutable fields as well as foreign keys; return a clean copy."""
    workspace = Workspace.model_validate_json(workspace.model_dump_json())
    identifiers: set[str] = set()
    for record in [
        *workspace.receipts,
        *workspace.groups,
        *workspace.monthly_entries,
        workspace.scenario,
    ]:
        if not record.id or record.id in identifiers:
            raise ValueError("Workspace record IDs must be nonempty and unique.")
        identifiers.add(record.id)
    groups = {group.id: group for group in workspace.groups}
    selected = workspace.cab_forecast.selected_group_ids
    if selected is not None and (
        len(selected) != len(set(selected)) or any(gid not in groups for gid in selected)
    ):
        raise ValueError("Comparison journey selection must contain unique existing group IDs.")
    for group in workspace.groups:
        if group.parent_id is not None and group.parent_id not in groups:
            raise ValueError("A travel group refers to a missing parent.")
        if any(not MONTH_RE.fullmatch(month) for month in group.monthly_overrides):
            raise ValueError("Monthly travel overrides must use YYYY-MM dates.")
    for group in workspace.groups:
        visited: set[str] = set()
        current = group
        while True:
            if current.id in visited:
                raise ValueError("Travel groups cannot contain a parent cycle.")
            visited.add(current.id)
            if current.parent_id is None:
                break
            current = groups[current.parent_id]
    hashes: set[str] = set()
    ride_ids: set[str] = set()
    for receipt in workspace.receipts:
        validate_hash(receipt.file_hash)
        if receipt.file_hash in hashes:
            raise ValueError("The workspace contains the same PDF more than once.")
        hashes.add(receipt.file_hash)
        if receipt.ride_id:
            if receipt.ride_id in ride_ids:
                raise ValueError("The workspace contains the same ride ID more than once.")
            ride_ids.add(receipt.ride_id)
    for record in [*workspace.receipts, *workspace.monthly_entries]:
        if record.group_id is not None and record.group_id not in groups:
            raise ValueError("A travel record refers to a missing group.")
    months = [coverage.month for coverage in workspace.coverage]
    total_keys = set()
    for entry in workspace.monthly_entries:
        if entry.entry_kind == "total":
            key = (
                entry.month,
                entry.group_id,
                entry.category.strip().casefold() if entry.group_id is None else "",
            )
            if key in total_keys:
                raise ValueError(
                    "Use only one complete total per month and travel group or ungrouped category."
                )
            total_keys.add(key)
    if len(months) != len(set(months)):
        raise ValueError("The same coverage month appears more than once.")
    return workspace
