from jack import prompts

TOOL_NAMES = {
    "record_issue",
    "judge_tow",
    "record_locations",
    "record_contact",
    "complete_intake",
}


def test_instructions_carry_the_formatted_price() -> None:
    text = prompts.instructions(15000)
    assert "$150.00" in text


def test_format_price() -> None:
    assert prompts.format_price(15000, "USD") == "$150.00"
    assert prompts.format_price(989, "USD") == "$9.89"
    assert prompts.format_price(15000, "EUR") == "150.00 EUR"


def test_tool_tables_agree_on_names() -> None:
    assert set(prompts.TOOL_DESCRIPTIONS) == TOOL_NAMES
    assert set(prompts.TOOL_PARAMETERS) == TOOL_NAMES
    assert set(prompts.TOOL_ACKS) == TOOL_NAMES


def test_notices_mention_what_the_model_must_do() -> None:
    assert "complete_intake" in prompts.PAYMENT_CONFIRMED_NOTICE
    assert "bad phone" in prompts.link_rejected_notice("bad phone")
    assert "again" in prompts.link_failed_notice("timeout")
    assert "abandoned" in prompts.link_expired_notice(final=True)
    assert "new link" in prompts.link_expired_notice(final=False)
