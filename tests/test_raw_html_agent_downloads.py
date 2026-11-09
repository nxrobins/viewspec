"""Downloads: links that download, and planned download steps."""

from __future__ import annotations

import pytest

from viewspec.raw_html import lift_html
from viewspec.raw_html_actions import AgentActionError, plan_control_action, plan_download


def _control(html: str):
    [control] = lift_html(html, profile="agent").controls
    return control


@pytest.mark.parametrize(
    ("html", "download"),
    [
        ('<a href="/files/invoice.pdf" download="invoice-1042.pdf">Invoice</a>', "invoice-1042.pdf"),
        ('<a href="/files/report.csv" download>Report</a>', ""),
        ('<a href="/files/report.csv">Report</a>', None),
        ('<button download="x.txt">Export</button>', None),  # Only a link downloads by its attribute.
    ],
)
def test_links_show_the_file_they_download(html, download):
    control = _control(html)
    assert control.download == download
    assert control.to_json()["download"] == download


def test_a_download_step_clicks_a_link_or_button():
    link = _control('<a href="/files/invoice.pdf" download data-lift-ref="e4">Invoice</a>')
    assert plan_download(link).to_json() == {"op": "download", "node_id": link.node_id, "ref": "e4", "name": "Invoice"}
    [step] = plan_control_action(_control("<button>Export orders</button>"), "download")
    assert (step.op, step.name, step.value) == ("download", "Export orders", None)


@pytest.mark.parametrize(
    ("html", "code"),
    [
        ('<input aria-label="Name">', "AGENT_ACTION_UNSUPPORTED"),
        ('<input type="checkbox" aria-label="Terms">', "AGENT_ACTION_UNSUPPORTED"),
        ("<button disabled>Export</button>", "AGENT_ACTION_DISABLED"),
    ],
)
def test_controls_that_do_not_download_are_refused(html, code):
    with pytest.raises(AgentActionError) as caught:
        plan_control_action(_control(html), "download")
    assert caught.value.code == code
