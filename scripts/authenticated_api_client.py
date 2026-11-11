"""Authenticated HTTP client for trusted local AgentsWatch maintenance scripts."""

import json
from http.cookiejar import CookieJar
from urllib.error import HTTPError
from urllib.request import HTTPCookieProcessor, Request, build_opener

import psycopg


class AuthenticatedApiClient:
    def __init__(self, api_url, username, password, database_url):
        self.api_url = api_url.rstrip("/")
        self.username = username
        self.password = password
        self.database_url = database_url
        self.cookies = CookieJar()
        self.opener = build_opener(HTTPCookieProcessor(self.cookies))
        self.login()

    def _csrf_token(self):
        return next(
            (cookie.value for cookie in self.cookies if cookie.name == "agentswatch_csrf"),
            None,
        )

    def _send(
        self, method, path, payload=None, authenticated=True, confirmation_id=None,
        confirmation_header="X-Confirmation-ID",
    ):
        headers = {"Content-Type": "application/json"}
        if authenticated and method in {"POST", "PUT", "PATCH", "DELETE"}:
            headers["X-CSRF-Token"] = self._csrf_token() or ""
        if confirmation_id:
            headers[confirmation_header] = confirmation_id
        request = Request(
            self.api_url + path,
            data=None if payload is None else json.dumps(payload).encode(),
            method=method,
            headers=headers,
        )
        try:
            with self.opener.open(request, timeout=15) as response:
                return response.status, json.load(response)
        except HTTPError as exc:
            try:
                detail = json.load(exc).get("detail", str(exc))
            except Exception:
                detail = str(exc)
            if (
                authenticated
                and confirmation_id is None
                and exc.code == 409
                and isinstance(detail, dict)
                and detail.get("code") == "CONFIRMATION_REQUIRED"
                and detail.get("confirmation_id")
            ):
                return self._send(
                    method,
                    path,
                    payload,
                    authenticated=True,
                    confirmation_id=detail["confirmation_id"],
                    confirmation_header=detail.get(
                        "confirmation_header", "X-Confirmation-ID",
                    ),
                )
            raise RuntimeError(f"AgentsWatch API {method} {path} failed: {detail}") from exc

    def login(self):
        _, challenge_response = self._send(
            "POST", "/api/v1/auth/challenge", {}, authenticated=False,
        )
        challenge = challenge_response["data"]
        with psycopg.connect(self.database_url) as connection:
            row = connection.execute(
                "SELECT expected_offset FROM security.login_challenge WHERE challenge_id = %s",
                (challenge["challenge_id"],),
            ).fetchone()
        if row is None:
            raise RuntimeError("AgentsWatch login challenge was not persisted")
        self._send(
            "POST",
            "/api/v1/auth/login",
            {
                "username": self.username,
                "password": self.password,
                "challenge_id": challenge["challenge_id"],
                "challenge_offset": row[0],
            },
            authenticated=False,
        )

    def request(self, method, path, payload=None):
        return self._send(method, path, payload, authenticated=True)
