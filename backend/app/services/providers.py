import smtplib
from dataclasses import dataclass
from email.message import EmailMessage

from app.core.config import get_settings


@dataclass
class SendResult:
    success: bool
    provider_message_id: str | None = None
    permanent_failure: bool = False
    reason: str | None = None


class EmailProvider:
    def send(self, *, to_email: str, from_email: str, reply_to: str, subject: str, html: str, text: str | None) -> SendResult:
        raise NotImplementedError


class SMTPProvider(EmailProvider):
    def __init__(
        self,
        host: str,
        port: int,
        username: str | None = None,
        password: str | None = None,
        encryption_type: str | None = None,
    ):
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.encryption_type = encryption_type

    def send(self, *, to_email: str, from_email: str, reply_to: str, subject: str, html: str, text: str | None) -> SendResult:
        message = EmailMessage()
        message["From"] = from_email
        message["To"] = to_email
        message["Reply-To"] = reply_to
        message["Subject"] = subject
        message.set_content(text or "Please view this email in an HTML-capable client.")
        message.add_alternative(html, subtype="html")

        try:
            if self.encryption_type == "ssl":
                client = smtplib.SMTP_SSL(self.host, self.port, timeout=20)
            else:
                client = smtplib.SMTP(self.host, self.port, timeout=20)
            with client:
                if self.encryption_type == "starttls":
                    client.starttls()
                if self.username and self.password:
                    client.login(self.username, self.password)
                refused = client.send_message(message)
            if refused:
                return SendResult(False, permanent_failure=True, reason=str(refused))
            return SendResult(True)
        except smtplib.SMTPRecipientsRefused as exc:
            return SendResult(False, permanent_failure=True, reason=str(exc))
        except smtplib.SMTPAuthenticationError:
            return SendResult(False, permanent_failure=True, reason="SMTP credentials invalid")
        except Exception as exc:
            return SendResult(False, permanent_failure=False, reason=str(exc))


class PlaceholderProvider(EmailProvider):
    def __init__(self, name: str):
        self.name = name

    def send(self, *, to_email: str, from_email: str, reply_to: str, subject: str, html: str, text: str | None) -> SendResult:
        return SendResult(False, permanent_failure=True, reason=f"{self.name} adapter is configured placeholder only")


def provider_from_config(provider: str, **kwargs) -> EmailProvider:
    settings = get_settings()
    if provider in {"mailhog", "mailpit", "local"}:
        return SMTPProvider(settings.local_mail_host, settings.local_mail_port)
    if provider in {"smtp", "gmail_smtp"}:
        return SMTPProvider(
            kwargs.get("host") or settings.local_mail_host,
            kwargs.get("port") or settings.local_mail_port,
            kwargs.get("username"),
            kwargs.get("password"),
            kwargs.get("encryption_type"),
        )
    if provider in {"ses", "sendgrid", "mailgun", "brevo"}:
        return PlaceholderProvider(provider)
    return PlaceholderProvider(provider)
