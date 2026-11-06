import os
from io import BytesIO
from pathlib import Path
from xml.sax.saxutils import escape

from django.core.exceptions import ImproperlyConfigured
from django.http import HttpResponse
from django.utils import timezone
from openpyxl.cell import WriteOnlyCell
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Table, TableStyle


def _safe_spreadsheet_text(value):
    value = str(value or "")
    if value.startswith(("=", "+", "-", "@", "\t", "\r")):
        return f"'{value}"
    return value


def _sales_headers(russian):
    if russian:
        return [
            "Дата",
            "Товар",
            "Артикул",
            "Покупатель",
            "Количество",
            "Цена за единицу",
            "Сумма",
            "Оператор",
        ]
    return [
        "Date",
        "Product",
        "SKU",
        "Customer",
        "Quantity",
        "Unit price",
        "Total",
        "Recorded by",
    ]


def _sale_values(sale):
    return [
        timezone.localtime(sale.sold_at).date(),
        sale.product.name,
        sale.product.sku,
        sale.customer.name if sale.customer else "",
        sale.quantity,
        sale.unit_price,
        sale.total_price,
        sale.created_by.username if sale.created_by else "",
    ]


def export_sales_excel(sales, total_revenue, start_date, end_date, russian):
    workbook = Workbook(write_only=True)
    sheet = workbook.create_sheet(
        "Продажи" if russian else "Sales"
    )
    sheet.freeze_panes = "A2"
    header_cells = []
    for header in _sales_headers(russian):
        cell = WriteOnlyCell(sheet, value=header)
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill(fill_type="solid", fgColor="1D4ED8")
        header_cells.append(cell)
    sheet.append(header_cells)

    row_count = 1
    for sale in sales:
        values = _sale_values(sale)
        safe_values = [
            _safe_spreadsheet_text(value)
            if isinstance(value, str)
            else float(value)
            if hasattr(value, "as_tuple")
            else value
            for value in values
        ]
        sheet.append(safe_values)
        row_count += 1

    total_label = "Итого" if russian else "Total"
    revenue_label = "Выручка" if russian else "Revenue"
    sheet.append([total_label, "", "", "", "", "", float(total_revenue), revenue_label])
    sheet.auto_filter.ref = f"A1:H{row_count}"
    for column, width in zip("ABCDEFGH", (14, 30, 18, 26, 14, 18, 18, 22)):
        sheet.column_dimensions[column].width = width

    buffer = BytesIO()
    workbook.save(buffer)
    response = HttpResponse(
        buffer.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = (
        f'attachment; filename="sales-report-{start_date.isoformat()}-{end_date.isoformat()}.xlsx"'
    )
    return response


def _font_paths():
    windows_fonts = Path(os.environ.get("WINDIR", "C:\\Windows")) / "Fonts"
    candidates = (
        (
            windows_fonts / "arial.ttf",
            windows_fonts / "arialbd.ttf",
        ),
        (
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
        ),
        (
            Path("/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf"),
            Path("/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf"),
        ),
    )
    return next(
        ((regular, bold) for regular, bold in candidates if regular.is_file() and bold.is_file()),
        None,
    )


def export_sales_pdf(sales, total_revenue, start_date, end_date, russian):
    font_paths = _font_paths()
    if font_paths is None:
        raise ImproperlyConfigured(
            "A system font with Cyrillic support is required for PDF exports."
        )

    regular_font, bold_font = font_paths
    registered_fonts = set(pdfmetrics.getRegisteredFontNames())
    if "WarehouseReportRegular" not in registered_fonts:
        pdfmetrics.registerFont(TTFont("WarehouseReportRegular", str(regular_font)))
    if "WarehouseReportBold" not in registered_fonts:
        pdfmetrics.registerFont(TTFont("WarehouseReportBold", str(bold_font)))

    buffer = BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=landscape(A4),
        rightMargin=24,
        leftMargin=24,
        topMargin=24,
        bottomMargin=24,
        title="Отчёт по продажам" if russian else "Sales report",
    )
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "WarehouseReportTitle",
        parent=styles["Title"],
        fontName="WarehouseReportBold",
    )
    body_style = ParagraphStyle(
        "WarehouseReportBody",
        parent=styles["BodyText"],
        fontName="WarehouseReportRegular",
        fontSize=7,
        leading=9,
        wordWrap="CJK",
    )
    story = [
        Paragraph(
            "Отчёт по продажам" if russian else "Sales report",
            title_style,
        ),
        Paragraph(
            (
                f"Период: {start_date:%d.%m.%Y} — {end_date:%d.%m.%Y}"
                if russian
                else f"Period: {start_date:%Y-%m-%d} to {end_date:%Y-%m-%d}"
            ),
            body_style,
        ),
        Paragraph(
            (
                f"Выручка за период: {total_revenue:.2f}"
                if russian
                else f"Revenue for this period: {total_revenue:.2f}"
            ),
            body_style,
        ),
    ]

    table_data = [
        [Paragraph(escape(header), body_style) for header in _sales_headers(russian)]
    ]
    for sale in sales:
        values = _sale_values(sale)
        table_data.append(
            [
                Paragraph(escape(value.isoformat()) if hasattr(value, "isoformat") else escape(str(value)), body_style)
                for value in values
            ]
        )

    table = Table(
        table_data,
        colWidths=(86, 130, 70, 105, 54, 80, 72, 112),
        repeatRows=1,
        hAlign="LEFT",
    )
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1D4ED8")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "WarehouseReportBold"),
                ("FONTNAME", (0, 1), (-1, -1), "WarehouseReportRegular"),
                ("FONTSIZE", (0, 0), (-1, -1), 7),
                ("LEADING", (0, 0), (-1, -1), 9),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#CBD5E1")),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), (colors.white, colors.HexColor("#F3F6FB"))),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    story.append(table)
    document.build(story)

    response = HttpResponse(buffer.getvalue(), content_type="application/pdf")
    response["Content-Disposition"] = (
        f'attachment; filename="sales-report-{start_date.isoformat()}-{end_date.isoformat()}.pdf"'
    )
    return response
