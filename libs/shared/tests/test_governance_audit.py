"""
- Title:    Tests for the audit hash chain
- Author:   Angel Martinez-Tenor
"""

from __future__ import annotations

import pytest

from ai_circus_shared.governance_audit import GENESIS_HASH, AuditRecord, entry_hash, record_hash, verify_chain


def _chain(length: int) -> list[AuditRecord]:
    records: list[AuditRecord] = []
    prev = GENESIS_HASH
    for index in range(1, length + 1):
        content = {
            "ts": f"2026-10-29T10:00:0{index}.000Z",
            "actor": "admin-api-key",
            "action": "gateway.budget.set",
            "target_type": "budget",
            "target_id": f"org-{index}",
            "org_id": f"org-{index}",
            "before": None,
            "after": {"monthly_cap_usd": 10.0 * index},
        }
        digest = entry_hash(prev, **content)
        records.append(AuditRecord(id=index, prev_hash=prev, hash=digest, **content))
        prev = digest
    return records


def test_empty_and_intact_chains_verify() -> None:
    assert verify_chain([]).ok
    result = verify_chain(_chain(5))
    assert result.ok and result.checked == 5


def test_hash_is_deterministic_and_depends_on_the_previous_hash() -> None:
    a, _ = _chain(2)
    assert record_hash(a) == a.hash
    assert (
        entry_hash(
            "1" * 64,
            ts=a.ts,
            actor=a.actor,
            action=a.action,
            target_type=a.target_type,
            target_id=a.target_id,
            org_id=a.org_id,
            before=a.before,
            after=a.after,
        )
        != a.hash
    )


@pytest.mark.parametrize("field,value", [("after", {"monthly_cap_usd": 1.0}), ("actor", "someone"), ("target_id", "x")])
def test_a_tampered_row_fails_verification_at_that_row(field: str, value: object) -> None:
    records = _chain(4)
    records[1] = records[1].model_copy(update={field: value})
    result = verify_chain(records)
    assert not result.ok
    assert result.first_invalid_id == 2 and result.checked == 1
    assert "hash" in (result.reason or "")


def test_a_deleted_middle_row_breaks_the_link() -> None:
    records = _chain(4)
    del records[1]
    result = verify_chain(records)
    assert not result.ok and result.first_invalid_id == 3


def test_a_rewritten_row_with_a_recomputed_own_hash_still_breaks_the_next_link() -> None:
    records = _chain(3)
    forged = records[0].model_copy(update={"after": {"monthly_cap_usd": 999.0}})
    forged = forged.model_copy(update={"hash": record_hash(forged)})
    records[0] = forged
    result = verify_chain(records)
    assert not result.ok and result.first_invalid_id == 2


def test_reordered_rows_fail() -> None:
    records = _chain(3)
    records[1], records[2] = records[2], records[1]
    assert not verify_chain(records).ok


def test_every_published_governance_schema_matches_its_model() -> None:
    """Regenerate with: cd libs/shared && uv run python ../../scripts/export_governance_schemas.py"""
    import importlib.util
    from pathlib import Path

    script = Path(__file__).resolve().parents[3] / "scripts" / "export_governance_schemas.py"
    spec = importlib.util.spec_from_file_location("export_governance_schemas", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for file_name, model in module.schema_files().items():
        published = (module.SCHEMAS_DIR / file_name).read_text(encoding="utf-8")
        assert published == module.render(model), f"{file_name} is stale — regenerate the schemas"
