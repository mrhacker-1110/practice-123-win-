from django.conf.urls.i18n import set_language
from django.contrib.auth import views as auth_views
from django.contrib import admin
from django.urls import include, path, reverse_lazy
from warehouse import views
from warehouse.pwa import service_worker

urlpatterns = [
    path("sw.js", service_worker, name="service_worker"),
    path("admin/", admin.site.urls),
    path(
        "accounts/login/",
        views.WarehouseLoginView.as_view(),
        name="login",
    ),
    path("accounts/logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("accounts/register/", views.register, name="register"),
    path("accounts/registered/", views.registration_complete, name="registration_complete"),
    path(
        "legal/<str:document>/",
        views.legal_information,
        name="legal_information",
    ),
    path(
        "accounts/verify/<str:uidb64>/<str:token>/",
        views.verify_email,
        name="verify_email",
    ),
    path(
        "accounts/resend-verification/",
        views.resend_verification,
        name="resend_verification",
    ),
    path(
        "accounts/password-reset/",
        auth_views.PasswordResetView.as_view(
            template_name="warehouse/password_reset_form.html",
            email_template_name="warehouse/password_reset_email.txt",
            subject_template_name="warehouse/password_reset_subject.txt",
            success_url=reverse_lazy("password_reset_done"),
        ),
        name="password_reset",
    ),
    path(
        "accounts/password-reset/done/",
        auth_views.PasswordResetDoneView.as_view(
            template_name="warehouse/password_reset_done.html"
        ),
        name="password_reset_done",
    ),
    path(
        "accounts/reset/<uidb64>/<token>/",
        auth_views.PasswordResetConfirmView.as_view(
            template_name="warehouse/password_reset_confirm.html",
            success_url=reverse_lazy("password_reset_complete"),
        ),
        name="password_reset_confirm",
    ),
    path(
        "accounts/reset/done/",
        auth_views.PasswordResetCompleteView.as_view(
            template_name="warehouse/password_reset_complete.html"
        ),
        name="password_reset_complete",
    ),
    path("accounts/profile/", views.profile, name="profile"),
    path("plans/", views.plans, name="plans"),
    path(
        "plans/<str:plan_key>/",
        views.plan_detail,
        name="plan_detail",
    ),
    path(
        "plans/subscribe/<str:plan_key>/",
        views.start_paypal_subscription,
        name="start_paypal_subscription",
    ),
    path(
        "plans/cancel/",
        views.cancel_paypal_subscription,
        name="cancel_paypal_subscription",
    ),
    path(
        "billing/paypal/return/",
        views.paypal_return,
        name="paypal_return",
    ),
    path("billing/paypal/webhook/", views.paypal_webhook, name="paypal_webhook"),
    path("i18n/setlang/", set_language, name="set_language"),
    path("", include("warehouse.urls")),
]
