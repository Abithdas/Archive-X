"""Canonical Archive X workflow registry.

Operations orchestrate workflows; engines provide capabilities; integrations
provide adapters. This registry prevents modules from inventing overlapping
stage order, ownership, or user-facing workflow identities.
"""
from __future__ import annotations
from dataclasses import dataclass

@dataclass(frozen=True)
class WorkflowDefinition:
    """Static description of one operations workflow."""
    id: str
    label: str
    stages: tuple[str, ...]
    operator_module: str
    notes: str = ""

WORKFLOWS = {
    "telegram_transfer": WorkflowDefinition("telegram_transfer", "Telegram Transfer", ("inventory", "duplicate_check", "cleanup_check", "metadata", "entities", "snapshots", "classify", "deliver", "verify", "record_state"), "Telegram Transfer Manager"),
    "local_upload": WorkflowDefinition("local_upload", "Local Upload", ("validate", "inventory", "metadata", "entities", "snapshots", "classify", "deliver", "verify", "record_state", "cleanup"), "Telegram Transfer Manager", "Compatibility workflow; UI is unified under Telegram Transfer."),
    "telegram_migration": WorkflowDefinition("telegram_migration", "Telegram Migration", ("inventory", "duplicate_check", "cleanup_check", "metadata", "entities", "snapshots", "classify", "deliver", "verify", "record_state"), "Telegram Transfer Manager"),
    "content_automation": WorkflowDefinition("content_automation", "Content Automation", ("discover", "torrent_verify", "inventory", "metadata_match", "performer_data", "channel_data", "snapshots", "db_commit", "content_plan", "telegram_transfer", "verify", "torrent_remove", "source_delete"), "Content Automation", "Torrent acquisition is preparation-first; Metadata & Assets commits reusable state before Telegram Transfer Manager delivery."),
    "metadata_assets": WorkflowDefinition("metadata_assets", "Metadata & Assets", ("inventory", "metadata", "performers", "channel_data", "snapshots"), "Metadata & Assets Manager"),
    "backup_restore": WorkflowDefinition("backup_restore", "Backup & Restore", ("snapshot", "backup", "verify", "cleanup"), "Backup & Restore"),
}

def workflow_snapshot() -> dict:
    return {k:{"label":v.label,"stages":list(v.stages),"operator_module":v.operator_module,"notes":v.notes} for k,v in WORKFLOWS.items()}
