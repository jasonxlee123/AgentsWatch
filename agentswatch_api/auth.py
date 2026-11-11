from __future__ import annotations

import base64
import hashlib
import hmac
import io
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from PIL import Image, ImageDraw

from .database import WriteDatabase


USERNAME_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{2,63}$")
PASSWORD_MIN_LENGTH = 12
PASSWORD_MAX_LENGTH = 128
SESSION_HOURS = 8
CHALLENGE_TTL_SECONDS = 120
CHALLENGE_TOLERANCE = 5
MAX_LOGIN_ATTEMPTS = 5
LOCKOUT_MINUTES = 15
API_KEY_IDENTIFIER_BYTES = 8
API_KEY_SECRET_BYTES = 32
API_KEY_PATTERN = re.compile(r"^awk_[0-9a-f]{16}_[A-Za-z0-9_-]{43}$")

PERMISSION_MATRIX = {
    "viewer": ("view",),
    "operator": ("view", "manage_assets", "execute_automation"),
    "admin": (
        "view",
        "manage_assets",
        "execute_automation",
        "manage_automation_library",
        "manage_users",
    ),
}


class AuthenticationError(ValueError):
    pass


class AuthorizationError(PermissionError):
    pass


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def generate_api_key() -> tuple[str, str]:
    """Return a new bearer credential and its non-secret display prefix."""
    prefix = f"awk_{secrets.token_hex(API_KEY_IDENTIFIER_BYTES)}"
    secret = secrets.token_urlsafe(API_KEY_SECRET_BYTES)
    return f"{prefix}_{secret}", prefix


def validate_username(username: str) -> str:
    normalized = username.strip()
    if not USERNAME_PATTERN.fullmatch(normalized):
        raise ValueError("用户名需为 3-64 位，并以字母开头，仅包含字母、数字、点、下划线或连字符")
    return normalized


def password_policy_errors(password: str, username: str | None = None) -> list[str]:
    errors: list[str] = []
    if len(password) < PASSWORD_MIN_LENGTH:
        errors.append(f"至少 {PASSWORD_MIN_LENGTH} 位")
    if len(password) > PASSWORD_MAX_LENGTH:
        errors.append(f"不能超过 {PASSWORD_MAX_LENGTH} 位")
    if not any(character.isupper() for character in password):
        errors.append("至少一个大写字母")
    if not any(character.islower() for character in password):
        errors.append("至少一个小写字母")
    if not any(character.isdigit() for character in password):
        errors.append("至少一个数字")
    if not any(not character.isalnum() and not character.isspace() for character in password):
        errors.append("至少一个符号")
    if any(character.isspace() for character in password):
        errors.append("不能包含空白字符")
    if username and len(username) >= 3 and username.lower() in password.lower():
        errors.append("不能包含用户名")
    return errors


def validate_password(password: str, username: str | None = None) -> None:
    errors = password_policy_errors(password, username)
    if errors:
        raise ValueError("密码必须" + "、".join(errors))


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    n, r, p = 16384, 8, 1
    derived = hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=n, r=r, p=p, dklen=32, maxmem=64 * 1024 * 1024,
    )
    return f"scrypt${n}${r}${p}${_b64encode(salt)}${_b64encode(derived)}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, n, r, p, salt, expected = encoded.split("$", 5)
        if algorithm != "scrypt":
            return False
        actual = hashlib.scrypt(
            password.encode("utf-8"),
            salt=_b64decode(salt),
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(_b64decode(expected)),
            maxmem=64 * 1024 * 1024,
        )
        return hmac.compare_digest(actual, _b64decode(expected))
    except (TypeError, ValueError):
        return False


_DUMMY_PASSWORD_HASH = hash_password(secrets.token_urlsafe(32))


def permissions_for_role(role: str) -> list[str]:
    return list(PERMISSION_MATRIX.get(role, ()))


def public_user(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "user_id": str(row["user_id"]),
        "username": row["username"],
        "display_name": row["display_name"],
        "role": row["role"],
        "status": row["status"],
        "permissions": permissions_for_role(row["role"]),
        "last_login_at": row.get("last_login_at"),
        "row_version": row.get("row_version"),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


def public_api_key(row: dict[str, Any] | None) -> dict[str, Any]:
    if row is None:
        return {
            "active": False,
            "api_key_id": None,
            "prefix": None,
            "created_at": None,
            "last_used_at": None,
        }
    return {
        "active": row.get("revoked_at") is None,
        "api_key_id": str(row["api_key_id"]),
        "prefix": row["key_prefix"],
        "created_at": row["created_at"],
        "last_used_at": row.get("last_used_at"),
    }


class AuthService:
    def __init__(self, database: WriteDatabase | None = None):
        self.database = database or WriteDatabase()

    @staticmethod
    def _challenge_image(target: int) -> bytes:
        palette = ["#0d8f91", "#176ec5", "#7667d6", "#e4a11b", "#5e7187"]
        image = Image.new("RGB", (280, 80), "#eef7f7")
        draw = ImageDraw.Draw(image, "RGBA")
        for y in range(80):
            draw.line((0, y, 280, y), fill=(238 - y // 8, 247 - y // 10, 247, 255))
        for _ in range(38):
            x = secrets.randbelow(260) + 10
            y = secrets.randbelow(62) + 9
            radius = secrets.randbelow(8) + 3
            color = secrets.choice(palette)
            rgb = tuple(int(color[index:index + 2], 16) for index in (1, 3, 5))
            draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=(*rgb, 56))
        shape = [
            (target, 21), (target + 14, 21), (target + 15, 15), (target + 20, 11),
            (target + 25, 15), (target + 26, 21), (target + 40, 21),
            (target + 40, 61), (target + 26, 61), (target + 25, 67),
            (target + 20, 71), (target + 15, 67), (target + 14, 61), (target, 61),
        ]
        draw.polygon(shape, fill=(255, 255, 255, 166), outline=(11, 119, 123, 255), width=2)
        buffer = io.BytesIO()
        image.save(buffer, format="PNG", optimize=True)
        return buffer.getvalue()

    def create_challenge(self, client_fingerprint: str) -> dict[str, Any]:
        target = secrets.randbelow(177) + 52
        expires_at = utcnow() + timedelta(seconds=CHALLENGE_TTL_SECONDS)
        with self.database.connection() as connection:
            connection.execute(
                "DELETE FROM security.login_challenge WHERE expires_at < now() - interval '1 day'"
            )
            row = connection.execute(
                """
                INSERT INTO security.login_challenge (
                    expected_offset, client_fingerprint_hash, expires_at
                ) VALUES (%s, %s, %s)
                RETURNING challenge_id
                """,
                (target, digest(client_fingerprint), expires_at),
            ).fetchone()
        image = self._challenge_image(target)
        return {
            "challenge_id": str(row["challenge_id"]),
            "image": "data:image/png;base64," + base64.b64encode(image).decode("ascii"),
            "track_width": 280,
            "piece_width": 42,
            "expires_in": CHALLENGE_TTL_SECONDS,
        }

    def _consume_challenge(
        self, challenge_id: UUID, offset: int, client_fingerprint: str,
    ) -> None:
        with self.database.connection() as connection:
            row = connection.execute(
                """
                UPDATE security.login_challenge
                SET used_at = now(), attempts = attempts + 1
                WHERE challenge_id = %s AND used_at IS NULL AND expires_at > now()
                RETURNING expected_offset, client_fingerprint_hash
                """,
                (challenge_id,),
            ).fetchone()
        if row is None:
            raise AuthenticationError("滑块验证已过期，请重新验证")
        fingerprint_matches = hmac.compare_digest(
            row["client_fingerprint_hash"].strip(), digest(client_fingerprint)
        )
        if not fingerprint_matches or abs(row["expected_offset"] - offset) > CHALLENGE_TOLERANCE:
            raise AuthenticationError("滑块位置不正确，请重试")

    def login(
        self,
        username: str,
        password: str,
        challenge_id: UUID,
        challenge_offset: int,
        client_fingerprint: str,
    ) -> dict[str, Any]:
        normalized = username.strip()
        token = secrets.token_urlsafe(48)
        csrf_token = secrets.token_urlsafe(32)
        expires_at = utcnow() + timedelta(hours=SESSION_HOURS)
        self._consume_challenge(challenge_id, challenge_offset, client_fingerprint)
        authentication_failed = False
        with self.database.connection() as connection:
            row = connection.execute(
                "SELECT * FROM security.user_account WHERE lower(username) = lower(%s) FOR UPDATE",
                (normalized,),
            ).fetchone()
            encoded = row["password_hash"] if row is not None else _DUMMY_PASSWORD_HASH
            password_matches = verify_password(password, encoded)
            account_locked = bool(
                row is not None
                and row["locked_until"] is not None
                and row["locked_until"] > utcnow()
            )
            if (
                row is None
                or row["status"] != "active"
                or account_locked
                or not password_matches
            ):
                if (
                    row is not None
                    and row["status"] == "active"
                    and not account_locked
                    and not password_matches
                ):
                    failed = row["failed_login_attempts"] + 1
                    locked_until = (
                        utcnow() + timedelta(minutes=LOCKOUT_MINUTES)
                        if failed >= MAX_LOGIN_ATTEMPTS else None
                    )
                    connection.execute(
                        """
                        UPDATE security.user_account
                        SET failed_login_attempts = %s, locked_until = %s, updated_by = username
                        WHERE user_id = %s
                        """,
                        (failed, locked_until, row["user_id"]),
                    )
                authentication_failed = True
            else:
                connection.execute(
                    """
                    UPDATE security.user_account
                    SET failed_login_attempts = 0, locked_until = NULL,
                        last_login_at = now(), updated_by = username
                    WHERE user_id = %s
                    """,
                    (row["user_id"],),
                )
                connection.execute(
                    """
                    INSERT INTO security.user_session (
                        user_id, token_hash, csrf_token_hash, client_fingerprint_hash, expires_at
                    ) VALUES (%s, %s, %s, %s, %s)
                    """,
                    (
                        row["user_id"], digest(token), digest(csrf_token),
                        digest(client_fingerprint), expires_at,
                    ),
                )
                row = connection.execute(
                    "SELECT * FROM security.user_account WHERE user_id = %s", (row["user_id"],)
                ).fetchone()
        if authentication_failed:
            raise AuthenticationError("用户名或密码错误")
        return {
            "session_token": token,
            "csrf_token": csrf_token,
            "expires_at": expires_at,
            "user": public_user(row),
        }

    def authenticate_session(self, token: str | None, client_fingerprint: str) -> dict[str, Any]:
        if not token:
            raise AuthenticationError("请先登录")
        with self.database.connection() as connection:
            row = connection.execute(
                """
                SELECT account.*, session.session_id, session.csrf_token_hash,
                       session.client_fingerprint_hash, session.last_seen_at
                FROM security.user_session session
                JOIN security.user_account account ON account.user_id = session.user_id
                WHERE session.token_hash = %s
                  AND session.revoked_at IS NULL
                  AND session.expires_at > now()
                  AND account.status = 'active'
                """,
                (digest(token),),
            ).fetchone()
            if row is None or not hmac.compare_digest(
                row["client_fingerprint_hash"].strip(), digest(client_fingerprint)
            ):
                raise AuthenticationError("登录已失效，请重新登录")
            if row["last_seen_at"] < utcnow() - timedelta(minutes=5):
                connection.execute(
                    "UPDATE security.user_session SET last_seen_at = now() WHERE session_id = %s",
                    (row["session_id"],),
                )
        user = public_user(row)
        user["session_id"] = str(row["session_id"])
        user["csrf_token_hash"] = row["csrf_token_hash"].strip()
        return user

    @staticmethod
    def _require_active_user(connection: Any, user_id: UUID, *, lock: bool) -> dict[str, Any]:
        suffix = " FOR UPDATE" if lock else ""
        row = connection.execute(
            "SELECT user_id, status FROM security.user_account WHERE user_id = %s" + suffix,
            (user_id,),
        ).fetchone()
        if row is None:
            raise ValueError("用户不存在")
        if row["status"] != "active":
            raise AuthenticationError("用户已停用")
        return row

    @staticmethod
    def _active_api_key(connection: Any, user_id: UUID) -> dict[str, Any] | None:
        return connection.execute(
            """
            SELECT api_key_id, key_prefix, created_at, last_used_at, revoked_at
            FROM security.api_key
            WHERE user_id = %s AND revoked_at IS NULL
            """,
            (user_id,),
        ).fetchone()

    def get_api_key_status(self, user_id: UUID) -> dict[str, Any]:
        with self.database.connection() as connection:
            self._require_active_user(connection, user_id, lock=False)
            row = self._active_api_key(connection, user_id)
        return public_api_key(row)

    def create_api_key(self, user_id: UUID) -> dict[str, Any]:
        with self.database.connection() as connection:
            self._require_active_user(connection, user_id, lock=True)
            if self._active_api_key(connection, user_id) is not None:
                raise ValueError("API Key 已存在，请使用刷新操作")
            token, prefix = generate_api_key()
            row = connection.execute(
                """
                INSERT INTO security.api_key (user_id, key_prefix, key_hash)
                VALUES (%s, %s, %s)
                RETURNING api_key_id, key_prefix, created_at, last_used_at, revoked_at
                """,
                (user_id, prefix, digest(token)),
            ).fetchone()
        result = public_api_key(row)
        result["api_key"] = token
        return result

    def rotate_api_key(self, user_id: UUID) -> dict[str, Any]:
        with self.database.connection() as connection:
            self._require_active_user(connection, user_id, lock=True)
            revoked = connection.execute(
                """
                UPDATE security.api_key
                SET revoked_at = now()
                WHERE user_id = %s AND revoked_at IS NULL
                RETURNING api_key_id
                """,
                (user_id,),
            ).fetchone()
            if revoked is None:
                raise ValueError("API Key 不存在，请先创建")
            token, prefix = generate_api_key()
            row = connection.execute(
                """
                INSERT INTO security.api_key (user_id, key_prefix, key_hash)
                VALUES (%s, %s, %s)
                RETURNING api_key_id, key_prefix, created_at, last_used_at, revoked_at
                """,
                (user_id, prefix, digest(token)),
            ).fetchone()
        result = public_api_key(row)
        result["api_key"] = token
        return result

    def revoke_api_key(self, user_id: UUID) -> dict[str, Any]:
        with self.database.connection() as connection:
            self._require_active_user(connection, user_id, lock=True)
            connection.execute(
                """
                UPDATE security.api_key
                SET revoked_at = now()
                WHERE user_id = %s AND revoked_at IS NULL
                """,
                (user_id,),
            )
        return public_api_key(None)

    def authenticate_api_key(self, token: str | None) -> dict[str, Any]:
        if not token or not API_KEY_PATTERN.fullmatch(token):
            raise AuthenticationError("API Key 无效或已撤销")
        with self.database.connection() as connection:
            row = connection.execute(
                """
                SELECT account.*, api_key.api_key_id, api_key.key_prefix
                FROM security.api_key api_key
                JOIN security.user_account account ON account.user_id = api_key.user_id
                WHERE api_key.key_hash = %s
                  AND api_key.revoked_at IS NULL
                  AND account.status = 'active'
                FOR UPDATE OF api_key
                """,
                (digest(token),),
            ).fetchone()
            if row is None:
                raise AuthenticationError("API Key 无效或已撤销")
            connection.execute(
                "UPDATE security.api_key SET last_used_at = now() WHERE api_key_id = %s",
                (row["api_key_id"],),
            )
        user = public_user(row)
        user["authentication_method"] = "api_key"
        user["api_key_id"] = str(row["api_key_id"])
        user["api_key_prefix"] = row["key_prefix"]
        return user

    def verify_csrf(self, user: dict[str, Any], header_token: str | None, cookie_token: str | None) -> None:
        if not header_token or not cookie_token or not hmac.compare_digest(header_token, cookie_token):
            raise AuthorizationError("请求校验失败，请刷新页面后重试")
        if not hmac.compare_digest(digest(header_token), user["csrf_token_hash"]):
            raise AuthorizationError("请求校验失败，请重新登录")

    def logout(self, session_id: UUID) -> None:
        with self.database.connection() as connection:
            connection.execute(
                "UPDATE security.user_session SET revoked_at = now() WHERE session_id = %s",
                (session_id,),
            )

    def create_user(
        self, username: str, display_name: str, password: str, role: str, actor: str,
    ) -> dict[str, Any]:
        username = validate_username(username)
        validate_password(password, username)
        if role not in PERMISSION_MATRIX:
            raise ValueError("未知用户角色")
        if not display_name.strip():
            raise ValueError("显示名称不能为空")
        with self.database.connection() as connection:
            row = connection.execute(
                """
                INSERT INTO security.user_account (
                    username, display_name, password_hash, role, created_by, updated_by
                ) VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING *
                """,
                (username, display_name.strip(), hash_password(password), role, actor, actor),
            ).fetchone()
        return public_user(row)

    def list_users(self) -> list[dict[str, Any]]:
        with self.database.connection() as connection:
            rows = connection.execute(
                "SELECT * FROM security.user_account ORDER BY status, role, lower(username)"
            ).fetchall()
        return [public_user(row) for row in rows]

    def update_user(
        self,
        user_id: UUID,
        row_version: int,
        values: dict[str, Any],
        actor: str,
    ) -> dict[str, Any]:
        allowed = {"display_name", "role", "status", "password"}
        if set(values) - allowed:
            raise ValueError("包含不支持的用户字段")
        with self.database.connection() as connection:
            before = connection.execute(
                "SELECT * FROM security.user_account WHERE user_id = %s FOR UPDATE", (user_id,)
            ).fetchone()
            if before is None:
                raise ValueError("用户不存在")
            if before["row_version"] != row_version:
                raise ValueError("用户信息已变化，请刷新后重试")
            role = values.get("role", before["role"])
            status = values.get("status", before["status"])
            if role not in PERMISSION_MATRIX or status not in {"active", "disabled"}:
                raise ValueError("用户角色或状态无效")
            if before["role"] == "admin" and before["status"] == "active" and (
                role != "admin" or status != "active"
            ):
                active_admins = connection.execute(
                    "SELECT count(*) AS count FROM security.user_account WHERE role = 'admin' AND status = 'active'"
                ).fetchone()["count"]
                if active_admins <= 1:
                    raise ValueError("不能停用或降级最后一个活动管理员")
            display_name = values.get("display_name", before["display_name"]).strip()
            if not display_name:
                raise ValueError("显示名称不能为空")
            password_hash = before["password_hash"]
            password_changed_at = before["password_changed_at"]
            if values.get("password"):
                validate_password(values["password"], before["username"])
                password_hash = hash_password(values["password"])
                password_changed_at = utcnow()
            row = connection.execute(
                """
                UPDATE security.user_account
                SET display_name = %s, role = %s, status = %s,
                    password_hash = %s, password_changed_at = %s, updated_by = %s
                WHERE user_id = %s AND row_version = %s
                RETURNING *
                """,
                (
                    display_name, role, status, password_hash, password_changed_at,
                    actor, user_id, row_version,
                ),
            ).fetchone()
            if row is None:
                raise ValueError("用户信息已变化，请刷新后重试")
            if values.get("password") or status == "disabled":
                connection.execute(
                    """
                    UPDATE security.user_session SET revoked_at = now()
                    WHERE user_id = %s AND revoked_at IS NULL
                    """,
                    (user_id,),
                )
            if status == "disabled":
                connection.execute(
                    """
                    UPDATE security.api_key SET revoked_at = now()
                    WHERE user_id = %s AND revoked_at IS NULL
                    """,
                    (user_id,),
                )
        return public_user(row)

    def change_password(self, user_id: UUID, old_password: str, new_password: str) -> None:
        with self.database.connection() as connection:
            row = connection.execute(
                "SELECT * FROM security.user_account WHERE user_id = %s FOR UPDATE", (user_id,)
            ).fetchone()
            if row is None or not verify_password(old_password, row["password_hash"]):
                raise AuthenticationError("原密码不正确")
            validate_password(new_password, row["username"])
            if verify_password(new_password, row["password_hash"]):
                raise ValueError("新密码不能与当前密码相同")
            connection.execute(
                """
                UPDATE security.user_account
                SET password_hash = %s, password_changed_at = now(), updated_by = username
                WHERE user_id = %s
                """,
                (hash_password(new_password), user_id),
            )
            connection.execute(
                "UPDATE security.user_session SET revoked_at = now() WHERE user_id = %s AND revoked_at IS NULL",
                (user_id,),
            )
