"""Intentional, temporary plan review; no dashboard and no model reasoning."""

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QPlainTextEdit, QVBoxLayout

from monster.mission.strategic.catalog import CATALOG


def review_text(plan):
    lines = [
        "SIMULATION ONLY · no desktop input, screenshots or real artifact creation.",
        "Goal: technical design proposal fixture",
        f"Plan digest: {plan.digest()}",
        "Revision: " + plan.revision_id,
        "Stages and dependencies:",
    ]
    for o in plan.proposal.objectives:
        lines.append(
            f"{o.objective_id}: {CATALOG[o.objective_kind][1]} "
            f"(after {', '.join(o.depends_on) or 'start'}; criteria {', '.join(o.criterion_ids)})"
        )
    lines += [
        "Declared gaps: "
        + (", ".join(f"{g.criterion_id}: {g.reason}" for g in plan.proposal.gaps) or "none"),
        "Workspace: " + plan.goal.workspace_ref + " · application: simulation-editor",
        f"Planner: {plan.roles.planner.model_ref} → {plan.roles.planner.destination_ref}",
        f"GUI role: {plan.roles.computer_use.model_ref} → {plan.roles.computer_use.destination_ref}",
        "Verification: synthetic fixture evidence only; no real design document is written.",
        (
            f"Max wall: {plan.schedule.budget.max_wall_seconds}s; "
            f"inputs: {plan.schedule.budget.max_total_input_transactions}; "
            f"model calls: {plan.schedule.budget.max_total_model_calls}; "
            f"strategic calls: {plan.limits.max_calls}; revisions: {plan.limits.max_replans}."
        ),
        "Unsupported verification: "
        + (
            ", ".join(
                c.criterion_id for c in plan.goal.criteria if c.evidence_kind in ("visual", "none")
            )
            or "none; fake or explicit human-attested evidence only"
        ),
        "Sending, posting, credentials, deployments and dynamic native input are disabled.",
        "Approval binds this exact digest. Changed plans require new review. Stop remains available.",
    ]
    return "\n".join(lines)


class StrategicReviewDialog(QDialog):
    simulate = Signal()
    edit_goal = Signal()

    def __init__(self, plan, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Monster · review simulated strategic plan")
        self.resize(620, 500)
        layout = QVBoxLayout(self)
        text = QPlainTextEdit()
        text.setReadOnly(True)
        text.setPlainText(review_text(plan))
        layout.addWidget(text)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        approve = buttons.addButton(
            "Approve plan / Simulate", QDialogButtonBox.ButtonRole.AcceptRole
        )
        edit = buttons.addButton("Edit goal", QDialogButtonBox.ButtonRole.ActionRole)
        approve.clicked.connect(self._approve)
        edit.clicked.connect(self._edit)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _approve(self):
        self.simulate.emit()
        self.accept()

    def _edit(self):
        self.edit_goal.emit()
        self.reject()
