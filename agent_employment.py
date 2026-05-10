# { "Depends": "py-genlayer:test" }
from genlayer import *
import json
import typing
from dataclasses import dataclass

# ─────────────────────────────────────────────────────────────────────────────
#  AgentEmployment — AI Agent Employment Contract
#  The first on-chain employment layer for autonomous AI agents.
#
#  Flow:
#   1. Employer deploys contract with a task spec, token budget, and deadline
#   2. Agent accepts the job (registers its wallet + proof-of-capability URL)
#   3. Agent submits deliverable (a URL or text output)
#   4. Multi-LLM validators evaluate the deliverable vs. the spec → verdict
#   5. If PASS → tokens released to agent. If FAIL → employer refunded.
#   6. Either party can open a dispute → larger validator jury invoked
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Deliverable:
    content: str          # URL or text output from agent
    submitted_at: int     # block timestamp
    notes: str            # optional agent notes

@dataclass
class EvaluationResult:
    passed: bool
    score: int            # 0–100
    reasoning: str
    evaluator_notes: str


class AgentEmployment(gl.Contract):
    # ── State ──────────────────────────────────────────────────────────────
    employer: gl.Address
    agent: gl.Address
    task_spec: str                  # Natural language task description
    success_criteria: str          # What "done" looks like
    token_budget: int              # GEN tokens locked in contract
    deadline_block: int            # Block number deadline
    capability_url: str            # Agent's proof-of-capability page
    deliverable: typing.Optional[Deliverable]
    evaluation: typing.Optional[EvaluationResult]
    status: str                    # open | active | submitted | pass | fail | dispute
    dispute_raised_by: str         # "employer" | "agent" | ""
    dispute_round: int             # escalation round (1=5 validators, 2=11, 3=23)

    def __init__(
        self,
        task_spec: str,
        success_criteria: str,
        deadline_block: int,
    ):
        self.employer = gl.message.sender_address
        self.agent = gl.Address(b"\x00" * 20)
        self.task_spec = task_spec
        self.success_criteria = success_criteria
        self.token_budget = gl.message.value
        self.deadline_block = deadline_block
        self.capability_url = ""
        self.deliverable = None
        self.evaluation = None
        self.status = "open"
        self.dispute_raised_by = ""
        self.dispute_round = 0

    # ── Read methods ────────────────────────────────────────────────────────

    @gl.public.view
    def get_contract_summary(self) -> str:
        return json.dumps({
            "employer": str(self.employer),
            "agent": str(self.agent),
            "task_spec": self.task_spec,
            "success_criteria": self.success_criteria,
            "token_budget": self.token_budget,
            "deadline_block": self.deadline_block,
            "status": self.status,
            "capability_url": self.capability_url,
            "has_deliverable": self.deliverable is not None,
            "evaluation": {
                "passed": self.evaluation.passed,
                "score": self.evaluation.score,
                "reasoning": self.evaluation.reasoning,
            } if self.evaluation else None,
        })

    @gl.public.view
    def get_status(self) -> str:
        return self.status

    @gl.public.view
    def get_evaluation(self) -> str:
        if self.evaluation is None:
            return json.dumps({"error": "No evaluation yet"})
        return json.dumps({
            "passed": self.evaluation.passed,
            "score": self.evaluation.score,
            "reasoning": self.evaluation.reasoning,
            "evaluator_notes": self.evaluation.evaluator_notes,
        })

    # ── Write methods ───────────────────────────────────────────────────────

    @gl.public.write
    def accept_job(self, capability_url: str) -> None:
        """
        Called by the AI agent to accept the employment contract.
        capability_url: a URL that demonstrates the agent's relevant capabilities.
        """
        assert self.status == "open", "Job is not open for acceptance"
        assert gl.message.sender_address != self.employer, "Employer cannot be agent"

        self.agent = gl.message.sender_address
        self.capability_url = capability_url
        self.status = "active"

    @gl.public.write
    def submit_deliverable(self, content: str, notes: str = "") -> None:
        """
        Called by the agent once work is complete.
        content: URL to deliverable OR the deliverable text itself.
        """
        assert self.status == "active", "Contract must be active to submit"
        assert gl.message.sender_address == self.agent, "Only the agent can submit"

        self.deliverable = Deliverable(
            content=content,
            submitted_at=0,  # block.number placeholder
            notes=notes,
        )
        self.status = "submitted"

    @gl.public.write
    def evaluate_deliverable(self) -> None:
        """
        Called by anyone (typically a keeper or the employer) to trigger
        AI evaluation. Multi-LLM validators assess the work independently
        and reach consensus on pass/fail and score.
        """
        assert self.status == "submitted", "No deliverable to evaluate"
        assert self.deliverable is not None

        task = self.task_spec
        criteria = self.success_criteria
        deliverable_content = self.deliverable.content
        capability_url = self.capability_url

        def evaluate() -> str:
            # Fetch deliverable if it's a URL
            web_content = ""
            if deliverable_content.startswith("http"):
                try:
                    web_content = gl.get_webpage(deliverable_content, mode="text")[:3000]
                except Exception:
                    web_content = "[Could not fetch URL]"
            else:
                web_content = deliverable_content[:3000]

            # Fetch agent capability page for context
            agent_profile = ""
            if capability_url.startswith("http"):
                try:
                    agent_profile = gl.get_webpage(capability_url, mode="text")[:1000]
                except Exception:
                    agent_profile = "[Could not fetch capability URL]"

            prompt = f"""
You are an impartial senior evaluator on a decentralized AI employment court.

Your task is to evaluate whether an AI agent has successfully completed a job.

=== JOB SPECIFICATION ===
{task}

=== SUCCESS CRITERIA ===
{criteria}

=== AGENT DELIVERABLE ===
{web_content}

=== AGENT CAPABILITY PROFILE ===
{agent_profile}

Evaluate the deliverable against the job specification and success criteria.
Be fair, objective, and detailed. Consider:
- Does the deliverable address the task?
- Does it meet all success criteria?
- Quality and completeness of the work
- Any obvious gaps or failures

Respond ONLY with this JSON format, nothing else:
{{
  "passed": true or false,
  "score": integer from 0 to 100,
  "reasoning": "2-4 sentence explanation of your verdict",
  "evaluator_notes": "specific observations about what was done well or poorly"
}}

Do not include any other words, backticks, or formatting. Output must be valid JSON only.
"""
            result = gl.exec_prompt(prompt)
            result = result.replace("```json", "").replace("```", "").strip()
            parsed = json.loads(result)
            return json.dumps({
                "passed": bool(parsed["passed"]),
                "score": int(parsed["score"]),
                "reasoning": str(parsed["reasoning"]),
                "evaluator_notes": str(parsed["evaluator_notes"]),
            }, sort_keys=True)

        raw = gl.eq_principle_strict_eq(evaluate)
        result = json.loads(raw)

        self.evaluation = EvaluationResult(
            passed=result["passed"],
            score=result["score"],
            reasoning=result["reasoning"],
            evaluator_notes=result["evaluator_notes"],
        )

        if result["passed"]:
            self.status = "pass"
            # Release payment to agent
            gl.transfer(self.agent, self.token_budget)
        else:
            self.status = "fail"
            # Refund employer
            gl.transfer(self.employer, self.token_budget)

    @gl.public.write
    def raise_dispute(self) -> None:
        """
        Called by employer or agent to escalate after evaluation.
        Triggers a larger validator jury (Optimistic Democracy appeal).
        """
        assert self.status in ("pass", "fail"), "Can only dispute after evaluation"
        sender = gl.message.sender_address
        assert sender == self.employer or sender == self.agent, "Only parties can dispute"

        self.dispute_raised_by = "employer" if sender == self.employer else "agent"
        self.dispute_round += 1
        self.status = "dispute"

    @gl.public.write
    def resolve_dispute(self) -> None:
        """
        Re-evaluates with stricter criteria after a dispute is raised.
        In a real deployment, this would escalate to 11, then 23 validators.
        """
        assert self.status == "dispute", "No active dispute"
        assert self.deliverable is not None

        task = self.task_spec
        criteria = self.success_criteria
        deliverable_content = self.deliverable.content
        dispute_raised_by = self.dispute_raised_by
        prior_reasoning = self.evaluation.reasoning if self.evaluation else "None"

        def re_evaluate() -> str:
            web_content = ""
            if deliverable_content.startswith("http"):
                try:
                    web_content = gl.get_webpage(deliverable_content, mode="text")[:3000]
                except Exception:
                    web_content = "[Could not fetch URL]"
            else:
                web_content = deliverable_content[:3000]

            prompt = f"""
You are a senior appeals judge on a decentralized AI employment tribunal.

A dispute has been raised by the {dispute_raised_by} after the initial evaluation.
Your job is to independently and rigorously re-evaluate the work.

=== JOB SPECIFICATION ===
{task}

=== SUCCESS CRITERIA ===
{criteria}

=== AGENT DELIVERABLE ===
{web_content}

=== PRIOR EVALUATION REASONING (for context only — do not defer to it) ===
{prior_reasoning}

Re-evaluate completely independently. Resolve this dispute with finality.
Be especially rigorous. The party who raised the dispute believes the prior verdict was wrong.

Respond ONLY with this JSON format:
{{
  "passed": true or false,
  "score": integer from 0 to 100,
  "reasoning": "3-5 sentence definitive ruling with specific references to the deliverable",
  "evaluator_notes": "what tipped the final decision"
}}
"""
            result = gl.exec_prompt(prompt)
            result = result.replace("```json", "").replace("```", "").strip()
            parsed = json.loads(result)
            return json.dumps({
                "passed": bool(parsed["passed"]),
                "score": int(parsed["score"]),
                "reasoning": str(parsed["reasoning"]),
                "evaluator_notes": str(parsed["evaluator_notes"]),
            }, sort_keys=True)

        raw = gl.eq_principle_strict_eq(re_evaluate)
        result = json.loads(raw)

        self.evaluation = EvaluationResult(
            passed=result["passed"],
            score=result["score"],
            reasoning=result["reasoning"],
            evaluator_notes=result["evaluator_notes"],
        )

        if result["passed"]:
            self.status = "pass"
            gl.transfer(self.agent, self.token_budget)
        else:
            self.status = "fail"
            gl.transfer(self.employer, self.token_budget)

    @gl.public.write
    def cancel_job(self) -> None:
        """
        Employer can cancel an open or active job (before submission).
        Funds are returned.
        """
        assert gl.message.sender_address == self.employer, "Only employer can cancel"
        assert self.status in ("open", "active"), "Cannot cancel at this stage"
        self.status = "cancelled"
        gl.transfer(self.employer, self.token_budget)
