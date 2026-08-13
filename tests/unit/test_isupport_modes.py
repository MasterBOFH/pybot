"""Unit tests for ISUPPORT and mode parsing."""

from __future__ import annotations

from pybot.irc.isupport import ISupport
from pybot.irc.modes import parse_mode_string
from pybot.irc.state import StateJournal


def test_casefold_rfc1459() -> None:
    i = ISupport()
    i.parse_tokens(["CASEMAPPING=rfc1459"])
    assert i.casefold("[Nick]") == "{nick}"
    assert i.equal("Foo[bar]", "foo{bar}")


def test_chanmodes_categories() -> None:
    i = ISupport()
    i.parse_tokens(
        [
            "CHANMODES=eIbq,k,flj,CFLMPQScgimnprstz",
            "PREFIX=(ov)@+",
            "WHOX",
        ]
    )
    assert i.whox
    assert i.mode_category("b") == "A"
    assert i.mode_category("k") == "B"
    assert i.mode_category("l") == "C"
    assert i.mode_category("m") == "D"
    assert i.mode_category("o") == "prefix"
    assert i.symbol_for_mode("o") == "@"
    assert i.mode_for_symbol("+") == "v"


def test_parse_modes_consumes_params() -> None:
    i = ISupport()
    i.parse_tokens(["CHANMODES=b,k,l,imn", "PREFIX=(ov)@+"])
    changes = parse_mode_string(i, "+ov-bl+m", ["alice", "bob", "bad!*@*", "key"])
    assert [(c.mode, c.add, c.param) for c in changes] == [
        ("o", True, "alice"),
        ("v", True, "bob"),
        ("b", False, "bad!*@*"),
        ("l", False, None),  # C: param only when setting
        ("m", True, None),
    ]


def test_state_journal_rename_and_modes() -> None:
    i = ISupport()
    st = StateJournal(i)
    st.add_member("#Dev", "Alice", {"o"})
    assert st.get_channel("#dev") is not None
    member = st.get_channel("#Dev").members[i.casefold("Alice")]
    assert "o" in member.prefixes
    st.rename_user("Alice", "Alicia")
    assert st.get_user("alicia") is not None
    assert i.casefold("Alicia") in st.get_channel("#Dev").members


def test_state_journal_tracks_oper_flag() -> None:
    i = ISupport()
    st = StateJournal(i)

    user = st.update_who("Alice", oper=True)
    assert user.oper is True
    assert st.get_user("alice").oper is True

    user = st.update_who("alice", oper=False)
    assert user.oper is False
    assert st.get_user("alice").oper is False
