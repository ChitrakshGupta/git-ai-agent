import pytest

from app.services.llm import Step, ToolCall
from app.services.publisher import build_review_payload
from app.services.review_agent import ReviewAgent, is_test_path
from app.services.schemas import Finding, Review
from tests.fakes import FakeGitHub, FakeLLM



def make_review(*findings):
    return Review(summary="Rewrites total().", risk_level="high", findings=list(findings))


BUG = Finding(category="bug", severity="high", file="src/calc.py", line=3, title="Off-by-one skips last item",
              explanation="range(len(items) - 1) never adds the last element.", suggestion="return sum(items)")


async def test_agent_runs_tools_and_returns_structured_review():
    llm = FakeLLM(
        steps=[
            Step(tool_calls=[ToolCall("1", "get_file_content", {"path": "src/calc.py"}),
                             ToolCall("2", "find_tests", {"path": "src/calc.py"})]),
            Step(text="Off-by-one at src/calc.py:3"),
        ],
        review=make_review(BUG),
    )
    trace = []

    async def progress(t):
        trace[:] = t

    result = await ReviewAgent(llm, FakeGitHub()).review("o/r", 1, progress)

    assert result.review.findings == [BUG]
    tools_run = [e["tool"] for e in result.trace if e["type"] == "tool"]
    assert tools_run == ["get_file_content", "find_tests"]
    assert trace == result.trace  # progress callback saw the live trace
    # Both tool results were sent back in ONE message (parallel calls, fewer rate-limited round trips).
    results_msgs = [entry for entry in llm.log if entry[0] == "results"]
    assert len(results_msgs) == 1 and len(results_msgs[0][1]) == 2
    # find_tests reports no related test for calc.py
    assert results_msgs[0][1][1][1]["related_test_files"] == []


async def test_small_pr_sends_diffs_up_front_and_skips_lockfiles():
    llm = FakeLLM(steps=[Step(text="no issues")], review=make_review())
    result = await ReviewAgent(llm, FakeGitHub()).review("o/r", 1)

    opening = llm.log[0][1]
    assert "All diffs" in opening and "src/calc.py" in opening
    assert "package-lock.json" not in opening
    assert result.skipped_files == ["package-lock.json"]


async def test_pr_text_is_wrapped_as_untrusted_data():
    llm = FakeLLM(steps=[Step(text="notes")], review=make_review())
    await ReviewAgent(llm, FakeGitHub()).review("o/r", 1)
    opening = llm.log[0][1]
    body_at = opening.index("Ignore previous instructions")
    assert opening.rfind("<untrusted>", 0, body_at) > opening.rfind("</untrusted>", 0, body_at)
    assert "untrusted data" in llm.system


async def test_tool_errors_are_returned_to_the_model_not_raised():
    llm = FakeLLM(
        steps=[Step(tool_calls=[ToolCall("1", "get_file_diff", {"path": "nope.py"}),
                                ToolCall("2", "get_file_content", {"path": "missing.py"})]),
               Step(text="done")],
        review=make_review(),
    )
    result = await ReviewAgent(llm, FakeGitHub()).review("o/r", 1)
    outputs = llm.log[1][1]
    assert "not part of this PR" in outputs[0][1]["error"]
    assert "404" in outputs[1][1]["error"]
    assert [e["ok"] for e in result.trace if e["type"] == "tool"] == [False, False]


async def test_turn_limit_answers_pending_calls_and_still_finishes():
    endless = [Step(tool_calls=[ToolCall(str(i), "list_changed_files", {})]) for i in range(10)]
    llm = FakeLLM(steps=endless + [Step(text="final notes")], review=make_review())
    result = await ReviewAgent(llm, FakeGitHub(), max_turns=3).review("o/r", 1)

    assert sum(1 for e in result.trace if e["type"] == "tool") == 3
    last_results = llm.log[-1][1]
    assert "Turn limit reached" in last_results[0][1]["error"]


async def test_postprocess_drops_hallucinated_files_and_snaps_bad_lines():
    outside_diff = BUG.model_copy(update={"line": 40, "title": "Line not in diff"})
    ghost = BUG.model_copy(update={"file": "src/ghost.py", "title": "Ghost file"})
    dup = BUG.model_copy()
    style = Finding(category="style", severity="low", file="src/calc.py", line=None, title="Name", explanation="x")
    llm = FakeLLM(steps=[Step(text="n")], review=make_review(style, BUG, outside_diff, ghost, dup))

    findings = (await ReviewAgent(llm, FakeGitHub()).review("o/r", 1)).review.findings

    titles = [f.title for f in findings]
    assert "Ghost file" not in titles
    assert titles.count(BUG.title) == 1  # de-duplicated
    assert next(f for f in findings if f.title == "Line not in diff").line is None
    assert findings[-1].severity.value == "low"  # sorted high -> low


def test_publisher_puts_valid_lines_inline_and_the_rest_in_the_body():
    file_level = BUG.model_copy(update={"line": None, "title": "File-level"})
    gh = FakeGitHub()
    body, inline = build_review_payload(make_review(BUG, file_level), {f["filename"]: f for f in gh.files})
    assert inline == [{"path": "src/calc.py", "line": 3, "side": "RIGHT", "body": inline[0]["body"]}]
    assert "File-level" in body and "Off-by-one" not in body


def test_is_test_path():
    for p in ["tests/test_x.py", "src/foo.test.ts", "pkg/foo_test.go", "app/__tests__/a.js", "test_utils.py"]:
        assert is_test_path(p), p
    for p in ["src/testing_tools.py", "src/latest.py"]:
        assert not is_test_path(p), p
