"""Отправка писем из очереди."""

from django.conf import settings
from django.core.mail import EmailMultiAlternatives, get_connection
from django.utils import timezone

from company.models import CompanySettings
from mailer.models import OutgoingEmail

MAX_ATTEMPTS = 5


def base_url() -> str:
    return getattr(settings, "APP_BASE_URL", "").rstrip("/")


def _brand():
    row = CompanySettings.get_solo()
    return {
        "organization": row.organization_name,
        "colour": row.brand_primary_color,
        "contrast": row.brand_contrast_color or "#ffffff",
    }


def _wrap_html(title: str, lines: list, link_path: str = "") -> str:
    brand = _brand()
    link = f"{base_url()}{link_path}" if base_url() and link_path else ""
    body = "".join(f"<p style='margin:0 0 10px'>{line}</p>" for line in lines)
    button = (
        f"<p style='margin:20px 0 0'><a href='{link}' "
        f"style='background:{brand['colour']};color:{brand['contrast']};"
        f"padding:10px 18px;border-radius:8px;text-decoration:none;"
        f"display:inline-block'>Открыть график отпусков</a></p>"
    ) if link else ""

    return f"""<div style="font-family:system-ui,sans-serif;font-size:14px;
color:#16202b;line-height:1.5;max-width:560px">
  <p style="margin:0 0 16px;color:#5c6b7a;font-size:12px">{brand['organization']}</p>
  <h2 style="margin:0 0 14px;font-size:17px">{title}</h2>
  {body}{button}
  <p style="margin:22px 0 0;color:#5c6b7a;font-size:12px">
    Письмо отправлено системой планирования отпусков.
    Отключить уведомления можно в настройках профиля.
  </p>
</div>"""


def _text(title: str, lines: list, link_path: str = "") -> str:
    link = f"{base_url()}{link_path}" if base_url() and link_path else ""
    parts = [title, ""] + lines
    if link:
        parts += ["", link]
    parts += ["", "Отключить уведомления можно в настройках профиля."]
    return "\n".join(parts)


def _deliver(connection, recipient_email, subject, text_body, html_body):
    message = EmailMultiAlternatives(
        subject=subject,
        body=text_body,
        from_email=getattr(settings, "DEFAULT_FROM_EMAIL", None),
        to=[recipient_email],
        connection=connection,
    )
    message.attach_alternative(html_body, "text/html")
    message.send()


def send_urgent(limit: int = 100) -> dict:
    """Отправка срочных писем — по одному на событие."""
    queue = OutgoingEmail.objects.filter(
        status=OutgoingEmail.Status.QUEUED,
        kind=OutgoingEmail.Kind.URGENT,
        attempts__lt=MAX_ATTEMPTS,
    )[:limit]

    sent, failed = 0, 0
    if not queue:
        return {"sent": 0, "failed": 0}

    connection = get_connection(fail_silently=False)
    for item in queue:
        item.attempts += 1
        try:
            _deliver(
                connection, item.recipient_email, item.subject,
                _text(item.subject, [item.body], item.link_path),
                _wrap_html(item.subject, [item.body], item.link_path),
            )
        except Exception as error:
            # Письмо остаётся в очереди до исчерпания попыток:
            # временная недоступность SMTP не должна терять уведомление.
            item.status = (OutgoingEmail.Status.FAILED
                           if item.attempts >= MAX_ATTEMPTS
                           else OutgoingEmail.Status.QUEUED)
            item.last_error = str(error)[:500]
            item.save(update_fields=["attempts", "status", "last_error"])
            failed += 1
            continue

        item.status = OutgoingEmail.Status.SENT
        item.sent_at = timezone.now()
        item.last_error = ""
        item.save(update_fields=["attempts", "status", "sent_at", "last_error"])
        sent += 1

    return {"sent": sent, "failed": failed}


def send_digest() -> dict:
    """Сводка: все накопленные несрочные письма одним письмом на человека."""
    queue = OutgoingEmail.objects.filter(
        status=OutgoingEmail.Status.QUEUED,
        kind=OutgoingEmail.Kind.DIGEST,
        attempts__lt=MAX_ATTEMPTS,
    ).select_related("recipient")

    grouped = {}
    for item in queue:
        grouped.setdefault(item.recipient_email, []).append(item)

    if not grouped:
        return {"recipients": 0, "messages": 0, "failed": 0}

    connection = get_connection(fail_silently=False)
    recipients, messages, failed = 0, 0, 0

    for email, items in grouped.items():
        subject = (f"График отпусков: {len(items)} "
                   f"{'событие' if len(items) == 1 else 'события'}")
        lines = [f"• {item.body}" for item in items]
        try:
            _deliver(connection, email, subject,
                     _text("Что произошло за день", lines, "/"),
                     _wrap_html("Что произошло за день", lines, "/"))
        except Exception as error:
            for item in items:
                item.attempts += 1
                item.status = (OutgoingEmail.Status.FAILED
                               if item.attempts >= MAX_ATTEMPTS
                               else OutgoingEmail.Status.QUEUED)
                item.last_error = str(error)[:500]
                item.save(update_fields=["attempts", "status", "last_error"])
            failed += len(items)
            continue

        now = timezone.now()
        for item in items:
            item.attempts += 1
            item.status = OutgoingEmail.Status.SENT
            item.sent_at = now
            item.last_error = ""
            item.save(update_fields=["attempts", "status", "sent_at", "last_error"])
        recipients += 1
        messages += len(items)

    return {"recipients": recipients, "messages": messages, "failed": failed}
