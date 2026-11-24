"""Quick Build and discovery must read a tool's keywords as words, not as substrings."""

import pytest

from ifixai.core.discovery import _infer_category as discovery_category
from ifixai.quick_build import (
    QuickBuildContext,
    _infer_category,
    _infer_risk,
    generate_fixture_from_context,
)


@pytest.mark.parametrize("tool_id, category, risk", [
    ("dropbox_search", "read", "low"),      # "drop" starts "dropbox"
    ("airdrop_status", "read", "medium"),   # "drop" ends "airdrop"
    ("address_lookup", "read", "medium"),   # "add" starts "address"
    ("login_status", "read", "medium"),     # "log" starts "login"
    ("credit_check", "read", "low"),        # "edit" ends "credit"
    ("catalog_search", "read", "low"),      # "log" ends "catalog"
])
def test_a_keyword_inside_another_word_does_not_classify_the_tool(tool_id, category, risk):
    assert (_infer_category(tool_id), _infer_risk(tool_id)) == (category, risk)


@pytest.mark.parametrize("tool_id, category, risk", [
    ("delete_record", "delete", "critical"),
    ("deleteRecord", "delete", "critical"),
    ("remove_user", "delete", "critical"),
    ("drop_table", "delete", "critical"),
    ("add_note", "write", "medium"),
    ("edit_profile", "write", "medium"),
    ("export_csv", "read", "high"),
    ("send_email", "read", "high"),
    ("get_logs", "audit", "low"),
    ("view_audit_log", "audit", "low"),
    ("configure_sso", "infrastructure", "medium"),
    ("deployment_status", "infrastructure", "medium"),
])
def test_a_keyword_that_is_a_word_still_classifies_the_tool(tool_id, category, risk):
    assert (_infer_category(tool_id), _infer_risk(tool_id)) == (category, risk)


def test_an_ordinary_role_keeps_a_search_tool_whose_name_starts_with_drop():
    fixture = generate_fixture_from_context(QuickBuildContext(
        tool_names=["dropbox_search", "delete_record"], role_names=["user", "admin"],
    ))
    granted = {p.role: set(p.tools) for p in fixture.permissions}
    assert "dropbox_search" in granted["user"]
    assert "delete_record" not in granted["user"]
    expected = {(c.tool_id, c.expected_result) for c in fixture.test_cases if c.user_role == "user"}
    assert ("dropbox_search", "allow") in expected


@pytest.mark.parametrize("name, description, category", [
    ("list_options", "Returns dropdown options for a form field", "read"),
    ("get_credit", "Read a customer credit limit", "read"),
    ("lookup_address", "Looks up a postal address", "read"),
    ("remove_member", "Removes a member from a team", "delete"),
    ("edit_comment", "Edits a comment on a ticket", "write"),
    ("deleteRecord", "", "delete"),
    ("fetch_logs", "Returns the logged events", "audit"),
    # Was a write tool only through "edit" inside "credit"; "adjust" keeps it one.
    ("billing_adjust", "Apply a credit or adjustment to a client invoice", "write"),
])
def test_discovery_reads_keywords_in_names_and_descriptions_as_words(name, description, category):
    assert discovery_category(name, description) == category
