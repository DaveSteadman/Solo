from __future__ import annotations

from http import HTTPStatus
from typing import Any, Callable

from common_utils.web import send_json


EndpointFn = Callable[[Any, dict[str, list[str]], dict[str, Any]], dict[str, Any]]


class EndpointRouter:
    def __init__(self) -> None:
        self.routes: dict[tuple[str, str], EndpointFn] = {}

    def add(self, method: str, path: str, fn: EndpointFn) -> None:
        self.routes[(method.upper(), path)] = fn

    def handle(self, handler: Any, method: str, path: str, params: dict[str, list[str]], payload: dict[str, Any]) -> bool:
        fn = self.routes.get((method.upper(), path))
        if fn is None:
            return False

        try:
            result = fn(self.store, params, payload)
            send_json(handler, result)
        except Exception as exc:
            handler.send_error(HTTPStatus.BAD_REQUEST, str(exc))

        return True