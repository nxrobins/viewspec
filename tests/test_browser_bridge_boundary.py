"""The limits of a browser-bridge session, checked without a browser.

``experiments/browser-agent-probe/boundary.py`` states what a session may reach, what it may send,
and what a result may hold. Whether the browser keeps to it needs a browser, and
``tests/test_browser_bridge_e2e.py`` checks that when it is switched on. These tests run in the
default suite and pin the rules themselves: which combinations are refused before anything starts,
which addresses count as private, and that a declared secret is taken out of a result in every
shape a page can give it.
"""

from __future__ import annotations

import base64
import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from urllib.parse import quote_plus

import pytest

from tests.test_browser_bridge_e2e import PROBE

SHOP = "https://shop.example"
PASSWORD = "Tr0ub4dor&3-horse"


def _boundary() -> ModuleType:
    """The experiment's ``boundary`` module, loaded by path: the experiment is not a package."""

    name = "browser_bridge_boundary"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, PROBE / "boundary.py")
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module  # Before it runs: its dataclasses look their module up by name.
        spec.loader.exec_module(module)
    return sys.modules[name]


def _build(**limits: object) -> object:
    return _boundary().Boundary.build(**limits)


# ---- what is refused before anything starts ----------------------------------------------------------


@pytest.mark.parametrize(
    ("limits", "reason"),
    [
        ({"allow_writes": True}, "--allow-writes needs --allow-origin"),
        ({"allow_write": ["POST https://shop.example/cart"]}, "--allow-write needs --allow-origin"),
        ({"secret": ["SHOP_PASSWORD"], "environ": {"SHOP_PASSWORD": PASSWORD}}, "--secret needs --allow-origin"),
        ({"allow_host": ["cdn.example"]}, "--allow-host needs --allow-origin"),
    ],
)
def test_credentials_and_writes_need_a_list_of_origins(limits: dict[str, object], reason: str) -> None:
    # Without a list, the browser may talk to any site. A session that can sign in or send must not.
    with pytest.raises(_boundary().BoundaryError, match=reason):
        _build(**limits)


def test_a_signed_in_session_needs_a_list_of_origins(tmp_path: Path) -> None:
    state = tmp_path / "state.json"
    state.write_text("{}", encoding="utf-8")

    with pytest.raises(_boundary().BoundaryError, match="--storage-state needs --allow-origin"):
        _build(storage_state=state)
    assert _build(allow_origin=[SHOP], storage_state=state).to_bridge()["storage_state"] == str(state.resolve())


@pytest.mark.parametrize(
    ("limits", "reason"),
    [
        ({"allow_origin": ["shop.example"]}, "is not an origin"),
        ({"allow_origin": ["ftp://shop.example"]}, "is not an origin"),
        ({"allow_origin": ["https://shop.example/account"]}, "must be an origin only"),
        ({"allow_origin": ["https://bo:pw@shop.example"]}, "must be an origin only"),
        ({"allow_origin": [SHOP], "allow_write": ["POST https://elsewhere.example/pay"]}, "not on a listed origin"),
        (
            {"allow_origin": [SHOP], "allow_write": ["POST https://shop.example.evil.test/pay"]},
            "not on a listed origin",
        ),
        ({"allow_origin": [SHOP], "allow_write": ["POST"]}, "must be a method and an address"),
        ({"allow_origin": [SHOP], "allow_host": ["https://cdn.example/lib"]}, "must be a host name"),
        ({"allow_origin": [SHOP], "secret": ["shop_password"]}, "in capitals"),
        ({"allow_origin": [SHOP], "secret": ["SHOP_PASSWORD"], "environ": {}}, "no value for it"),
        ({"allow_origin": [SHOP], "secret": ["PIN"], "environ": {"PIN": "1234"}}, "at least 6 characters"),
        ({"allow_origin": [SHOP], "deny_url": [" "]}, "needs an address pattern"),
    ],
)
def test_limits_that_cannot_be_kept_are_refused(limits: dict[str, object], reason: str) -> None:
    with pytest.raises(_boundary().BoundaryError, match=reason):
        _build(**limits)


def test_a_refusal_never_repeats_a_secret() -> None:
    with pytest.raises(_boundary().BoundaryError) as refused:
        _build(secret=["SHOP_PASSWORD"], environ={"SHOP_PASSWORD": PASSWORD})

    assert PASSWORD not in str(refused.value)
    held = _build(allow_origin=[SHOP], secret=["SHOP_PASSWORD"], environ={"SHOP_PASSWORD": PASSWORD})
    assert PASSWORD not in repr(held) and PASSWORD not in str(held.to_bridge()) and PASSWORD not in str(held.describe())


# ---- where a session may go ----------------------------------------------------------------------------


def test_an_open_session_reads_the_public_web_and_nothing_on_this_computer() -> None:
    limits = _build()

    assert not limits.sealed and not limits.sends_writes
    assert limits.refusal("https://en.wikipedia.org/wiki/Alan_Turing") is None
    for private in (
        "http://localhost:8000/",
        "http://app.localhost/",
        "http://127.0.0.1:8000/admin",
        "http://10.0.0.5/",
        "http://192.168.1.1/",
        "http://169.254.169.254/latest/meta-data/",  # A cloud's metadata service.
        "http://100.64.0.1/",
        "http://[::1]:8000/",
        "http://[::ffff:127.0.0.1]/",
        "http://[fd00::1]/",
    ):
        assert limits.refusal(private) is not None and limits.refusal(private)[0] == "BROWSER_ADDRESS_PRIVATE", private
    for other in ("file:///etc/passwd", "ftp://example.com/", "javascript:alert(1)", "https://bo:pw@example.com/"):
        assert limits.refusal(other) is not None and limits.refusal(other)[0] == "BROWSER_ADDRESS_NOT_ALLOWED", other


def test_a_sealed_session_opens_its_origins_only() -> None:
    limits = _build(
        allow_origin=["https://shop.example/", "HTTP://127.0.0.1:8000"],
        allow_read=[SHOP + "/cart*", "http://127.0.0.1:8000/checkout.html"],
        allow_host=["cdn.example", "img.example:8443"],
    )

    assert limits.sealed and limits.origins == (SHOP, "http://127.0.0.1:8000")
    # A listed origin on this computer is allowed: whoever starts the server has said so.
    assert limits.refusal("http://127.0.0.1:8000/checkout.html") is None
    assert limits.refusal("https://shop.example/cart?item=1") is None
    for elsewhere in (
        "http://shop.example/",  # Another scheme is another origin.
        "https://shop.example:8443/",
        "https://shop.example.evil.test/",
        "https://cdn.example/lib.js",  # A host for requests is not a page the agent may open.
        "http://127.0.0.1:8001/",
    ):
        assert limits.refusal(elsewhere) is not None and limits.refusal(elsewhere)[0] == "BROWSER_ORIGIN_NOT_ALLOWED", (
            elsewhere
        )
    # What the gate is given: every host and port the browser may connect to.
    assert limits.endpoints() == ("shop.example:443", "127.0.0.1:8000", "cdn.example:*", "img.example:8443")


def test_a_denied_address_is_refused_by_any_method() -> None:
    limits = _build(
        allow_origin=[SHOP], allow_read=[SHOP + "/*"], deny_url=["*/logout*", "https://shop.example/account/delete?*"]
    )

    assert limits.refusal("https://shop.example/logout") is not None
    assert limits.refusal("https://shop.example/account/delete?confirm=1") is not None
    assert limits.refusal("https://shop.example/account/deleted-items") is None  # "?" is a question mark.
    assert limits.to_bridge()["deny"] == ["*/logout*", "https://shop.example/account/delete?*"]


def test_a_pattern_has_one_special_character() -> None:
    matches = _boundary().glob_matches

    assert matches("https://shop.example/cart/*", "https://shop.example/cart/items/7?qty=2")
    assert not matches("https://shop.example/cart/*", "https://shop.example/cart")
    assert not matches("https://shop.example/cart", "https://shop.example/cart/items")
    assert not matches("https://shop.example/a.b", "https://shop.example/aXb")  # A dot is a dot.
    assert not matches("https://shop.example/pay?x=[1]", "https://shop.example/pay?x=1")


# ---- what a session may send -----------------------------------------------------------------------------


def test_write_rules_name_a_method_and_an_address() -> None:
    limits = _build(
        allow_origin=[SHOP],
        allow_write=[
            "post https://shop.example/cart/*",
            "* https://shop.example/api/draft",
            "WEBSOCKET wss://shop.example/live",
        ],
    )

    assert limits.sends_writes
    assert limits.to_bridge()["writes"] == {
        "all": False,
        "rules": [
            {"method": "POST", "pattern": "https://shop.example/cart/*"},
            {"method": "*", "pattern": "https://shop.example/api/draft"},
            {"method": "WEBSOCKET", "pattern": "wss://shop.example/live"},
        ],
    }
    assert "Writes: only POST https://shop.example/cart/*" in " ".join(limits.describe())


def test_a_session_without_rules_sends_no_write() -> None:
    for limits in (_build(), _build(allow_origin=[SHOP])):
        assert limits.to_bridge()["writes"] == {"all": False, "rules": []}
        assert any(line.startswith("Writes: none.") for line in limits.describe())


# ---- what a result may hold --------------------------------------------------------------------------------


def test_a_declared_secret_is_taken_out_of_a_result_in_every_shape() -> None:
    limits = _build(allow_origin=[SHOP], secret=["SHOP_PASSWORD"], environ={"SHOP_PASSWORD": PASSWORD})
    encoded = base64.b64encode(PASSWORD.encode()).decode()
    result = {
        "view": f"message status 'You typed: {PASSWORD}'\n[4] text 'Search' = '{PASSWORD}'",
        "url": f"https://shop.example/search?q={quote_plus(PASSWORD)}",
        "blocked_writes": [{"form_fields": {"note": PASSWORD, PASSWORD: "as a key"}}],
        "steps": [{"download": {"url": f"https://shop.example/f?t={encoded}"}}, {"verified": True, "count": 3}],
    }

    cleaned = limits.scrub(result)

    assert PASSWORD not in str(cleaned) and quote_plus(PASSWORD) not in str(cleaned) and encoded not in str(cleaned)
    assert (
        cleaned["view"]
        == "message status 'You typed: [secret SHOP_PASSWORD]'\n[4] text 'Search' = '[secret SHOP_PASSWORD]'"
    )
    assert cleaned["url"] == "https://shop.example/search?q=[secret SHOP_PASSWORD]"
    assert cleaned["blocked_writes"] == [
        {"form_fields": {"note": "[secret SHOP_PASSWORD]", "[secret SHOP_PASSWORD]": "as a key"}}
    ]
    assert cleaned["steps"][1] == {"verified": True, "count": 3}  # What is not text is left as it is.
    assert result["view"].count(PASSWORD) == 2, "scrub changed the result it was given"


def test_a_session_without_secrets_returns_a_result_untouched() -> None:
    result = {"view": "H1 Account", "steps": [{"verified": None}]}

    assert _build().scrub(result) == result


def test_the_limits_are_stated_in_plain_sentences() -> None:
    assert _build().describe()[:3] == [
        "Pages: any public http or https page. Pages on this computer or a private network are refused.",
        "Writes: none. Every request that is not GET, HEAD, or OPTIONS is stopped and listed.",
        "Credentials: none. This session is not signed in anywhere.",
    ]
    sealed = _build(
        allow_origin=[SHOP],
        allow_host=["cdn.example"],
        allow_write=["POST https://shop.example/cart/*"],
        deny_url=["*/logout*"],
        secret=["SHOP_PASSWORD"],
        environ={"SHOP_PASSWORD": PASSWORD},
    ).describe()
    assert sealed[:5] == [
        "Pages: only https://shop.example.",
        "Requests: only to https://shop.example, cdn.example (requests only). Nothing else is reached, by a page, "
        "a script, a redirect, a socket, or a new tab.",
        "Writes: only POST https://shop.example/cart/*. Every other one is stopped and listed.",
        "Never requested: */logout*.",
        "Credentials: secret SHOP_PASSWORD. A secret is filled in by its name, into a secret field only, and its "
        "value is taken out of every result.",
    ]


@pytest.mark.parametrize(
    "url",
    [
        "https://@shop.example/",
        "https://user@shop.example/",
        "https://shop.example\\@elsewhere.example/",
        "https://shop.example\t/",
        "http://127.1/",
        "http://0x7f000001/",
        "http://[::1%25lo0]/",
    ],
)
def test_ambiguous_browser_addresses_are_refused(url: str) -> None:
    assert _build().refusal(url)[0] == "BROWSER_ADDRESS_NOT_ALLOWED"


def test_policy_normalizes_leading_zero_ports_consistently() -> None:
    limits = _build(
        allow_origin=["http://127.0.0.1:08801"],
        allow_host=["cdn.example:00080"],
        allow_write=["GET http://127.0.0.1:08801/update"],
        allow_read=["http://127.0.0.1:08801/view"],
    )
    assert limits.origins == ("http://127.0.0.1:8801",)
    assert limits.hosts == ("cdn.example:80",)
    assert limits.refusal("http://127.0.0.1:08801/view") is None
    assert limits.to_bridge()["writes"]["rules"][0] == {"method": "GET", "pattern": "http://127.0.0.1:8801/update"}


def test_sensitive_sessions_require_operator_classified_reads() -> None:
    limits = _build(
        allow_origin=[SHOP], secret=["PASSWORD"], environ={"PASSWORD": PASSWORD}, allow_read=[SHOP + "/view*"]
    )
    assert limits.read_policy == "explicit"
    assert limits.refusal(SHOP + "/view?id=1") is None
    assert limits.refusal(SHOP + "/unknown") is not None
    assert _build(allow_origin=[SHOP], allow_writes=True).read_policy == "explicit"
    assert _build(allow_origin=[SHOP], allow_writes=True).refusal(SHOP + "/unknown") is not None
    assert _build(allow_origin=[SHOP], allow_writes=True, read_policy="permissive").read_policy == "permissive"


def test_classified_get_mutations_need_write_permission_even_in_permissive_mode() -> None:
    limits = _build(allow_origin=[SHOP], mutation_url=[SHOP + "/update*"], allow_read=[SHOP + "/*"])
    assert limits.refusal(SHOP + "/view") is None
    assert limits.refusal(SHOP + "/update?id=1") is not None
    permitted = _build(
        allow_origin=[SHOP],
        mutation_url=[SHOP + "/update*"],
        allow_read=[SHOP + "/view"],
        allow_write=["GET " + SHOP + "/update?id=1"],
    )
    assert permitted.refusal(SHOP + "/update?id=1") is None
    assert permitted.refusal(SHOP + "/update?id=2") is not None


@pytest.mark.parametrize("query", ["_method=DELETE", "%5fmethod=PATCH", "_METHOD="])
def test_method_override_query_is_a_mutation(query: str) -> None:
    limits = _build(allow_origin=[SHOP], allow_read=[SHOP + "/*"])
    assert limits.refusal(SHOP + "/view?" + query) is not None


@pytest.mark.parametrize("pattern", [SHOP + "/a/../blocked", SHOP + "/%2e%2E/blocked", SHOP + "/café"])
def test_policy_patterns_do_not_silently_use_a_different_browser_spelling(pattern: str) -> None:
    with pytest.raises(_boundary().BoundaryError):
        _build(allow_origin=[SHOP], deny_url=[pattern])


def test_trial_reads_are_explicit_even_without_credentials() -> None:
    trial = _build(allow_origin=[SHOP], allow_read=[SHOP + "/catalog"])
    assert trial.refusal(SHOP + "/catalog") is None
    assert trial.refusal(SHOP + "/unknown") is not None


def test_storage_export_refuses_an_existing_path_and_other_origins(tmp_path: Path) -> None:
    output = tmp_path / "trial.json"
    policy = _build(allow_origin=[SHOP], save_storage_state=output)
    with pytest.raises(_boundary().BoundaryError):
        policy.save_state({"cookies": [{"domain": ".shop.example", "value": PASSWORD}]})
    assert not output.exists()
    output.write_text("keep")
    with pytest.raises(_boundary().BoundaryError):
        policy.save_state({"cookies": [], "origins": []})
    assert output.read_text() == "keep"
