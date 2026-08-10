"""Persistent lifecycle audit for one trading cycle."""

from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass
from typing import Any

from backend.core import repository
from backend.core.audit import serialize_payload_snapshot


def _safe_text(value: Any, limit: int = 2000) -> str:
    return str(value).replace("\x00", "")[:limit]


@dataclass
class TradingRunAudit:
    run_id: str
    started_at: int
    started_monotonic: float
    stage: str = "startup"
    finalized: bool = False

    @classmethod
    def start(cls) -> "TradingRunAudit":
        started_at = int(time.time())
        run = cls(
            run_id=str(uuid.uuid4()),
            started_at=started_at,
            started_monotonic=time.monotonic(),
        )
        repository.create_trading_run(
            {
                "run_id": run.run_id,
                "started_at": started_at,
                "status": "STARTED",
                "stage": run.stage,
                "mode": "paper",
                "model": os.getenv("LLM_MODEL"),
                "provider_base_url": os.getenv("LLM_BASE_URL"),
                "prompt_profile": os.getenv("LLM_PROMPT_PROFILE", "current"),
                "llm_called": False,
            }
        )
        return run

    def __enter__(self) -> "TradingRunAudit":
        return self

    def __exit__(self, exc_type, exc, traceback) -> bool:
        if exc is not None and not self.finalized:
            self.fail(exc)
        elif not self.finalized:
            self.abort("Ciclo encerrado sem veredito final.")
        return False

    def mark_stage(self, stage: str) -> None:
        self.stage = _safe_text(stage, 64)
        repository.update_trading_run(self.run_id, {"stage": self.stage})

    def capture_payload(self, payload: dict) -> None:
        repository.update_trading_run(
            self.run_id,
            {"payload_snapshot_json": serialize_payload_snapshot(payload)},
        )

    def capture_llm(self, decision: Any) -> None:
        repository.update_trading_run(
            self.run_id,
            {
                "llm_called": True,
                "llm_action": _safe_text(decision.action, 32),
                "llm_conviction": float(decision.conviction),
                "llm_reasoning": _safe_text(decision.reasoning),
                "llm_decision_brief": _safe_text(decision.decision_brief),
            },
        )

    def capture_risk(self, final_order: dict) -> None:
        repository.update_trading_run(
            self.run_id,
            {
                "risk_action": _safe_text(final_order.get("action"), 32),
                "risk_reason": _safe_text(final_order.get("reason")),
                "executed_size": float(final_order.get("executed_size", 0.0)),
            },
        )

    def complete(self, *, trade_log_id: int, execution_audit: dict) -> None:
        self._finalize(
            "COMPLETED",
            trade_log_id=trade_log_id,
            execution_audit_json=json.dumps(
                execution_audit, ensure_ascii=False, separators=(",", ":"), sort_keys=True
            ),
        )

    def abort(
        self,
        reason: str,
        *,
        trade_log_id: int | None = None,
        payload: dict | None = None,
    ) -> None:
        values: dict[str, Any] = {
            "risk_action": "HOLD",
            "risk_reason": _safe_text(reason),
            "trade_log_id": trade_log_id,
        }
        if payload is not None:
            values["payload_snapshot_json"] = serialize_payload_snapshot(payload)
        self._finalize("ABORTED", **values)

    def fail(self, error: BaseException) -> None:
        self._finalize(
            "FAILED",
            error_type=type(error).__name__,
            error_message=_safe_text(error),
        )

    def _finalize(self, status: str, **values: Any) -> None:
        if self.finalized:
            return
        completed_at = int(time.time())
        values.update(
            {
                "completed_at": completed_at,
                "duration_ms": round((time.monotonic() - self.started_monotonic) * 1000.0, 3),
                "status": status,
                "stage": self.stage,
            }
        )
        repository.update_trading_run(self.run_id, values)
        self.finalized = True
