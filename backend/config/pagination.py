"""Постраничная выдача списков.

Клиент может попросить страницу крупнее (`page_size`), но не больше 500:
без параметра запросы вида `?page_size=400` молча получали 50 записей,
и экран показывал неполный список без признаков обрезки.
"""

from rest_framework.pagination import PageNumberPagination


class Pagination(PageNumberPagination):
    page_size = 50
    page_size_query_param = "page_size"
    max_page_size = 500
