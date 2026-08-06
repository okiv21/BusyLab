"""The "business in review" digest (spec Pillar 2).

An email that reads itself in under a minute. That constraint drives every
decision here: three or four lines, the largest movement first, and nothing
that needs a chart to make sense. A digest nobody finishes is a digest nobody
opens twice.

It leads with what changed rather than with a total, because a total is
something the owner already knows. It also deliberately carries one good thing
where there is one — a weekly email that is only ever bad news gets filtered,
and then the bad news stops arriving too.

Delivery is pluggable, and the default writes to the log rather than sending
anything. Same reasoning as the language model: BusyLab has to be fully
buildable and testable without credentials for a third-party service, and an
email provider is a deployment concern rather than a product one.
"""

from __future__ import annotations

import logging
import os
import smtplib
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from email.message import EmailMessage
from html import escape
from typing import Any, Protocol, runtime_checkable

from .alerts import Alert, AlertLevel
from .findings import Finding, Severity

log = logging.getLogger(__name__)

#: How many findings a one-minute read can carry.
MAX_FINDINGS = 3
#: How many alerts before the digest stops listing and starts summarising.
MAX_ALERTS = 4


@dataclass
class Digest:
    """One "business in review" email, ready to render or send."""

    period_label: str
    headline: str
    lines: list[str] = field(default_factory=list)
    alerts: list[Alert] = field(default_factory=list)
    good_news: str | None = None
    findings: list[Finding] = field(default_factory=list)
    generated_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    @property
    def subject(self) -> str:
        return f"Your business in review · {self.period_label}"

    @property
    def is_empty(self) -> bool:
        """True when there is genuinely nothing to say.

        Sending an email that says nothing trains people to ignore the ones
        that say something, so an empty digest is not sent at all.
        """
        return not self.lines and not self.alerts and self.good_news is None

    def to_text(self) -> str:
        parts = [self.subject, "=" * len(self.subject), "", self.headline, ""]
        for line in self.lines:
            parts.append(f"- {line}")
        if self.alerts:
            parts.extend(["", "Worth a look:"])
            for alert in self.alerts[:MAX_ALERTS]:
                parts.append(f"- {alert.title}")
        if self.good_news:
            parts.extend(["", f"Good news: {self.good_news}"])
        parts.extend(
            [
                "",
                "Nothing here is advice. These are the facts as the numbers "
                "have them; the decisions stay yours.",
            ]
        )
        return "\n".join(parts)

    def to_html(self) -> str:
        """Inline styles only, because email clients strip everything else."""
        body = [
            '<div style="font-family:-apple-system,Segoe UI,Helvetica,Arial,'
            'sans-serif;color:#211c15;max-width:560px;margin:0 auto;'
            'padding:24px">',
            '<div style="font-size:12px;letter-spacing:.1em;text-transform:'
            f'uppercase;color:#8a8378">Business in review</div>',
            f'<h1 style="font-size:22px;margin:8px 0 4px">{escape(self.period_label)}</h1>',
            f'<p style="font-size:15px;line-height:1.55;color:#211c15">'
            f"{escape(self.headline)}</p>",
        ]
        if self.lines:
            body.append('<ul style="padding-left:18px;margin:14px 0">')
            for line in self.lines:
                body.append(
                    '<li style="font-size:14px;line-height:1.6;color:#6e675c;'
                    f'margin-bottom:6px">{escape(line)}</li>'
                )
            body.append("</ul>")

        if self.alerts:
            body.append(
                '<div style="font-size:12px;letter-spacing:.08em;text-transform:'
                'uppercase;color:#8a8378;margin-top:18px">Worth a look</div>'
            )
            for alert in self.alerts[:MAX_ALERTS]:
                colour = "#c74722" if alert.level is AlertLevel.HIGH else "#b06a1e"
                body.append(
                    f'<div style="border-left:3px solid {colour};padding:6px 0 6px 12px;'
                    f'margin:10px 0;font-size:14px;line-height:1.5">'
                    f"<strong>{escape(alert.title)}</strong><br>"
                    f'<span style="color:#6e675c">{escape(alert.detail)}</span></div>'
                )

        if self.good_news:
            body.append(
                '<div style="background:#e7f6f0;border-radius:10px;padding:12px 14px;'
                'margin-top:16px;font-size:14px;color:#177e5b">'
                f"{escape(self.good_news)}</div>"
            )

        body.append(
            '<p style="font-size:12px;color:#8a8378;margin-top:22px;'
            'border-top:1px solid #f0ebe3;padding-top:12px">'
            "Nothing here is advice. These are the facts as the numbers have "
            "them; the decisions stay yours.</p>"
        )
        body.append("</div>")
        return "".join(body)

    def to_dict(self) -> dict[str, Any]:
        return {
            "period_label": self.period_label,
            "subject": self.subject,
            "headline": self.headline,
            "lines": self.lines,
            "alerts": [a.to_dict() for a in self.alerts[:MAX_ALERTS]],
            "good_news": self.good_news,
            "generated_at": self.generated_at,
            "is_empty": self.is_empty,
        }


def build_digest(
    findings: list[Finding],
    alerts: list[Alert],
    *,
    period_label: str | None = None,
) -> Digest:
    """Assemble a digest from an analysis that has already run.

    Nothing is computed here. The digest is a selection and an ordering of
    findings the engine produced, which is what keeps it consistent with what
    the app itself shows.
    """
    label = period_label or f"week of {date.today().isoformat()}"

    ranked = [f for f in findings if not f.id.startswith("goal_")] + [
        f for f in findings if f.id.startswith("goal_")
    ]
    notable = [
        f
        for f in ranked
        if f.severity in (Severity.URGENT, Severity.WATCH)
        and f.type.value not in {"ranking", "noise"}
    ]

    if notable:
        headline = notable[0].summary
        lines = [f.summary for f in notable[1:MAX_FINDINGS]]
    elif ranked:
        headline = (
            "Quiet period: nothing moved outside the range this business "
            "normally sits in."
        )
        lines = [
            f.summary
            for f in ranked
            if f.type.value not in {"ranking"}
        ][:2]
    else:
        headline = "No findings this period."
        lines = []

    good = next(
        (
            f.summary
            for f in findings
            if f.severity is Severity.GOOD and f.type.value != "ranking"
        ),
        None,
    )
    if good is None:
        good = next(
            (a.detail for a in alerts if a.level is AlertLevel.GOOD), None
        )

    # An alert that restates a finding already in the body is the same
    # sentence twice. The body wins; the alert list carries what is new.
    shown = {f.id for f in notable[:MAX_FINDINGS]}
    fresh_alerts = [a for a in alerts if a.finding_id not in shown]

    ordered_alerts = sorted(
        fresh_alerts,
        key=lambda a: {
            AlertLevel.HIGH: 0,
            AlertLevel.MEDIUM: 1,
            AlertLevel.GOOD: 2,
            AlertLevel.INFO: 3,
        }.get(a.level, 9),
    )

    return Digest(
        period_label=label,
        headline=headline,
        lines=lines,
        alerts=ordered_alerts,
        good_news=good,
        findings=notable[:MAX_FINDINGS],
    )


# --------------------------------------------------------------------------
# Delivery
# --------------------------------------------------------------------------


@runtime_checkable
class Mailer(Protocol):
    """Anything that can deliver a digest."""

    name: str

    def available(self) -> bool:
        ...

    def send(self, to: str, digest: Digest) -> bool:
        ...


@dataclass
class LogMailer:
    """The default: writes the digest to the log and sends nothing.

    A supported configuration rather than a broken one. The digest can be
    built, rendered, reviewed and tested without any third-party account.
    """

    name: str = "log"

    def available(self) -> bool:
        return True

    def send(self, to: str, digest: Digest) -> bool:
        log.info("digest for %s:\n%s", to, digest.to_text())
        return True


@dataclass
class SmtpMailer:
    """Plain SMTP, which every provider speaks.

    Chosen over a vendor SDK for the same reason the LLM provider is a raw
    POST: no extra dependency, and switching provider is configuration.
    """

    host: str = field(default_factory=lambda: os.environ.get("SMTP_HOST", ""))
    port: int = field(default_factory=lambda: int(os.environ.get("SMTP_PORT", "587")))
    user: str = field(default_factory=lambda: os.environ.get("SMTP_USER", ""))
    password: str = field(default_factory=lambda: os.environ.get("SMTP_PASSWORD", ""))
    sender: str = field(default_factory=lambda: os.environ.get("SMTP_FROM", ""))
    name: str = "smtp"

    def available(self) -> bool:
        return bool(self.host and self.user and self.password)

    def send(self, to: str, digest: Digest) -> bool:
        if not self.available():
            log.warning("SMTP is not configured; digest not sent")
            return False

        message = EmailMessage()
        message["Subject"] = digest.subject
        message["From"] = self.sender or self.user
        message["To"] = to
        message.set_content(digest.to_text())
        message.add_alternative(digest.to_html(), subtype="html")

        try:
            # Port 465 is implicit TLS and must not be given STARTTLS; 587 is
            # the reverse. Providers differ on which they offer, and getting it
            # wrong fails with a handshake error that names neither.
            if self.port == 465:
                with smtplib.SMTP_SSL(self.host, self.port, timeout=20) as server:
                    server.login(self.user, self.password)
                    server.send_message(message)
            else:
                with smtplib.SMTP(self.host, self.port, timeout=20) as server:
                    server.starttls()
                    server.login(self.user, self.password)
                    server.send_message(message)
            return True
        except (smtplib.SMTPException, OSError) as exc:
            log.error("digest delivery failed: %s", exc)
            return False


def mailer_from_env() -> Mailer:
    """Build a mailer from the environment, defaulting to the log."""
    from .narration.provider import load_dotenv

    load_dotenv()
    if os.environ.get("BUSYLAB_MAILER", "auto").lower() in {"log", "none", "off"}:
        return LogMailer()
    smtp = SmtpMailer()
    return smtp if smtp.available() else LogMailer()


def send_digest(digest: Digest, to: str, mailer: Mailer | None = None) -> bool:
    """Deliver a digest, unless there is nothing in it to deliver."""
    if digest.is_empty:
        log.info("digest for %s is empty; not sending", to)
        return False
    return (mailer or mailer_from_env()).send(to, digest)
