"""Разрешение запросов от фронтенда, работающего на другом порту.

Отдельная реализация вместо django-cors-headers: нужен один заголовок,
лишняя зависимость в проекте такого размера не оправдана.
"""

from django.conf import settings
from django.http import HttpResponse


class CorsMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.method == "OPTIONS" and "HTTP_ORIGIN" in request.META:
            response = HttpResponse(status=204)
        else:
            response = self.get_response(request)

        origin = request.META.get("HTTP_ORIGIN")
        allowed = getattr(settings, "CORS_ALLOWED_ORIGINS", [])
        if origin and origin in allowed:
            response["Access-Control-Allow-Origin"] = origin
            response["Access-Control-Allow-Credentials"] = "true"
            response["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
            response["Access-Control-Allow-Methods"] = "GET, POST, PATCH, DELETE, OPTIONS"
        return response
