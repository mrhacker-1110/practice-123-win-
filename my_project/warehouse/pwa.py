from pathlib import Path
from django.contrib.staticfiles import finders
from django.http import HttpResponse


def service_worker(request):
    script_path = finders.find("warehouse/sw.js")
    if not script_path:
        return HttpResponse(status=404)
    response = HttpResponse(Path(script_path).read_text(encoding="utf-8"), content_type="application/javascript; charset=utf-8")
    response["Service-Worker-Allowed"] = "/"
    response["Cache-Control"] = "no-cache"
    return response