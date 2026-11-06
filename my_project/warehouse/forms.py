import re

from django import forms
from django.contrib.auth import authenticate, get_user_model
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm, UsernameField
from django.db.models import Q
from django.utils.translation import get_language

from .models import Customer, Organization, Product, StockMovement, Supplier, UserProfile

User = get_user_model()
TERMS_VERSION = "2026-10-27-v1"


class WarehouseAuthenticationForm(AuthenticationForm):
    username = UsernameField(
        widget=forms.TextInput(
            attrs={
                "autofocus": True,
                "autocomplete": "username",
                "placeholder": "username or email",
                "class": "form-control",
            }
        )
    )
    password = forms.CharField(
        strip=False,
        widget=forms.PasswordInput(
            attrs={
                "autocomplete": "current-password",
                "placeholder": "••••••••",
                "class": "form-control",
            }
        ),
    )
    remember_me = forms.BooleanField(
        required=False,
        widget=forms.CheckboxInput(attrs={"class": "checkbox-input"}),
    )

    error_messages = {
        **AuthenticationForm.error_messages,
        "inactive": (
            "Аккаунт ещё не подтверждён. Проверьте почту или запросите повторное письмо."
            if get_language() == "ru"
            else "This account is not verified yet. Check your email or request a new confirmation link."
        ),
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        russian = get_language() == "ru"
        self.fields["remember_me"].label = (
            "Запомнить меня" if russian else "Remember me"
        )

    def confirm_login_allowed(self, user):
        if not user.is_active:
            raise forms.ValidationError(
                self.error_messages["inactive"],
                code="inactive",
            )
        super().confirm_login_allowed(user)

    def clean(self):
        username = self.cleaned_data.get("username")
        password = self.cleaned_data.get("password")

        if username and password:
            user = User.objects.filter(
                Q(username__iexact=username) | Q(email__iexact=username)
            ).first()
            if user is not None:
                self.user_cache = authenticate(
                    self.request,
                    username=user.username,
                    password=password,
                )
            else:
                self.user_cache = None

            if self.user_cache is None:
                raise self.get_invalid_login_error()
            self.confirm_login_allowed(self.user_cache)

        return self.cleaned_data


class RegistrationForm(UserCreationForm):
    email = forms.EmailField()
    company_name = forms.CharField(max_length=150)
    phone = forms.CharField(max_length=30, required=False)
    first_name = forms.CharField(max_length=150, required=False)
    last_name = forms.CharField(max_length=150, required=False)
    accept_terms = forms.BooleanField(required=True)

    class Meta(UserCreationForm.Meta):
        model = User
        fields = (
            "username",
            "email",
            "first_name",
            "last_name",
            "company_name",
            "phone",
        )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        russian = get_language() == "ru"
        labels = (
            {
                "username": "Логин",
                "email": "Электронная почта",
                "first_name": "Имя",
                "last_name": "Фамилия",
                "company_name": "Название компании",
                "phone": "Телефон",
                "password1": "Пароль",
                "password2": "Повторите пароль",
            }
            if russian
            else {
                "username": "Username",
                "email": "Email address",
                "first_name": "First name",
                "last_name": "Last name",
                "company_name": "Company name",
                "phone": "Phone",
                "password1": "Password",
                "password2": "Confirm password",
            }
        )
        for field_name, label in labels.items():
            self.fields[field_name].label = label

        placeholders = (
            {
                "username": "ivanov_warehouse",
                "email": "name@company.com",
                "first_name": "Иван",
                "last_name": "Иванов",
                "company_name": "Kuba Logistics",
                "phone": "+7 999 123-45-67",
                "password1": "Введите пароль",
                "password2": "Повторите пароль",
            }
            if russian
            else {
                "username": "warehouse_manager",
                "email": "name@company.com",
                "first_name": "John",
                "last_name": "Smith",
                "company_name": "Kuba Logistics",
                "phone": "+1 415 555-0101",
                "password1": "Enter password",
                "password2": "Repeat password",
            }
        )
        for field_name, placeholder in placeholders.items():
            self.fields[field_name].widget.attrs["placeholder"] = placeholder
            self.fields[field_name].widget.attrs["autocomplete"] = {
                "username": "username",
                "email": "email",
                "first_name": "given-name",
                "last_name": "family-name",
                "company_name": "organization",
                "phone": "tel",
                "password1": "new-password",
                "password2": "new-password",
            }.get(field_name, "off")

        self.fields["phone"].widget.attrs["inputmode"] = "tel"
        self.fields["phone"].widget.attrs["pattern"] = r"^\+?[0-9\s\-\(\)]{7,30}$"
        self.fields["password1"].widget.attrs["minlength"] = 8
        self.fields["password2"].widget.attrs["minlength"] = 8
        self.fields["accept_terms"].label = (
            "Я принимаю пользовательское соглашение и уведомление о конфиденциальности."
            if russian
            else "I agree to the Terms of Service and Privacy Notice."
        )
        self.fields["accept_terms"].widget.attrs["class"] = "consent-checkbox"
        self.fields["company_name"].help_text = (
            "Например: Kuba Logistics. Для вашей изолированной рабочей области."
            if russian
            else "Example: Kuba Logistics. This creates your private workspace."
        )
        self.fields["email"].help_text = (
            "Подтвердите адрес e-mail, чтобы активировать аккаунт."
            if russian
            else "Verify your email address to activate the account."
        )
        self.fields["phone"].help_text = (
            "Например: +7 999 123-45-67"
            if russian
            else "Example: +1 415 555-0101"
        )

    @staticmethod
    def password_requirements(password):
        rules = {
            "min_length": len(password) >= 8,
            "has_letter": any(ch.isalpha() for ch in password),
            "has_digit": any(ch.isdigit() for ch in password),
            "not_only_digits": any(not ch.isdigit() for ch in password),
        }
        return rules

    def clean_password1(self):
        password = self.cleaned_data.get("password1", "")
        rules = self.password_requirements(password)
        errors = []
        if not rules["min_length"]:
            errors.append(
                "Пароль должен содержать минимум 8 символов."
                if get_language() == "ru"
                else "Password must contain at least 8 characters."
            )
        if not rules["has_letter"]:
            errors.append(
                "Пароль должен содержать хотя бы одну букву."
                if get_language() == "ru"
                else "Password must contain at least one letter."
            )
        if not rules["has_digit"]:
            errors.append(
                "Пароль должен содержать хотя бы одну цифру."
                if get_language() == "ru"
                else "Password must contain at least one digit."
            )
        if not rules["not_only_digits"]:
            errors.append(
                "Пароль не должен состоять только из цифр."
                if get_language() == "ru"
                else "Password must not consist only of digits."
            )
        if errors:
            raise forms.ValidationError("; ".join(errors))
        return password

    def clean(self):
        cleaned_data = super().clean()
        password1 = self.data.get("password1", "")
        password2 = self.data.get("password2", "")
        if password1 and password2 and password1 != password2:
            self.add_error(
                "password2",
                "Пароли не совпадают. Повторите ввод ещё раз."
                if get_language() == "ru"
                else "Passwords do not match. Please re-enter them.",
            )
        return cleaned_data

    def clean_email(self):
        email = self.cleaned_data["email"].strip().casefold()
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError(
                "Этот адрес уже зарегистрирован."
                if get_language() == "ru"
                else "An account with this email already exists."
            )
        return email

    def clean_phone(self):
        phone = (self.cleaned_data.get("phone") or "").strip()
        if not phone:
            return ""
        if not re.fullmatch(r"^\+?[0-9\s\-\(\)]{7,30}$", phone):
            raise forms.ValidationError(
                "Телефон должен содержать только цифры, пробелы, скобки и знак +."
                if get_language() == "ru"
                else "Phone number must contain only digits, spaces, parentheses and a plus sign."
            )
        return phone


class ProfileForm(forms.ModelForm):
    company_name = forms.CharField(max_length=150)

    class Meta:
        model = UserProfile
        fields = ["full_name", "phone"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        russian = get_language() == "ru"
        self.fields["company_name"].initial = self.instance.organization.name
        labels = (
            {"full_name": "Имя и фамилия", "phone": "Телефон", "company_name": "Название компании"}
            if russian
            else {"full_name": "Full name", "phone": "Phone", "company_name": "Company name"}
        )
        for field_name, label in labels.items():
            self.fields[field_name].label = label

    def save(self, commit=True):
        profile = super().save(commit=commit)
        company_name = self.cleaned_data["company_name"].strip()
        if company_name:
            Organization.objects.filter(pk=profile.organization_id).update(name=company_name)
        return profile


class ProductForm(forms.ModelForm):
    class Meta:
        model = Product
        fields = [
            "name",
            "sku",
            "category",
            "supplier",
            "quantity",
            "unit_price",
            "minimum_stock",
            "description",
            "is_active",
        ]

    def __init__(self, *args, **kwargs):
        organization = kwargs.pop("organization", None)
        super().__init__(*args, **kwargs)
        russian = get_language() == "ru"
        if organization is not None:
            self.fields["category"].queryset = organization.categories.order_by("name")
            self.fields["supplier"].queryset = organization.suppliers.order_by("name")
        if russian:
            self.fields["name"].label = "Название"
            self.fields["sku"].label = "Артикул"
            self.fields["category"].label = "Категория"
            self.fields["supplier"].label = "Поставщик"
            self.fields["quantity"].label = "Количество"
            self.fields["unit_price"].label = "Цена за единицу"
            self.fields["minimum_stock"].label = "Минимальный остаток"
            self.fields["description"].label = "Описание"
            self.fields["is_active"].label = "Активный товар"
        self.fields["name"].widget.attrs["placeholder"] = "Product name" if not russian else "Название товара"
        self.fields["sku"].widget.attrs["placeholder"] = "SKU-001"
        self.fields["description"].widget = forms.Textarea(attrs={"rows": 4})


class StockMovementForm(forms.ModelForm):
    class Meta:
        model = StockMovement
        fields = ["product", "movement_type", "quantity", "notes"]

    def __init__(self, *args, **kwargs):
        organization = kwargs.pop("organization", None)
        super().__init__(*args, **kwargs)
        russian = get_language() == "ru"
        products = Product.objects.filter(is_active=True)
        if organization is not None:
            products = products.filter(organization=organization)
        self.fields["product"].queryset = products
        self.fields["movement_type"].choices = (
            (("in", "Приход"), ("out", "Расход"), ("adjust", "Корректировка"))
            if russian
            else (("in", "Incoming"), ("out", "Outgoing"), ("adjust", "Adjustment"))
        )
        self.fields["product"].label = "Товар" if russian else "Product"
        self.fields["movement_type"].label = "Тип операции" if russian else "Movement type"
        self.fields["quantity"].label = "Количество" if russian else "Quantity"
        self.fields["notes"].label = "Комментарий" if russian else "Notes"

    def clean_quantity(self):
        quantity = self.cleaned_data["quantity"]
        if self.cleaned_data.get("movement_type") != "adjust" and quantity < 1:
            raise forms.ValidationError(
                "Количество должно быть больше нуля."
                if get_language() == "ru"
                else "Quantity must be greater than zero."
            )
        return quantity


class SaleForm(forms.Form):
    product = forms.ModelChoiceField(queryset=Product.objects.none())
    customer = forms.ModelChoiceField(queryset=Customer.objects.all(), required=False)
    quantity = forms.IntegerField(min_value=1)

    def __init__(self, *args, **kwargs):
        organization = kwargs.pop("organization", None)
        super().__init__(*args, **kwargs)
        russian = get_language() == "ru"
        products = Product.objects.filter(is_active=True)
        customers = Customer.objects.all()
        if organization is not None:
            products = products.filter(organization=organization)
            customers = customers.filter(organization=organization)
        self.fields["product"].queryset = products
        self.fields["customer"].queryset = customers
        self.fields["product"].label = "Товар" if russian else "Product"
        self.fields["customer"].label = "Покупатель" if russian else "Customer"
        self.fields["quantity"].label = "Количество" if russian else "Quantity"

    def clean(self):
        cleaned_data = super().clean()
        product = cleaned_data.get("product")
        quantity = cleaned_data.get("quantity")
        if product and quantity and quantity > product.quantity:
            self.add_error(
                "quantity",
                "На складе недостаточно товара."
                if get_language() == "ru"
                else "There is not enough stock for this sale.",
            )
        return cleaned_data


class PurchaseForm(forms.Form):
    product = forms.ModelChoiceField(queryset=Product.objects.none())
    supplier = forms.ModelChoiceField(queryset=Supplier.objects.all(), required=False)
    quantity = forms.IntegerField(min_value=1)
    unit_cost = forms.DecimalField(min_value=0, decimal_places=2, max_digits=10)
    reference = forms.CharField(max_length=80, required=False)

    def __init__(self, *args, **kwargs):
        organization = kwargs.pop("organization", None)
        super().__init__(*args, **kwargs)
        russian = get_language() == "ru"
        products = Product.objects.filter(is_active=True)
        suppliers = Supplier.objects.all()
        if organization is not None:
            products = products.filter(organization=organization)
            suppliers = suppliers.filter(organization=organization)
        self.fields["product"].queryset = products
        self.fields["supplier"].queryset = suppliers
        labels = (
            {"product": "Товар", "supplier": "Поставщик", "quantity": "Количество", "unit_cost": "Закупочная цена", "reference": "Номер документа"}
            if russian
            else {"product": "Product", "supplier": "Supplier", "quantity": "Quantity", "unit_cost": "Unit cost", "reference": "Reference"}
        )
        for field_name, label in labels.items():
            self.fields[field_name].label = label


class SupplierForm(forms.ModelForm):
    class Meta:
        model = Supplier
        fields = ["name", "company", "phone", "email"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if get_language() == "ru":
            self.fields["name"].label = "Контактное лицо"
            self.fields["company"].label = "Компания"
            self.fields["phone"].label = "Телефон"
            self.fields["email"].label = "Электронная почта"


class CustomerForm(forms.ModelForm):
    class Meta:
        model = Customer
        fields = ["name", "company", "phone", "email"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if get_language() == "ru":
            self.fields["name"].label = "Имя"
            self.fields["company"].label = "Компания"
            self.fields["phone"].label = "Телефон"
            self.fields["email"].label = "Электронная почта"
