import csv
from calendar import month_abbr
from decimal import Decimal
from functools import wraps
import io
import json
import logging
import math
import mimetypes
import re
import smtplib
import uuid
import zipfile
from datetime import date, timedelta
from itertools import cycle
from pathlib import Path
from xml.etree import ElementTree

from django.conf import settings
from django.core import signing
from django.core.exceptions import ImproperlyConfigured
from django.core.mail import send_mail
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.db.models import F, Q, Sum
from django.http import Http404, HttpResponse, HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.encoding import force_bytes, force_str
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode
from django.urls import reverse
from django.utils.dateparse import parse_date
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from .forms import (
    CustomerForm,
    ProductForm,
    ProfileForm,
    PurchaseForm,
    RegistrationForm,
    SaleForm,
    StockMovementForm,
    SupplierForm,
    TERMS_VERSION,
    WarehouseAuthenticationForm,
)
from .gemini import GeminiError, answer_consultant, suggest_product_fields
from .models import (
    AIConversation,
    AIMessage,
    Category,
    Customer,
    Organization,
    PayPalWebhookEvent,
    Product,
    Purchase,
    Sale,
    StockMovement,
    Supplier,
    UserProfile,
)
from .paypal import PayPalError, api_request, is_configured, verify_webhook
from .plans import PLANS, effective_plan_for_user, has_paid_plan
from .product_csv import MAX_IMPORT_ROWS, parse_product_csv
from .report_exports import export_sales_excel, export_sales_pdf

logger = logging.getLogger(__name__)
User = get_user_model()
MAX_PRODUCT_PHOTO_BYTES = 5 * 1024 * 1024
PRODUCT_PHOTO_TYPES = {"image/jpeg", "image/png", "image/webp", "image/heic", "image/heif", "image/avif"}
MAX_CHAT_ATTACHMENT_BYTES = 14 * 1024 * 1024
MAX_CHAT_ATTACHMENT_TEXT_CHARS = 60_000
CHAT_INLINE_MIME_TYPES = PRODUCT_PHOTO_TYPES | {
    "image/bmp",
    "application/pdf",
    "audio/wav", "audio/mp3", "audio/aiff", "audio/aac", "audio/ogg", "audio/flac",
    "audio/mpeg", "audio/m4a", "audio/l16", "audio/opus", "audio/alaw", "audio/mulaw", "audio/webm",
    "video/mp4", "video/mpeg", "video/quicktime", "video/avi", "video/x-flv",
    "video/mpg", "video/webm", "video/x-ms-wmv", "video/3gpp",
}
CHAT_TEXT_EXTENSIONS = {
    ".txt", ".csv", ".tsv", ".json", ".xml", ".html", ".htm", ".css", ".js", ".mjs",
    ".md", ".markdown", ".rtf", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".log",
    ".py", ".java", ".cs", ".sql", ".sh", ".ps1", ".bat", ".c", ".cpp", ".h", ".go", ".rs",
}


class UnsupportedChatAttachment(ValueError):
    pass


def _check_office_archive(data):
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as error:
        raise UnsupportedChatAttachment("This Office document is damaged or has an unsupported format.") from error
    if len(archive.infolist()) > 2000 or sum(item.file_size for item in archive.infolist()) > 40 * 1024 * 1024:
        archive.close()
        raise UnsupportedChatAttachment("This Office document is too large to read safely.")
    return archive


def _extract_office_text(extension, data):
    chunks = []
    character_count = 0
    if extension == ".xlsx":
        from openpyxl import load_workbook

        try:
            checked_archive = _check_office_archive(data)
            checked_archive.close()
            workbook = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
            for sheet in workbook.worksheets[:50]:
                chunks.append(f"Sheet: {sheet.title}")
                for row_number, row in enumerate(sheet.iter_rows(max_col=80, values_only=True)):
                    if row_number >= 5000:
                        chunks.append("[Sheet truncated after 5,000 rows]")
                        break
                    values = [str(value) if value is not None else "" for value in row]
                    if any(values):
                        line = " | ".join(values)
                        chunks.append(line)
                        character_count += len(line)
                    if character_count >= MAX_CHAT_ATTACHMENT_TEXT_CHARS:
                        break
                if character_count >= MAX_CHAT_ATTACHMENT_TEXT_CHARS:
                    break
            workbook.close()
        except Exception as error:
            raise UnsupportedChatAttachment("Could not read this spreadsheet. Save it as .xlsx or .csv and try again.") from error
    else:
        archive = _check_office_archive(data)
        try:
            if extension == ".docx":
                names = ["word/document.xml"]
                namespace = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
            else:
                names = sorted(
                    (name for name in archive.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", name)),
                    key=lambda name: int(re.search(r"slide(\d+)", name).group(1)),
                )
                namespace = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
            for name in names:
                try:
                    root = ElementTree.fromstring(archive.read(name))
                except KeyError:
                    continue
                for paragraph in root.iter(f"{namespace}p"):
                    line = "".join(node.text or "" for node in paragraph.iter(f"{namespace}t"))
                    if line:
                        chunks.append(line)
                        character_count += len(line)
                    if character_count >= MAX_CHAT_ATTACHMENT_TEXT_CHARS:
                        break
                if character_count >= MAX_CHAT_ATTACHMENT_TEXT_CHARS:
                    break
        except (ElementTree.ParseError, KeyError, OSError) as error:
            raise UnsupportedChatAttachment("Could not read this Office document. Save it as PDF and try again.") from error
        finally:
            archive.close()
    text = "\n".join(chunks).strip()
    if not text:
        raise UnsupportedChatAttachment("No readable text was found in this document.")
    return text[:MAX_CHAT_ATTACHMENT_TEXT_CHARS]


def _prepare_chat_attachment(upload):
    data = upload.read()
    filename = upload.name[:255]
    extension = Path(filename).suffix.lower()
    mime_type = upload.content_type
    if not mime_type or mime_type == "application/octet-stream":
        mime_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    if extension in {".docx", ".xlsx", ".pptx"}:
        return {"name": filename, "text": _extract_office_text(extension, data)}
    if extension in CHAT_TEXT_EXTENSIONS or mime_type.startswith("text/") or mime_type in {
        "application/json", "application/xml", "application/javascript", "application/rtf"
    }:
        text = data.decode("utf-8-sig", errors="replace")
        if "\x00" in text:
            raise UnsupportedChatAttachment("This file is binary. Try PDF or a text-based version of the file.")
        return {"name": filename, "text": text[:MAX_CHAT_ATTACHMENT_TEXT_CHARS]}
    if extension == ".mp3":
        mime_type = "audio/mp3"
    elif extension == ".m4a":
        mime_type = "audio/m4a"
    elif extension == ".mov":
        mime_type = "video/quicktime"
    elif extension == ".wmv":
        mime_type = "video/x-ms-wmv"
    if mime_type not in CHAT_INLINE_MIME_TYPES:
        raise UnsupportedChatAttachment(
            "This file can be selected, but Gemini cannot read this format yet. Try PDF, Office (.docx/.xlsx/.pptx), text, a common image, audio, or video file."
        )
    return {"name": filename, "mime_type": mime_type, "data": data}


class WarehouseLoginView(LoginView):
    form_class = WarehouseAuthenticationForm
    template_name = "warehouse/login.html"

    def form_valid(self, form):
        if form.cleaned_data.get("remember_me"):
            self.request.session.set_expiry(60 * 60 * 24 * 30)
        else:
            self.request.session.set_expiry(0)
        return super().form_valid(form)


def _is_russian(request):
    return request.LANGUAGE_CODE == "ru"


def warehouse_staff_required(view):
    @wraps(view)
    @login_required
    def wrapped(request, *args, **kwargs):
        if not request.user.is_staff:
            message = (
                "У вашей учётной записи нет доступа к складской системе."
                if _is_russian(request)
                else "Your account is not authorized to use the warehouse system."
            )
            return HttpResponseForbidden(message)
        try:
            request.organization = request.user.warehouse_profile.organization
        except UserProfile.DoesNotExist:
            return HttpResponseForbidden("No warehouse workspace is assigned to this account.")
        return view(request, *args, **kwargs)

    return wrapped


def _paginate(request, queryset):
    page_obj = Paginator(queryset, 50).get_page(request.GET.get("page"))
    query = request.GET.copy()
    previous_page_url = None
    next_page_url = None
    if page_obj.has_previous():
        query["page"] = page_obj.previous_page_number()
        previous_page_url = f"?{query.urlencode()}"
    if page_obj.has_next():
        query["page"] = page_obj.next_page_number()
        next_page_url = f"?{query.urlencode()}"
    return page_obj, previous_page_url, next_page_url


def _send_verification_email(request, user):
    token = signing.dumps(
        {"user_id": user.pk, "email": user.email},
        salt="warehouse.email-confirmation",
    )
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    confirmation_url = request.build_absolute_uri(
        reverse("verify_email", args=[uid, token])
    )
    russian = _is_russian(request)
    subject = "Подтвердите адрес электронной почты" if russian else "Confirm your email address"
    message = (
        f"Здравствуйте!\n\nПодтвердите адрес электронной почты по ссылке:\n{confirmation_url}\n\n"
        "Ссылка действует 24 часа."
        if russian
        else f"Hello,\n\nConfirm your email address using this link:\n{confirmation_url}\n\n"
        "This link expires in 24 hours."
    )
    send_mail(subject, message, settings.DEFAULT_FROM_EMAIL, [user.email], fail_silently=False)


def register(request):
    russian = _is_russian(request)
    if request.user.is_authenticated:
        return redirect("dashboard")
    if request.method == "POST":
        form = RegistrationForm(request.POST)
        if form.is_valid():
            try:
                with transaction.atomic():
                    user = form.save(commit=False)
                    user.email = form.cleaned_data["email"]
                    user.first_name = form.cleaned_data["first_name"]
                    user.last_name = form.cleaned_data["last_name"]
                    user.is_active = False
                    user.is_staff = False
                    user.save()
                    organization = Organization.objects.create(
                        name=form.cleaned_data["company_name"].strip(),
                        owner=user,
                    )
                    UserProfile.objects.create(
                        user=user,
                        organization=organization,
                        full_name=" ".join(
                            part
                            for part in (user.first_name, user.last_name)
                            if part
                        ),
                        phone=form.cleaned_data["phone"],
                        email_verified=False,
                        terms_accepted_at=timezone.now(),
                        terms_version=TERMS_VERSION,
                    )
            except IntegrityError:
                logger.exception("Warehouse account creation hit a uniqueness conflict.")
                form.add_error(
                    None,
                    "Не удалось создать аккаунт: логин или электронная почта уже используются. Проверьте данные и попробуйте снова."
                    if russian
                    else "We could not create the account because the username or email is already in use. Check the details and try again.",
                )
                return render(request, "warehouse/register.html", {"form": form})
            try:
                _send_verification_email(request, user)
            except (OSError, smtplib.SMTPException):
                logger.exception("Unable to send warehouse account verification email.")
                messages.error(
                    request,
                    "Аккаунт создан, но письмо пока не отправлено. Проверьте настройки почты или запросите подтверждение повторно."
                    if russian
                    else "Your account was created, but we could not send the email. Check mail settings or request confirmation again.",
                )
                return redirect("resend_verification")
            return redirect("registration_complete")
    else:
        form = RegistrationForm()

    return render(request, "warehouse/register.html", {"form": form})


def registration_complete(request):
    return render(request, "warehouse/registration_complete.html")


def legal_information(request, document):
    russian = _is_russian(request)
    content = {
        "terms": {
            "title": "Пользовательское соглашение" if russian else "Terms of Service",
            "sections": [
                (
                    "Назначение сервиса" if russian else "Service purpose",
                    "Сервис предоставляет инструменты для управления складскими данными вашей компании. Вы отвечаете за точность и законность сведений, которые добавляете."
                    if russian
                    else "The service provides tools for managing your company's warehouse data. You are responsible for the accuracy and lawful use of information you add.",
                ),
                (
                    "Аккаунт и безопасность" if russian else "Account and security",
                    "Используйте достоверный адрес электронной почты, храните пароль в секрете и сообщайте администратору сервиса о подозрении на несанкционированный доступ."
                    if russian
                    else "Use an email address you control, keep your password confidential, and notify the service administrator if you suspect unauthorized access.",
                ),
                (
                    "Тарифы и доступ" if russian else "Plans and access",
                    "Доступные функции зависят от выбранного тарифа. Платные тарифы и автоматическое продление применяются только после настройки и подтверждения платежа через PayPal."
                    if russian
                    else "Available features depend on your plan. Paid plans and recurring billing apply only after PayPal checkout is configured and the payment is confirmed.",
                ),
            ],
        },
        "privacy": {
            "title": "Уведомление о конфиденциальности" if russian else "Privacy Notice",
            "sections": [
                (
                    "Какие данные обрабатываются" if russian else "Information handled",
                    "Данные аккаунта (имя пользователя, email, имя и телефон), название компании и введённые вами складские сведения. Также сервис обрабатывает технические данные, необходимые для входа и безопасности."
                    if russian
                    else "Account details (username, email, name, and phone), company name, and warehouse information you enter. The service also processes technical data needed for sign-in and security.",
                ),
                (
                    "Для чего они нужны" if russian else "How information is used",
                    "Для создания аккаунта, подтверждения email, работы изолированной рабочей области, предоставления функций тарифа и защиты сервиса."
                    if russian
                    else "To create your account, verify your email, operate your isolated workspace, provide plan features, and protect the service.",
                ),
                (
                    "Доступ и платежи" if russian else "Access and payments",
                    "Данные рабочей области доступны её авторизованным участникам и уполномоченным администраторам сервиса. При включении оплаты необходимые сведения передаются PayPal согласно его условиям."
                    if russian
                    else "Workspace data is available to its authorized members and service administrators. If payments are enabled, necessary information is sent to PayPal under its terms.",
                ),
                (
                    "Черновик документа" if russian else "Draft notice",
                    "Перед публикацией сервиса проверьте и адаптируйте этот документ к требованиям вашей юрисдикции и фактическим процессам хранения данных."
                    if russian
                    else "Before publishing the service, review and adapt this notice to your jurisdiction and actual data-retention practices.",
                ),
            ],
        },
    }
    if document not in content:
        raise Http404("Legal document not found.")
    return render(request, "warehouse/legal.html", content[document])


def resend_verification(request):
    russian = _is_russian(request)
    if request.method == "POST":
        email = request.POST.get("email", "").strip()
        user = User.objects.filter(email__iexact=email, is_active=False).first()
        if user:
            try:
                _send_verification_email(request, user)
            except (OSError, smtplib.SMTPException):
                logger.exception("Unable to resend warehouse account verification email.")
                messages.error(
                    request,
                    "Не удалось отправить письмо. Попробуйте позже."
                    if russian
                    else "We could not send the email. Please try again later.",
                )
                return redirect("resend_verification")
        messages.success(
            request,
            "Если такой неподтверждённый аккаунт существует, письмо отправлено."
            if russian
            else "If an unverified account exists for that address, a confirmation email has been sent.",
        )
        return redirect("login")
    return render(request, "warehouse/resend_verification.html")


def verify_email(request, uidb64, token):
    russian = _is_russian(request)
    try:
        user_id = force_str(urlsafe_base64_decode(uidb64))
        user = User.objects.get(pk=user_id, is_active=False)
        payload = signing.loads(
            token,
            salt="warehouse.email-confirmation",
            max_age=24 * 60 * 60,
        )
    except (
        User.DoesNotExist,
        signing.BadSignature,
        signing.SignatureExpired,
        ValueError,
        TypeError,
    ):
        return render(
            request,
            "warehouse/verification_result.html",
            {
                "success": False,
                "message": (
                    "Ссылка подтверждения недействительна или устарела."
                    if russian
                    else "This confirmation link is invalid or has expired."
                ),
            },
            status=400,
        )
    if payload != {"user_id": user.pk, "email": user.email}:
        return render(
            request,
            "warehouse/verification_result.html",
            {
                "success": False,
                "message": (
                    "Ссылка подтверждения недействительна."
                    if russian
                    else "This confirmation link is invalid."
                ),
            },
            status=400,
        )
    profile = user.warehouse_profile
    profile.email_verified = True
    profile.save(update_fields=["email_verified"])
    user.is_active = True
    user.is_staff = True
    user.save(update_fields=["is_active", "is_staff"])
    return render(
        request,
        "warehouse/verification_result.html",
        {
            "success": True,
            "message": (
                "Электронная почта подтверждена. Теперь можно войти."
                if russian
                else "Email verified. You can now sign in."
            ),
        },
    )


@login_required
def profile(request):
    user_profile = get_object_or_404(UserProfile, user=request.user)
    russian = _is_russian(request)
    if request.method == "POST":
        form = ProfileForm(request.POST, instance=user_profile)
        if form.is_valid():
            form.save()
            request.user.first_name = form.cleaned_data["full_name"].strip()
            request.user.save(update_fields=["first_name"])
            messages.success(
                request,
                "Профиль обновлён." if russian else "Profile updated.",
            )
            return redirect("profile")
    else:
        form = ProfileForm(instance=user_profile)
    return render(
        request,
        "warehouse/profile.html",
        {
            "form": form,
            "profile": user_profile,
            "plan_name": PLANS[
                effective_plan_for_user(user_profile.organization, request.user)
            ]["name"][
                "ru" if russian else "en"
            ],
        },
    )


@warehouse_staff_required
def dashboard(request):
    organization = request.organization
    products = Product.objects.filter(
        organization=organization,
        is_active=True,
    ).select_related("category", "supplier")
    total_items = products.aggregate(total=Sum("quantity"))["total"] or 0
    low_stock = products.filter(quantity__lte=F("minimum_stock")).count()
    total_value = (
        products.aggregate(total=Sum(F("quantity") * F("unit_price")))["total"]
        or Decimal("0.00")
    )
    recent_movements = StockMovement.objects.filter(organization=organization).select_related(
        "product", "created_by"
    ).order_by("-created_at")[:5]
    recent_sales = Sale.objects.filter(organization=organization).select_related(
        "product", "customer", "created_by"
    ).order_by("-sold_at")[:5]
    category_totals = list(
        products.values("category__name")
        .annotate(quantity=Sum("quantity"))
        .order_by("-quantity", "category__name")[:6]
    )
    max_category_quantity = max(
        (row["quantity"] or 0 for row in category_totals),
        default=0,
    )
    colors = cycle(("blue", "cyan", "violet", "green", "orange", "pink"))
    inventory_chart = [
        {
            "label": row["category__name"]
            or ("Без категории" if _is_russian(request) else "Uncategorized"),
            "quantity": row["quantity"] or 0,
            "width": int((row["quantity"] or 0) * 100 / max_category_quantity)
            if max_category_quantity
            else 0,
            "color": next(colors),
        }
        for row in category_totals
    ]
    context = {
        "products": products[:5],
        "total_items": total_items,
        "low_stock": low_stock,
        "total_value": total_value,
        "recent_movements": recent_movements,
        "recent_sales": recent_sales,
        "inventory_chart": inventory_chart,
    }
    return render(request, "warehouse/dashboard.html", context)


@warehouse_staff_required
def ai_consultant(request):
    conversations = AIConversation.objects.filter(
        user=request.user,
        organization=request.organization,
    ).order_by("-updated_at")
    conversation = None
    requested_id = request.GET.get("conversation")
    if requested_id:
        conversation = get_object_or_404(conversations, pk=requested_id)
    else:
        conversation = conversations.first()
    chat_messages = (
        list(conversation.messages.order_by("created_at", "pk")[:200])
        if conversation else []
    )
    return render(request, "warehouse/ai_consultant_workspace.html", {
        "ai_configured": bool(settings.GEMINI_API_KEY),
        "conversations": conversations,
        "conversation": conversation,
        "chat_messages": chat_messages,
    })


@warehouse_staff_required
@require_POST
def new_ai_consultant_conversation(request):
    title = "Новый чат" if _is_russian(request) else "New chat"
    conversation = AIConversation.objects.create(
        user=request.user,
        organization=request.organization,
        title=title,
    )
    return redirect(f"{reverse('ai_consultant')}?conversation={conversation.pk}")


@warehouse_staff_required
@require_POST
def delete_ai_consultant_conversation(request, pk):
    conversation = get_object_or_404(
        AIConversation,
        pk=pk,
        user=request.user,
        organization=request.organization,
    )
    conversation.delete()
    return redirect("ai_consultant")


@warehouse_staff_required
@require_POST
def _legacy_ai_consultant_chat(request):
    russian = _is_russian(request)
    try:
        payload = json.loads(request.body.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        message = "Не удалось прочитать запрос." if russian else "Could not read the request."
        return JsonResponse({"error": message}, status=400)
    messages = payload.get("messages") if isinstance(payload, dict) else None
    valid = isinstance(messages, list) and 1 <= len(messages) <= 21 and len(messages) % 2 == 1
    if valid:
        for index, item in enumerate(messages):
            expected_role = "user" if index % 2 == 0 else "model"
            if (
                not isinstance(item, dict)
                or item.get("role") != expected_role
                or not isinstance(item.get("text"), str)
                or not item["text"].strip()
                or len(item["text"]) > 4000
            ):
                valid = False
                break
    if not valid:
        message = "Сообщение не прошло проверку. Напишите вопрос короче и попробуйте снова." if russian else "The message did not pass validation. Shorten it and try again."
        return JsonResponse({"error": message}, status=400)
    if not settings.GEMINI_API_KEY:
        message = "Чат заработает после настройки Gemini API на сервере." if russian else "The chat will work after Gemini API is configured on the server."
        return JsonResponse({"error": message}, status=503)
    try:
        answer = answer_consultant(messages, "ru" if russian else "en")
    except GeminiError as error:
        return JsonResponse({"error": str(error)}, status=502)
    return JsonResponse({"answer": answer})


@warehouse_staff_required
@require_POST
def ai_consultant_chat(request):
    russian = _is_russian(request)
    text = request.POST.get("message", "").strip()
    attachment = request.FILES.get("attachment") or request.FILES.get("photo")
    if len(text) > 4000:
        message = "Сообщение должно быть короче 4000 символов." if russian else "Message must be 4,000 characters or fewer."
        return JsonResponse({"error": message}, status=400)
    if not text and not attachment:
        message = "Напиши вопрос или прикрепи файл." if russian else "Write a question or attach a file."
        return JsonResponse({"error": message}, status=400)
    if attachment and attachment.size > MAX_CHAT_ATTACHMENT_BYTES:
        message = "Файл должен быть не больше 14 МБ." if russian else "File must be 14 MB or smaller."
        return JsonResponse({"error": message}, status=400)
    try:
        api_attachment = _prepare_chat_attachment(attachment) if attachment else None
    except UnsupportedChatAttachment as error:
        message = (
            "Не удалось прочитать этот файл. Попробуй PDF, Word, Excel, PowerPoint, текстовый файл, фото, аудио или видео."
            if russian
            else str(error)
        )
        return JsonResponse({"error": message}, status=400)
    if not settings.GEMINI_API_KEY:
        message = "Чат заработает после настройки Gemini API на сервере." if russian else "The chat will work after Gemini API is configured on the server."
        return JsonResponse({"error": message}, status=503)

    conversation = get_object_or_404(
        AIConversation,
        pk=request.POST.get("conversation_id"),
        user=request.user,
        organization=request.organization,
    )
    previous_messages = list(conversation.messages.order_by("-created_at", "-pk")[:20])
    previous_messages.reverse()
    history = [{"role": item.role, "text": item.content} for item in previous_messages]
    attachment_name = attachment.name[:255] if attachment else ""
    user_text = text or (
        f"ÐŸÑ€Ð¾Ð°Ð½Ð°Ð»Ð¸Ð·Ð¸Ñ€ÑƒÐ¹ Ñ„Ð°Ð¹Ð». ÐžÐ±ÑŠÑÑÐ½Ð¸ ÐµÐ³Ð¾ ÑÐ¾Ð´ÐµÑ€Ð¶Ð°Ð½Ð¸Ðµ: {attachment_name}"
        if russian
        else f"Analyze the attached file and explain its contents: {attachment_name}"
    )
    history.append({"role": "user", "text": user_text})
    try:
        answer = answer_consultant(
            history,
            "ru" if russian else "en",
            attachment=api_attachment,
        )
    except GeminiError as error:
        return JsonResponse({"error": str(error)}, status=502)

    stored_text = user_text
    if attachment_name:
        stored_text += f"\n[{('Файл' if russian else 'File')}: {attachment_name}]"
    with transaction.atomic():
        if not conversation.messages.filter(role="user").exists():
            title_source = " ".join((text or attachment_name).split())
            conversation.title = title_source[:117] + ("…" if len(title_source) > 117 else "")
        user_message = AIMessage.objects.create(
            conversation=conversation,
            role="user",
            content=stored_text,
            attachment_name=attachment_name,
            attachment_mime_type=(api_attachment.get("mime_type") or "text/plain") if attachment else "",
        )
        assistant_message = AIMessage.objects.create(conversation=conversation, role="model", content=answer)
        conversation.save(update_fields=["title", "updated_at"])
    return JsonResponse({
        "answer": answer,
        "attachment_name": attachment_name,
        "conversation_id": conversation.pk,
        "title": conversation.title,
        "user_created_at": user_message.created_at.isoformat(),
        "answer_created_at": assistant_message.created_at.isoformat(),
    })


@warehouse_staff_required
def inventory_advisor(request):
    organization = request.organization
    plan_key = effective_plan_for_user(organization, request.user)
    if plan_key != "super":
        return render(
            request,
            "warehouse/inventory_advisor.html",
            {"can_access": False},
        )

    products = list(
        Product.objects.filter(organization=organization, is_active=True)
        .select_related("category")
        .order_by("quantity", "name")
    )
    sold_units_by_product = dict(
        Sale.objects.filter(
            organization=organization,
            sold_at__gte=timezone.now() - timedelta(days=30),
        )
        .values("product_id")
        .annotate(units=Sum("quantity"))
        .values_list("product_id", "units")
    )
    suggestions = []
    for product in products:
        sold_units = sold_units_by_product.get(product.pk, 0)
        average_daily_sales = sold_units / 30
        recommended_stock = max(
            product.minimum_stock,
            math.ceil(average_daily_sales * 14),
        )
        reorder_quantity = max(0, recommended_stock - product.quantity)
        if reorder_quantity:
            suggestions.append(
                {
                    "product": product,
                    "sold_units": sold_units,
                    "average_daily_sales": round(average_daily_sales, 1),
                    "recommended_stock": recommended_stock,
                    "reorder_quantity": reorder_quantity,
                    "urgency": "critical" if product.quantity == 0 else "warning",
                }
            )
    return render(
        request,
        "warehouse/inventory_advisor.html",
        {
            "can_access": True,
            "suggestions": suggestions[:50],
            "suggestion_count": len(suggestions),
            "products_analyzed": len(products),
            "window_days": 30,
            "lead_days": 14,
        },
    )


@warehouse_staff_required
def product_list(request):
    organization = request.organization
    products = Product.objects.filter(
        organization=organization,
        is_active=True,
    ).select_related("category", "supplier")
    query = request.GET.get("q", "").strip()
    status = request.GET.get("status", "")
    category_id = request.GET.get("category", "")

    if query:
        products = products.filter(name__icontains=query) | products.filter(sku__icontains=query)
    if status == "low":
        products = products.filter(quantity__lte=F("minimum_stock"))
    elif status == "in_stock":
        products = products.filter(quantity__gt=F("minimum_stock"))
    if category_id.isdigit():
        products = products.filter(category_id=int(category_id))

    products, previous_page_url, next_page_url = _paginate(
        request, products.order_by("name")
    )
    return render(
        request,
        "warehouse/products.html",
        {
            "products": products,
            "page_obj": products,
            "previous_page_url": previous_page_url,
            "next_page_url": next_page_url,
            "categories": Category.objects.filter(organization=organization).order_by("name"),
            "query": query,
            "status": status,
            "category_id": category_id,
            "can_export_products": effective_plan_for_user(
                organization,
                request.user,
            )
            != "free",
            "can_import_products": effective_plan_for_user(
                organization,
                request.user,
            )
            != "free",
        },
    )


@warehouse_staff_required
def product_import(request):
    organization = request.organization
    russian = _is_russian(request)
    can_import = effective_plan_for_user(organization, request.user) != "free"
    errors = []
    preview = []
    preview_token = ""

    if request.method == "POST":
        if not can_import:
            messages.info(
                request,
                "Импорт товаров по CSV доступен в тарифах Pro и Super."
                if russian
                else "CSV product import is available on Pro and Super plans.",
            )
            return redirect("plan_detail", plan_key="pro")

        action = request.POST.get("action", "")
        if action == "import":
            preview_token = request.POST.get("preview_token", "")
            session_key = f"product-import:{preview_token}"
            saved_preview = request.session.get(session_key)
            if (
                not preview_token
                or not isinstance(saved_preview, dict)
                or saved_preview.get("user_id") != request.user.pk
                or saved_preview.get("organization_id") != organization.pk
                or timezone.now().timestamp() - saved_preview.get("created_at", 0) > 900
            ):
                errors.append(
                    "Предпросмотр импорта истёк. Выберите CSV-файл ещё раз."
                    if russian
                    else "The import preview expired. Select the CSV file again."
                )
            else:
                imported_rows = saved_preview["products"]
                try:
                    with transaction.atomic():
                        locked_organization = Organization.objects.select_for_update().get(
                            pk=organization.pk
                        )
                        plan_key = effective_plan_for_user(
                            locked_organization,
                            request.user,
                        )
                        product_limit = PLANS[plan_key]["product_limit"]
                        active_count = Product.objects.filter(
                            organization=locked_organization,
                            is_active=True,
                        ).count()
                        if active_count + len(imported_rows) > product_limit:
                            errors.append(
                                "Импорт превысит лимит товаров вашего тарифа."
                                if russian
                                else "Importing these rows would exceed your plan's product limit."
                            )
                        else:
                            category_ids = {
                                row["category_id"]
                                for row in imported_rows
                                if row["category_id"] is not None
                            }
                            imported_skus = [row["sku"] for row in imported_rows]
                            if Category.objects.filter(
                                organization=locked_organization,
                                pk__in=category_ids,
                            ).count() != len(category_ids):
                                errors.append(
                                    "Категория из предпросмотра больше недоступна. Загрузите файл заново."
                                    if russian
                                    else "A category in the preview is no longer available. Upload the file again."
                                )
                            elif Product.objects.filter(
                                organization=locked_organization,
                                sku__in=imported_skus,
                            ).exists():
                                errors.append(
                                    "Некоторые артикулы уже появились в рабочей области. Загрузите файл заново."
                                    if russian
                                    else "Some SKUs now exist in this workspace. Upload the file again."
                                )
                            else:
                                for row in imported_rows:
                                    product = Product.objects.create(
                                        organization=locked_organization,
                                        category_id=row["category_id"],
                                        name=row["name"],
                                        sku=row["sku"],
                                        quantity=row["quantity"],
                                        unit_price=Decimal(row["unit_price"]),
                                        minimum_stock=row["minimum_stock"],
                                    )
                                    if product.quantity:
                                        StockMovement.objects.create(
                                            organization=locked_organization,
                                            product=product,
                                            movement_type="adjust",
                                            quantity=product.quantity,
                                            notes=(
                                                "Начальный остаток при импорте CSV"
                                                if russian
                                                else "Initial stock imported from CSV"
                                            ),
                                            created_by=request.user,
                                        )
                                del request.session[session_key]
                                messages.success(
                                    request,
                                    f"Импортировано товаров: {len(imported_rows)}."
                                    if russian
                                    else f"Imported {len(imported_rows)} products.",
                                )
                                return redirect("products")
                except IntegrityError:
                    logger.exception(
                        "Product CSV import conflicted with existing workspace data."
                    )
                    errors.append(
                        "Не удалось сохранить импорт из-за конфликта данных. Проверьте артикулы и повторите загрузку."
                        if russian
                        else "The import conflicted with workspace data. Check the SKUs and upload again."
                    )
        elif action == "preview":
            upload = request.FILES.get("csv_file")
            if not upload:
                errors.append(
                    "Выберите CSV-файл для импорта."
                    if russian
                    else "Choose a CSV file to import."
                )
            else:
                preview, errors = parse_product_csv(
                    upload,
                    organization,
                    language=request.LANGUAGE_CODE,
                )
                if len(preview) > MAX_IMPORT_ROWS:
                    preview = []
                    errors.append(
                        f"Файл не может содержать больше {MAX_IMPORT_ROWS} товаров."
                        if russian
                        else f"The file cannot contain more than {MAX_IMPORT_ROWS} products."
                    )
                active_count = organization.products.filter(is_active=True).count()
                product_limit = PLANS[
                    effective_plan_for_user(organization, request.user)
                ]["product_limit"]
                if preview and active_count + len(preview) > product_limit:
                    preview = []
                    errors.append(
                        "Импорт превысит лимит товаров вашего тарифа."
                        if russian
                        else "Importing these rows would exceed your plan's product limit."
                    )
                if preview and not errors:
                    preview_token = uuid.uuid4().hex
                    request.session[f"product-import:{preview_token}"] = {
                        "user_id": request.user.pk,
                        "organization_id": organization.pk,
                        "created_at": timezone.now().timestamp(),
                        "products": preview,
                    }
        else:
            errors.append(
                "Недопустимое действие импорта."
                if russian
                else "Invalid import action."
            )

    return render(
        request,
        "warehouse/product_import.html",
        {
            "can_import": can_import,
            "errors": errors,
            "preview": preview,
            "preview_token": preview_token,
            "max_rows": MAX_IMPORT_ROWS,
            "max_file_mb": 5,
        },
    )


@warehouse_staff_required
def product_import_template(request):
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = 'attachment; filename="warehouse-products-import-template.csv"'
    response.write("\ufeff")
    writer = csv.writer(response)
    if _is_russian(request):
        writer.writerow(
            ["Название", "Артикул", "Категория", "Количество", "Цена", "Минимум"]
        )
        writer.writerow(["Кофе", "COFFEE-001", "", 25, "9.90", 5])
    else:
        writer.writerow(
            ["Name", "SKU", "Category", "Quantity", "Unit price", "Minimum stock"]
        )
        writer.writerow(["Coffee", "COFFEE-001", "", 25, "9.90", 5])
    return response


@warehouse_staff_required
@require_POST
def analyze_product_photo(request):
    russian = _is_russian(request)
    if not settings.GEMINI_API_KEY:
        message = "Анализ фото пока не настроен на сервере." if russian else "Gemini photo analysis is not configured on this server."
        return JsonResponse({"error": message}, status=503)
    photo = request.FILES.get("photo")
    if not photo:
        message = "Сначала сделайте фото или выберите файл." if russian else "Choose or take a product photo first."
        return JsonResponse({"error": message}, status=400)
    if photo.size > MAX_PRODUCT_PHOTO_BYTES:
        message = "Размер фото не должен превышать 5 МБ." if russian else "Photo must be 5 MB or smaller."
        return JsonResponse({"error": message}, status=400)
    if photo.content_type not in PRODUCT_PHOTO_TYPES:
        message = "Выберите изображение JPEG, PNG, WebP или HEIC." if russian else "Use a JPEG, PNG, WebP, or HEIC image."
        return JsonResponse({"error": message}, status=400)
    try:
        suggestions = suggest_product_fields(
            photo.read(), photo.content_type, "ru" if _is_russian(request) else "en"
        )
    except GeminiError as error:
        if "(HTTP 429)" in str(error):
            message = (
                "Лимит запросов Gemini сейчас исчерпан. Фото осталось на странице: заполни поля вручную. Чтобы снова включить распознавание, дождись обновления квоты или проверь лимиты проекта в Google AI Studio."
                if russian
                else "Gemini's request limit is exhausted. Your photo is still here; fill in the fields manually. To use recognition again, wait for the quota to reset or check the project limits in Google AI Studio."
            )
            return JsonResponse({"error": message, "code": "quota_exhausted"}, status=429)
        return JsonResponse({"error": str(error)}, status=502)
    return JsonResponse({"suggestions": suggestions})


@warehouse_staff_required
def product_create(request):
    russian = _is_russian(request)
    organization = request.organization
    plan_key = effective_plan_for_user(organization, request.user)
    if organization.products.filter(is_active=True).count() >= PLANS[plan_key]["product_limit"]:
        messages.warning(
            request,
            "В вашем тарифе достигнут лимит товаров. Архивируйте товары или выберите другой тариф."
            if russian
            else "Your plan's product limit has been reached. Archive products or choose another plan.",
        )
        return redirect("plans")
    if request.method == "POST":
        form = ProductForm(request.POST, organization=organization)
        if form.is_valid():
            with transaction.atomic():
                product = form.save(commit=False)
                product.organization = organization
                product.save()
                if product.quantity:
                    StockMovement.objects.create(
                        product=product,
                        organization=organization,
                        movement_type="adjust",
                        quantity=product.quantity,
                        notes="Начальный остаток" if russian else "Initial stock",
                        created_by=request.user,
                    )
            messages.success(request, "Товар добавлен." if russian else "Product added.")
            return redirect("products")
    else:
        form = ProductForm(organization=organization)

    return render(
        request,
        "warehouse/product_form.html",
        {"form": form, "title": "Добавить товар" if russian else "Add product"},
    )


@warehouse_staff_required
def product_update(request, pk):
    organization = request.organization
    product = get_object_or_404(Product, pk=pk, organization=organization, is_active=True)
    russian = _is_russian(request)
    if request.method == "POST":
        form = ProductForm(request.POST, instance=product, organization=organization)
        form.fields["quantity"].disabled = True
        if form.is_valid():
            form.save()
            messages.success(request, "Товар обновлён." if russian else "Product updated.")
            return redirect("products")
    else:
        form = ProductForm(instance=product, organization=organization)
        form.fields["quantity"].disabled = True
        form.fields["quantity"].help_text = (
            "Изменяйте остаток через раздел «Движения»."
            if russian
            else "Change stock using the Movements page."
        )

    return render(
        request,
        "warehouse/product_form.html",
        {"form": form, "title": "Изменить товар" if russian else "Edit product"},
    )


@warehouse_staff_required
def product_delete(request, pk):
    product = get_object_or_404(Product, pk=pk, organization=request.organization)
    if request.method == "POST":
        product.is_active = False
        product.save(update_fields=["is_active", "updated_at"])
        messages.success(
            request,
            "Товар архивирован; история операций сохранена."
            if _is_russian(request)
            else "Product archived; its transaction history was preserved.",
        )
        return redirect("products")
    return render(request, "warehouse/product_confirm_delete.html", {"product": product})


@warehouse_staff_required
def stock_movements(request):
    organization = request.organization
    movements, previous_page_url, next_page_url = _paginate(
        request,
        StockMovement.objects.filter(organization=organization)
        .select_related("product", "created_by")
        .order_by("-created_at"),
    )
    russian = _is_russian(request)
    if request.method == "POST":
        form = StockMovementForm(request.POST, organization=organization)
        if form.is_valid():
            movement = form.save(commit=False)
            with transaction.atomic():
                product = Product.objects.select_for_update().get(
                    pk=movement.product_id,
                    organization=organization,
                )
                if movement.movement_type == "out" and product.quantity < movement.quantity:
                    form.add_error(
                        "quantity",
                        "Недостаточно товара на складе."
                        if russian
                        else "Not enough stock for this outgoing movement.",
                    )
                else:
                    if movement.movement_type == "in":
                        product.quantity += movement.quantity
                    elif movement.movement_type == "out":
                        product.quantity -= movement.quantity
                    else:
                        product.quantity = movement.quantity
                    product.save(update_fields=["quantity", "updated_at"])
                    movement.organization = organization
                    movement.created_by = request.user
                    movement.save()
                    messages.success(
                        request,
                        "Движение сохранено."
                        if russian
                        else "Inventory movement recorded.",
                    )
                    return redirect("stock_movements")
    else:
        form = StockMovementForm(organization=organization)

    return render(
        request,
        "warehouse/stock_movements.html",
        {
            "movements": movements,
            "page_obj": movements,
            "previous_page_url": previous_page_url,
            "next_page_url": next_page_url,
            "form": form,
        },
    )


@warehouse_staff_required
def supplier_list(request):
    organization = request.organization
    suppliers = Supplier.objects.filter(organization=organization).order_by("name")
    russian = _is_russian(request)
    if request.method == "POST":
        form = SupplierForm(request.POST)
        if form.is_valid():
            supplier = form.save(commit=False)
            supplier.organization = organization
            supplier.save()
            messages.success(request, "Поставщик добавлен." if russian else "Supplier added.")
            return redirect("suppliers")
    else:
        form = SupplierForm()
    return render(
        request,
        "warehouse/partners.html",
        {
            "items": suppliers,
            "form": form,
            "entity": "Поставщики" if russian else "Suppliers",
            "add_label": "поставщика" if russian else "supplier",
            "empty_label": "Поставщиков пока нет." if russian else "No suppliers yet.",
        },
    )


@warehouse_staff_required
def customer_list(request):
    organization = request.organization
    customers = Customer.objects.filter(organization=organization).order_by("name")
    russian = _is_russian(request)
    if request.method == "POST":
        form = CustomerForm(request.POST)
        if form.is_valid():
            customer = form.save(commit=False)
            customer.organization = organization
            customer.save()
            messages.success(request, "Покупатель добавлен." if russian else "Customer added.")
            return redirect("customers")
    else:
        form = CustomerForm()
    return render(
        request,
        "warehouse/partners.html",
        {
            "items": customers,
            "form": form,
            "entity": "Покупатели" if russian else "Customers",
            "add_label": "покупателя" if russian else "customer",
            "empty_label": "Покупателей пока нет." if russian else "No customers yet.",
        },
    )


@warehouse_staff_required
def sale_create(request):
    russian = _is_russian(request)
    organization = request.organization
    if request.method == "POST":
        form = SaleForm(request.POST, organization=organization)
        if form.is_valid():
            with transaction.atomic():
                product = Product.objects.select_for_update().get(
                    pk=form.cleaned_data["product"].pk,
                    organization=organization,
                )
                quantity = form.cleaned_data["quantity"]
                if product.quantity < quantity:
                    form.add_error(
                        "quantity",
                        "Недостаточно товара на складе."
                        if russian
                        else "Not enough stock available.",
                    )
                else:
                    unit_price = product.unit_price
                    total_price = unit_price * quantity
                    product.quantity -= quantity
                    product.save(update_fields=["quantity", "updated_at"])
                    sale = Sale.objects.create(
                        product=product,
                        organization=organization,
                        customer=form.cleaned_data["customer"],
                        quantity=quantity,
                        unit_price=unit_price,
                        total_price=total_price,
                        created_by=request.user,
                    )
                    StockMovement.objects.create(
                        product=product,
                        organization=organization,
                        movement_type="out",
                        quantity=quantity,
                        notes=f"Sale #{sale.pk}",
                        created_by=request.user,
                    )
                    messages.success(request, "Продажа оформлена." if russian else "Sale recorded.")
                    return redirect("sales_report")
    else:
        form = SaleForm(organization=organization)

    return render(request, "warehouse/transaction_form.html", {
        "form": form,
        "title": "Оформить продажу" if russian else "Record a sale",
        "submit_label": "Оформить продажу" if russian else "Record sale",
        "cancel_url": "sales_report",
    })


@warehouse_staff_required
def purchase_create(request):
    russian = _is_russian(request)
    organization = request.organization
    if request.method == "POST":
        form = PurchaseForm(request.POST, organization=organization)
        if form.is_valid():
            with transaction.atomic():
                product = Product.objects.select_for_update().get(
                    pk=form.cleaned_data["product"].pk,
                    organization=organization,
                )
                quantity = form.cleaned_data["quantity"]
                unit_cost = form.cleaned_data["unit_cost"]
                purchase = Purchase.objects.create(
                    product=product,
                    organization=organization,
                    supplier=form.cleaned_data["supplier"],
                    quantity=quantity,
                    unit_cost=unit_cost,
                    total_cost=unit_cost * quantity,
                    reference=form.cleaned_data["reference"],
                    created_by=request.user,
                )
                product.quantity += quantity
                product.save(update_fields=["quantity", "updated_at"])
                StockMovement.objects.create(
                    product=product,
                    organization=organization,
                    movement_type="in",
                    quantity=quantity,
                    notes=f"Purchase #{purchase.pk}",
                    created_by=request.user,
                )
                messages.success(
                    request,
                    "Поставка принята и остаток обновлён."
                    if russian
                    else "Purchase received and stock updated.",
                )
                return redirect("purchases")
    else:
        form = PurchaseForm(organization=organization)

    return render(request, "warehouse/transaction_form.html", {
        "form": form,
        "title": "Оформить поставку" if russian else "Receive a purchase",
        "submit_label": "Принять поставку" if russian else "Receive purchase",
        "cancel_url": "purchases",
    })


@warehouse_staff_required
def purchase_list(request):
    organization = request.organization
    purchases_query = Purchase.objects.select_related(
        "product", "supplier", "created_by"
    ).filter(organization=organization).order_by("-received_at")
    total_spend = purchases_query.aggregate(total=Sum("total_cost"))["total"] or Decimal("0.00")
    purchases, previous_page_url, next_page_url = _paginate(request, purchases_query)
    return render(
        request,
        "warehouse/purchases.html",
        {
            "purchases": purchases,
            "page_obj": purchases,
            "previous_page_url": previous_page_url,
            "next_page_url": next_page_url,
            "total_spend": total_spend,
        },
    )


def _sales_period_start(organization, user):
    month_count = PLANS[effective_plan_for_user(organization, user)]["report_months"]
    today = timezone.localdate()
    year, month = today.year, today.month
    for _ in range(month_count - 1):
        month -= 1
        if month == 0:
            year -= 1
            month = 12
    return date(year, month, 1)


def _sales_report_range(request, organization):
    russian = _is_russian(request)
    today = timezone.localdate()
    history_start = _sales_period_start(organization, request.user)
    period = request.GET.get("period", "plan")

    if period == "plan":
        start_date, end_date = history_start, today
    elif period == "day":
        start_date, end_date = today, today
    elif period == "week":
        start_date, end_date = today - timedelta(days=today.weekday()), today
    elif period == "month":
        start_date, end_date = today.replace(day=1), today
    elif period == "year":
        start_date, end_date = today.replace(month=1, day=1), today
    elif period == "custom":
        start_date = parse_date(request.GET.get("start_date", ""))
        end_date = parse_date(request.GET.get("end_date", ""))
        if start_date is None or end_date is None:
            return period, None, None, (
                "Укажите корректные даты начала и окончания."
                if russian
                else "Enter valid start and end dates."
            )
    else:
        return period, None, None, (
            "Выберите корректный период отчёта."
            if russian
            else "Select a valid report period."
        )

    if start_date > end_date:
        return period, start_date, end_date, (
            "Дата начала не может быть позже даты окончания."
            if russian
            else "The start date must not be after the end date."
        )
    if end_date > today:
        return period, start_date, end_date, (
            "Отчёт не может включать будущие даты."
            if russian
            else "Reports cannot include future dates."
        )
    if start_date < history_start:
        return period, start_date, end_date, (
            (
                f"История вашего тарифа доступна с {history_start:%d.%m.%Y}. "
                "Выберите более короткий период или смените тариф."
                if russian
                else f"Your plan includes report history from {history_start:%Y-%m-%d}. "
                "Choose a shorter period or upgrade your plan."
            )
        )
    return period, start_date, end_date, ""


def _sales_chart_data(request, sales_query, start_date, end_date, period):
    if start_date is None or end_date is None:
        return []

    russian = _is_russian(request)
    if period in {"day", "week"} or (
        period == "custom" and (end_date - start_date).days <= 30
    ):
        daily_totals = {
            row["sold_at__date"]: row["total"]
            for row in sales_query.values("sold_at__date")
            .annotate(total=Sum("total_price"))
        }
        chart_data = []
        current_date = start_date
        while current_date <= end_date:
            revenue = daily_totals.get(current_date, Decimal("0.00"))
            label = (
                current_date.strftime("%d.%m")
                if russian
                else current_date.strftime("%b %d")
            )
            chart_data.append({"label": label, "revenue": revenue})
            current_date += timedelta(days=1)
    else:
        month_totals = {
            (row["sold_at__year"], row["sold_at__month"]): row["total"]
            for row in sales_query.values("sold_at__year", "sold_at__month")
            .annotate(total=Sum("total_price"))
        }
        current_year, current_month = start_date.year, start_date.month
        chart_data = []
        while (current_year, current_month) <= (end_date.year, end_date.month):
            revenue = month_totals.get(
                (current_year, current_month),
                Decimal("0.00"),
            )
            label = (
                f"{current_month:02d}/{current_year}"
                if russian
                else f"{month_abbr[current_month]} {current_year}"
            )
            chart_data.append({"label": label, "revenue": revenue})
            current_month += 1
            if current_month == 13:
                current_year += 1
                current_month = 1

    max_revenue = max(
        (item["revenue"] for item in chart_data),
        default=Decimal("0.00"),
    )
    for item in chart_data:
        item["width"] = (
            int(item["revenue"] * 100 / max_revenue)
            if max_revenue
            else 0
        )
    return chart_data


def _sales_report_queryset(request, organization):
    period, start_date, end_date, range_error = _sales_report_range(
        request,
        organization,
    )
    sales_query = Sale.objects.none()
    if not range_error:
        sales_query = Sale.objects.filter(
            organization=organization,
            sold_at__date__gte=start_date,
            sold_at__date__lte=end_date,
        )
    sales_query = sales_query.select_related(
        "product",
        "customer",
        "created_by",
    ).order_by("-sold_at")
    return period, start_date, end_date, range_error, sales_query


@warehouse_staff_required
def sales_report(request):
    organization = request.organization
    period, start_date, end_date, range_error, sales_query = (
        _sales_report_queryset(request, organization)
    )
    total_revenue = sales_query.aggregate(total=Sum("total_price"))["total"] or Decimal("0.00")
    sales, previous_page_url, next_page_url = _paginate(request, sales_query)
    report_months = PLANS[
        effective_plan_for_user(organization, request.user)
    ]["report_months"]
    return render(
        request,
        "warehouse/sales_report.html",
        {
            "sales": sales,
            "page_obj": sales,
            "previous_page_url": previous_page_url,
            "next_page_url": next_page_url,
            "total_revenue": total_revenue,
            "chart_data": _sales_chart_data(
                request,
                sales_query,
                start_date,
                end_date,
                period,
            ),
            "report_months": report_months,
            "can_view_extended_reports": report_months > 1,
            "can_export_sales_report": report_months > 1 and not range_error,
            "selected_period": period,
            "start_date": start_date,
            "end_date": end_date,
            "custom_start_date": request.GET.get("start_date", ""),
            "custom_end_date": request.GET.get("end_date", ""),
            "report_history_start": _sales_period_start(
                organization,
                request.user,
            ),
            "report_max_date": timezone.localdate(),
            "range_error": range_error,
        },
    )


def _sales_report_export(request, format_name):
    organization = request.organization
    russian = _is_russian(request)
    if effective_plan_for_user(organization, request.user) == "free":
        messages.info(
            request,
            "Экспорт отчётов доступен в тарифах Pro и Super."
            if russian
            else "Sales report exports are available on Pro and Super plans.",
        )
        return redirect("plan_detail", plan_key="pro")

    _, start_date, end_date, range_error, sales_query = (
        _sales_report_queryset(request, organization)
    )
    if range_error:
        messages.error(request, range_error)
        query_string = request.GET.urlencode()
        return redirect(
            f"{reverse('sales_report')}?{query_string}"
            if query_string
            else reverse("sales_report")
        )

    row_count = sales_query.count()
    if format_name == "pdf" and row_count > 5000:
        messages.error(
            request,
            "Для PDF выберите период не более чем на 5 000 продаж."
            if russian
            else "PDF reports are limited to 5,000 sales. Choose a shorter period.",
        )
        return redirect(f"{reverse('sales_report')}?{request.GET.urlencode()}")
    if format_name == "excel" and row_count > 1_048_575:
        messages.error(
            request,
            "Для Excel выберите период не более чем на 1 048 575 продаж."
            if russian
            else "Excel reports are limited to 1,048,575 sales. Choose a shorter period.",
        )
        return redirect(f"{reverse('sales_report')}?{request.GET.urlencode()}")

    total_revenue = sales_query.aggregate(total=Sum("total_price"))["total"] or Decimal("0.00")
    if format_name == "excel":
        return export_sales_excel(
            sales_query,
            total_revenue,
            start_date,
            end_date,
            russian,
        )
    try:
        return export_sales_pdf(
            sales_query,
            total_revenue,
            start_date,
            end_date,
            russian,
        )
    except ImproperlyConfigured:
        logger.exception("PDF sales export is missing a system font.")
        messages.error(
            request,
            "PDF-экспорт недоступен: на сервере не найден шрифт с поддержкой кириллицы."
            if russian
            else "PDF export is unavailable because the server has no Cyrillic-capable font installed.",
        )
        return redirect(f"{reverse('sales_report')}?{request.GET.urlencode()}")


@warehouse_staff_required
def export_sales_report_excel(request):
    return _sales_report_export(request, "excel")


@warehouse_staff_required
def export_sales_report_pdf(request):
    return _sales_report_export(request, "pdf")


@warehouse_staff_required
def export_products_csv(request):
    organization = request.organization
    if effective_plan_for_user(organization, request.user) == "free":
        messages.info(
            request,
            "Экспорт CSV доступен в тарифах Pro и Super."
            if _is_russian(request)
            else "CSV export is available on Pro and Super plans.",
        )
        return redirect("plans")
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = 'attachment; filename="warehouse-products.csv"'
    response.write("\ufeff")
    writer = csv.writer(response)
    if _is_russian(request):
        writer.writerow(["Название", "Артикул", "Категория", "Остаток", "Цена", "Минимум"])
    else:
        writer.writerow(["Name", "SKU", "Category", "Stock", "Price", "Minimum stock"])
    def safe_cell(value):
        value = str(value)
        return f"'{value}" if value.startswith(("=", "+", "-", "@")) else value

    for product in Product.objects.filter(
        organization=request.organization,
        is_active=True,
    ).select_related("category"):
        writer.writerow(
            [
                safe_cell(product.name),
                safe_cell(product.sku),
                safe_cell(product.category.name if product.category else ""),
                product.quantity,
                product.unit_price,
                product.minimum_stock,
            ]
        )
    return response


def plans(request):
    russian = _is_russian(request)
    organization = None
    current_plan = "free"
    is_admin = request.user.is_authenticated and request.user.is_superuser
    if request.user.is_authenticated:
        profile = UserProfile.objects.filter(user=request.user).select_related("organization").first()
        if profile:
            organization = profile.organization
            current_plan = effective_plan_for_user(organization, request.user)
        elif is_admin:
            current_plan = "super"

    plan_cards = []
    for key, definition in PLANS.items():
        plan_cards.append(
            {
                "key": key,
                "icon": definition["icon"],
                "name": definition["name"]["ru" if russian else "en"],
                "price": definition["price"],
                "limit": definition["product_limit"],
                "features": definition["features"]["ru" if russian else "en"],
                "is_current": current_plan == key,
                "paypal_ready": is_configured(key),
                "is_admin": is_admin,
                "detail_url": reverse("plan_detail", args=[key]),
            }
        )
    return render(
        request,
        "warehouse/plans.html",
        {
            "plan_cards": plan_cards,
            "organization": organization,
            "billing_ready": is_configured(),
            "subscription_status": organization.subscription_status if organization else "",
            "is_owner": organization.owner_id == request.user.pk
            if organization and request.user.is_authenticated
            else False,
            "paypal_mode": settings.PAYPAL_MODE,
            "is_admin": is_admin,
        },
    )


def plan_detail(request, plan_key):
    if plan_key not in PLANS:
        raise Http404("Plan not found.")

    russian = _is_russian(request)
    definition = PLANS[plan_key]
    profile = (
        UserProfile.objects.filter(user=request.user)
        .select_related("organization")
        .first()
        if request.user.is_authenticated
        else None
    )
    organization = profile.organization if profile else None
    is_admin = request.user.is_authenticated and request.user.is_superuser
    current_plan = (
        effective_plan_for_user(organization, request.user)
        if organization
        else "super"
        if is_admin
        else "free"
    )

    return render(
        request,
        "warehouse/plan_detail.html",
        {
            "plan_key": plan_key,
            "plan_name": definition["name"]["ru" if russian else "en"],
            "plan_icon": definition["icon"],
            "plan_price": definition["price"],
            "plan_limit": definition["product_limit"],
            "report_months": definition["report_months"],
            "plan_features": definition["features"]["ru" if russian else "en"],
            "is_current": current_plan == plan_key,
            "organization": organization,
            "is_admin": is_admin,
            "is_owner": organization is not None
            and organization.owner_id == request.user.pk,
            "subscription_status": organization.subscription_status
            if organization
            else "",
            "paypal_ready": is_configured(plan_key),
            "paypal_mode": settings.PAYPAL_MODE,
        },
    )


@warehouse_staff_required
@require_POST
def start_paypal_subscription(request, plan_key):
    organization = request.organization
    russian = _is_russian(request)
    if plan_key not in {"pro", "super"}:
        return HttpResponseForbidden("Unknown subscription plan.")
    if organization.owner_id != request.user.pk:
        messages.error(
            request,
            "Тариф может изменить только владелец компании."
            if russian
            else "Only the company owner can change the subscription.",
        )
        return redirect("plans")
    if has_paid_plan(organization):
        messages.info(
            request,
            "Сначала отмените текущую подписку, чтобы сменить тариф."
            if russian
            else "Cancel the current subscription before changing plans.",
        )
        return redirect("plans")
    if not is_configured(plan_key):
        messages.error(
            request,
            "Оплата PayPal ещё не настроена владельцем сайта."
            if russian
            else "PayPal checkout has not been configured by the site owner yet.",
        )
        return redirect("plans")

    plan_id = (
        settings.PAYPAL_PRO_PLAN_ID
        if plan_key == "pro"
        else settings.PAYPAL_SUPER_PLAN_ID
    )
    return_url = request.build_absolute_uri(reverse("paypal_return"))
    cancel_url = request.build_absolute_uri(reverse("plans"))
    payload = {
        "plan_id": plan_id,
        "custom_id": str(organization.pk),
        "application_context": {
            "brand_name": "Warehouse Pro",
            "user_action": "SUBSCRIBE_NOW",
            "shipping_preference": "NO_SHIPPING",
            "return_url": return_url,
            "cancel_url": cancel_url,
        },
    }
    try:
        result = api_request(
            "POST",
            "/v1/billing/subscriptions",
            payload,
            request_id=str(uuid.uuid4()),
        )
    except PayPalError:
        logger.exception("Unable to create a PayPal subscription.")
        messages.error(
            request,
            "Не удалось связаться с PayPal. Попробуйте позже."
            if russian
            else "PayPal could not be reached. Please try again later.",
        )
        return redirect("plans")

    subscription_id = result.get("id")
    approval_url = next(
        (link["href"] for link in result.get("links", []) if link.get("rel") == "approve"),
        None,
    )
    if not subscription_id or not approval_url:
        logger.error("PayPal subscription response did not contain an ID and approval link.")
        messages.error(
            request,
            "PayPal не вернул ссылку оплаты. Попробуйте позже."
            if russian
            else "PayPal did not provide an approval link. Please try again later.",
        )
        return redirect("plans")

    organization.pending_plan = plan_key
    organization.pending_paypal_subscription_id = subscription_id
    organization.subscription_status = "pending"
    organization.save(
        update_fields=[
            "pending_plan",
            "pending_paypal_subscription_id",
            "subscription_status",
        ]
    )
    return redirect(approval_url)


@login_required
def paypal_return(request):
    russian = _is_russian(request)
    profile = get_object_or_404(UserProfile, user=request.user)
    organization = profile.organization
    subscription_id = request.GET.get("subscription_id", "")
    if not subscription_id or subscription_id != organization.pending_paypal_subscription_id:
        messages.error(
            request,
            "Не удалось сверить платёж. Подписка не активирована."
            if russian
            else "The PayPal subscription could not be matched. Access was not activated.",
        )
        return redirect("plans")

    try:
        remote = api_request(
            "GET",
            f"/v1/billing/subscriptions/{subscription_id}",
        )
    except PayPalError:
        logger.exception("Unable to verify the PayPal subscription return.")
        messages.error(
            request,
            "Не удалось проверить статус PayPal. Подождите подтверждения или попробуйте обновить страницу."
            if russian
            else "We could not verify PayPal yet. Wait for confirmation or reload this page.",
        )
        return redirect("plans")

    expected_plan_id = (
        settings.PAYPAL_PRO_PLAN_ID
        if organization.pending_plan == "pro"
        else settings.PAYPAL_SUPER_PLAN_ID
        if organization.pending_plan == "super"
        else ""
    )
    if (
        remote.get("custom_id") != str(organization.pk)
        or remote.get("plan_id") != expected_plan_id
        or remote.get("status") != "ACTIVE"
    ):
        messages.error(
            request,
            "PayPal пока не подтвердил подписку. Доступ останется на текущем тарифе."
            if russian
            else "PayPal has not confirmed the subscription. Your current plan remains in effect.",
        )
        return redirect("plans")

    messages.info(
        request,
        "Оплата одобрена. Тариф активируется после подтверждения безопасного уведомления PayPal."
        if russian
        else "Payment approved. The plan will activate after PayPal's verified webhook arrives.",
    )
    return redirect("plans")


@warehouse_staff_required
@require_POST
def cancel_paypal_subscription(request):
    organization = request.organization
    russian = _is_russian(request)
    if organization.owner_id != request.user.pk:
        return HttpResponseForbidden(
            "Только владелец компании может отменить подписку."
            if russian
            else "Only the company owner can cancel the subscription."
        )
    if not has_paid_plan(organization) or not organization.paypal_subscription_id:
        messages.error(
            request,
            "Активная подписка не найдена."
            if russian
            else "No active subscription was found.",
        )
        return redirect("plans")

    try:
        api_request(
            "POST",
            f"/v1/billing/subscriptions/{organization.paypal_subscription_id}/cancel",
            {"reason": "Cancellation requested by the account owner"},
            request_id=str(uuid.uuid4()),
        )
    except PayPalError:
        logger.exception("Unable to cancel the PayPal subscription.")
        messages.error(
            request,
            "PayPal не смог отменить подписку. Попробуйте позже."
            if russian
            else "PayPal could not cancel the subscription. Please try again later.",
        )
        return redirect("plans")

    messages.success(
        request,
        "Запрос на отмену отправлен в PayPal. Тариф изменится после подтверждения."
        if russian
        else "Cancellation was sent to PayPal. Your plan will change after confirmation.",
    )
    return redirect("plans")


@csrf_exempt
@require_POST
def paypal_webhook(request):
    try:
        event = json.loads(request.body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return JsonResponse({"error": "Invalid JSON."}, status=400)
    if (
        not isinstance(event, dict)
        or not isinstance(event.get("id"), str)
        or not event["id"]
        or not isinstance(event.get("event_type"), str)
        or not isinstance(event.get("resource", {}), dict)
    ):
        return JsonResponse({"error": "Invalid webhook event."}, status=400)

    try:
        verified = verify_webhook(request.headers, event)
    except PayPalError:
        logger.exception("Unable to verify the PayPal webhook.")
        return JsonResponse({"error": "Verification is temporarily unavailable."}, status=503)
    if not verified:
        return JsonResponse({"error": "Invalid webhook signature."}, status=400)

    event_id = event["id"]
    event_type = event["event_type"]
    resource = event.get("resource", {})
    if PayPalWebhookEvent.objects.filter(event_id=event_id).exists():
        return JsonResponse({"status": "duplicate"})

    subscription_id = resource.get("id", "")
    if event_type.startswith("PAYMENT.SALE."):
        subscription_id = resource.get("billing_agreement_id", "")

    if event_type == "BILLING.SUBSCRIPTION.ACTIVATED" and subscription_id:
        try:
            remote = api_request(
                "GET",
                f"/v1/billing/subscriptions/{subscription_id}",
            )
        except PayPalError:
            logger.exception("Unable to reconcile the activated PayPal subscription.")
            return JsonResponse({"error": "Subscription check failed."}, status=503)

        expected = {
            settings.PAYPAL_PRO_PLAN_ID: "pro",
            settings.PAYPAL_SUPER_PLAN_ID: "super",
        }
        plan_key = expected.get(remote.get("plan_id"))
        organization_id = remote.get("custom_id")
        if (
            remote.get("status") == "ACTIVE"
            and plan_key
            and organization_id
            and str(organization_id).isdigit()
        ):
            organization = Organization.objects.filter(pk=int(organization_id)).first()
            if organization and (
                organization.pending_paypal_subscription_id == subscription_id
                or organization.paypal_subscription_id == subscription_id
            ):
                organization.plan = plan_key
                organization.subscription_status = "active"
                organization.paypal_subscription_id = subscription_id
                organization.pending_plan = ""
                organization.pending_paypal_subscription_id = None
                organization.save(
                    update_fields=[
                        "plan",
                        "subscription_status",
                        "paypal_subscription_id",
                        "pending_plan",
                        "pending_paypal_subscription_id",
                    ]
                )

    elif event_type in {
        "BILLING.SUBSCRIPTION.CANCELLED",
        "BILLING.SUBSCRIPTION.EXPIRED",
        "BILLING.SUBSCRIPTION.SUSPENDED",
    } and subscription_id:
        organization = Organization.objects.filter(
            Q(paypal_subscription_id=subscription_id)
            | Q(pending_paypal_subscription_id=subscription_id)
        ).first()
        if organization:
            organization.plan = "free"
            organization.subscription_status = (
                "suspended"
                if event_type == "BILLING.SUBSCRIPTION.SUSPENDED"
                else "cancelled"
            )
            organization.paypal_subscription_id = None
            organization.pending_plan = ""
            organization.pending_paypal_subscription_id = None
            organization.save(
                update_fields=[
                    "plan",
                    "subscription_status",
                    "paypal_subscription_id",
                    "pending_plan",
                    "pending_paypal_subscription_id",
                ]
            )

    _, created = PayPalWebhookEvent.objects.get_or_create(
        event_id=event_id,
        defaults={"event_type": event_type},
    )
    if not created:
        return JsonResponse({"status": "duplicate"})
    return JsonResponse({"status": "received"})
