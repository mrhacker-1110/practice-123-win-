# Warehouse Pro

Warehouse Pro is a Django inventory application with an HTML, CSS, and JavaScript
interface. The interface supports English and Russian. Each registered company
gets a separate workspace for its inventory, customers, suppliers, and transactions.

## Run locally

```powershell
cd C:\Users\HP\OneDrive\Desktop\practica\my_project
python -m pip install -r requirements.txt
python manage.py migrate
python manage.py runserver
```

Open http://127.0.0.1:8000 and create an account. New accounts must confirm their
email address before they can use the warehouse. With the default local settings,
confirmation and password-reset emails are printed in the runserver console.

To create an administrator, run `python manage.py createsuperuser`; administrator
accounts can use `/admin/`.

## Demo data

To populate the warehouse with sample products, suppliers, categories, and a demo
workspace, run:

```powershell
python manage.py seed_demo_data
```

This creates a default demo workspace and a login account with the username
`demo-admin` and password `Demo12345!` unless you pass custom arguments.

## Features

- Product catalog with search, category and stock filters, CSV export, and a
  Pro/Super CSV import flow with validation, preview, and explicit confirmation.
- CSV import accepts UTF-8 files with English or Russian column headers, up to
  500 new products and 5 MB per file. It never overwrites existing SKUs,
  rejects invalid files without partial imports, and records starting stock
  movements for imported products with an initial quantity.
- Supplier and customer management, purchases, sales, and stock adjustments.
- Stock validation and transaction history.
- Product removal archives the item instead of deleting it. Products with sales,
  purchases, or stock movements, and workspaces containing records, are protected
  from hard deletion; transactions are read-only in the admin.
- Sales history and analytics over the time range included in each subscription.
- Sales reports can be filtered by today, this week, this month, this year, the
  full plan history, or a custom date range. Pro/Super users can export the
  selected report to Excel or PDF; exports stay inside the current workspace.
  Custom dates cannot exceed the report-history limit of the active plan.
- Company profile and organization-level data isolation.
- Email confirmation, resend-confirmation, and password-reset flows.
- Required acceptance of the Terms of Service and Privacy Notice at registration;
  the acceptance time and notice version are recorded with the account.
- Responsive navigation, system/light/dark appearance preferences, and inline
  basic form validation.
- Dashboard stock-by-category chart and plan-limited sales charts.
- Separate plan detail pages show included features, monthly prices, and the
  PayPal checkout action. Paid features unlock only after a verified active
  subscription; site superusers can use Super-level features without billing.
- Super includes an inventory advisor that uses local calculations over recent
  sales and stock levels to suggest reorder quantities. It is not a generative
  AI service; warehouse data is not sent to an external AI or API.
- The product form can send a mobile photo to Gemini for editable product field
  suggestions. A person must review and save the product.
- The AI consultant answers warehouse and app questions in a separate chat; it
  does not access or change warehouse records. Text history is saved per user and
  workspace and remains available when navigating between pages.
- The consultant accepts photos, PDF, TXT, CSV, and JSON files up to 8 MB. Files
  are sent to Gemini for analysis; the app retains only the file name in chat history.
- Excel and PDF reports use `openpyxl` and ReportLab. PDF generation requires a
  system font with Cyrillic support (for example Arial or DejaVu Sans).
- Superuser administration pages for accounts, company workspaces, and profiles.
- English and Russian interface.
- Free, Pro, and Super subscription tiers. The configured list prices are $0,
  $19/month, and $49/month; paid checkout is unavailable until PayPal is configured.

## Email configuration

For production, provide SMTP details using environment variables:

```powershell
$env:EMAIL_HOST = "smtp.example.com"
$env:EMAIL_PORT = "587"
$env:EMAIL_HOST_USER = "your-smtp-user"
$env:EMAIL_HOST_PASSWORD = "your-smtp-password"
$env:EMAIL_USE_TLS = "true"
$env:DEFAULT_FROM_EMAIL = "Warehouse Pro <no-reply@example.com>"
```

Do not commit credentials. The local development email backend writes messages to
the console; production uses SMTP.

## PayPal subscriptions

Paid subscriptions use PayPal's server-side subscription API. The application
activates a plan only after a signed webhook is verified and the subscription is
re-checked with PayPal. Configure the following environment variables:

```powershell
$env:PAYPAL_MODE = "sandbox"
$env:PAYPAL_CLIENT_ID = "..."
$env:PAYPAL_CLIENT_SECRET = "..."
$env:PAYPAL_WEBHOOK_ID = "..."
$env:PAYPAL_PRO_PLAN_ID = "..."
$env:PAYPAL_SUPER_PLAN_ID = "..."
```

Create monthly PayPal plans for $19 and $49, then register the webhook endpoint
`https://your-domain.example/billing/paypal/webhook/` for subscription activation,
cancellation, expiration, and suspension events. Use `PAYPAL_MODE=live` only after
the sandbox flow has been tested. Keep all PayPal credentials out of source control.

The included Terms of Service and Privacy Notice are starter drafts, not legal
advice. Review and adapt them to the actual data-retention and service practices
and applicable law before launching publicly.

## Gemini photo suggestions

Set a Gemini API key in the server environment to enable photo suggestions:

```powershell
$env:GEMINI_API_KEY = "..."
$env:GEMINI_MODEL = "gemini-3.8-flash"
$env:GEMINI_CHAT_MODEL = "gemini-3.5-flash-lite"
```

Product photos and consultant attachments are sent to Google Gemini for processing.
The app stores consultant text history and attachment names in the local database,
but does not store attached file contents. Keep the API key out of source control.
Mobile camera capture requires HTTPS outside local development.

## Production deployment

The local database is SQLite. Before deploying, configure a production database,
a unique `DJANGO_SECRET_KEY`, `DJANGO_DEBUG=false`, and `DJANGO_ALLOWED_HOSTS`
(comma-separated host names), plus HTTPS and SMTP. Do not expose Django's
development server as a production service. Review Django's deployment checklist
for the target hosting environment.

The local development email backend safely writes confirmation emails to the
runserver console, including on Windows terminals that cannot display Cyrillic.
If the terminal does not support UTF-8, Russian text is escaped while the
confirmation link remains readable and usable.
