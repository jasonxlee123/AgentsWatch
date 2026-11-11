import base64
import io
import unittest

from PIL import Image

from agentswatch_api.auth import (
    AuthService,
    hash_password,
    password_policy_errors,
    permissions_for_role,
    validate_password,
    validate_username,
    verify_password,
)


class AuthenticationTests(unittest.TestCase):
    def test_password_policy_requires_every_character_class(self):
        errors = password_policy_errors("alllowercase")

        self.assertIn("至少一个大写字母", errors)
        self.assertIn("至少一个数字", errors)
        self.assertIn("至少一个符号", errors)

    def test_password_policy_accepts_strong_password_and_rejects_username(self):
        validate_password("Safe!Control2026", "operator")

        with self.assertRaisesRegex(ValueError, "不能包含用户名"):
            validate_password("Admin!Secure2026", "admin")

    def test_password_hash_is_salted_and_verifiable(self):
        first = hash_password("Safe!Control2026")
        second = hash_password("Safe!Control2026")

        self.assertNotEqual(first, second)
        self.assertTrue(verify_password("Safe!Control2026", first))
        self.assertFalse(verify_password("Wrong!Control2026", first))
        self.assertFalse(verify_password("Safe!Control2026", "not-a-password-hash"))

    def test_username_and_role_permissions_are_validated(self):
        self.assertEqual(validate_username("ops.user-1"), "ops.user-1")
        with self.assertRaises(ValueError):
            validate_username("1invalid")

        self.assertEqual(permissions_for_role("viewer"), ["view"])
        self.assertIn("execute_automation", permissions_for_role("operator"))
        self.assertIn("manage_users", permissions_for_role("admin"))

    def test_slider_challenge_is_a_raster_image_without_embedded_offset_metadata(self):
        image_bytes = AuthService._challenge_image(120)
        image = Image.open(io.BytesIO(image_bytes))

        self.assertEqual(image.format, "PNG")
        self.assertEqual(image.size, (280, 80))
        encoded = base64.b64encode(image_bytes).decode("ascii")
        self.assertNotIn("120", encoded[:100])


if __name__ == "__main__":
    unittest.main()
