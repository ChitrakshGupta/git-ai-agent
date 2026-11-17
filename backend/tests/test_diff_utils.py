import pytest

from app.services import diff_utils
from tests.fakes import PATCH


def test_parse_pr_url():
    assert diff_utils.parse_pr_url("https://github.com/pallets/click/pull/3921") == ("pallets/click", 3921)
    assert diff_utils.parse_pr_url("https://github.com/a/b.c/pull/7/files#diff") == ("a/b.c", 7)
    for bad in ["https://github.com/a/b/issues/1", "https://gitlab.com/a/b/pull/1", "not a url"]:
        with pytest.raises(ValueError):
            diff_utils.parse_pr_url(bad)


def test_commentable_lines_are_new_file_lines_in_the_diff():
    # Hunk starts at new line 1: context(1), +2, +3, +4, +5, context(6), context(7)
    assert diff_utils.commentable_lines(PATCH) == {1, 2, 3, 4, 5, 6, 7}
    assert diff_utils.commentable_lines(None) == set()


def test_number_patch_marks_new_lines_and_leaves_removed_unnumbered():
    numbered = diff_utils.number_patch(PATCH).splitlines()
    assert numbered[2].strip().startswith("-    return sum")  # removed line has no number
    assert numbered[4].startswith("   3 +    for i in range")


def test_should_skip_generated_and_lock_files():
    assert diff_utils.should_skip("package-lock.json")
    assert diff_utils.should_skip("web/dist/app.min.js")
    assert diff_utils.should_skip("assets/logo.png")
    assert not diff_utils.should_skip("src/app.py")
