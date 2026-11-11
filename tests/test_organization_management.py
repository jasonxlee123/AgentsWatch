import unittest

from pydantic import ValidationError

from agentswatch_api.main import DepartmentUpdate, PersonUpdate
from agentswatch_api.organization_management import normalize_required_text


class OrganizationManagementUnitTests(unittest.TestCase):
    def test_external_identity_fields_are_not_accepted_on_update(self):
        with self.assertRaises(ValidationError):
            DepartmentUpdate(row_version=1, department_code="NEW-CODE", name="新名称")
        with self.assertRaises(ValidationError):
            PersonUpdate(row_version=1, employee_no="999999", display_name="新名称")

    def test_required_status_and_name_fields_cannot_be_explicitly_cleared(self):
        for payload in (
            {"row_version": 1, "name": None},
            {"row_version": 1, "is_active": None},
        ):
            with self.assertRaises(ValidationError):
                DepartmentUpdate(**payload)
        for payload in (
            {"row_version": 1, "display_name": None},
            {"row_version": 1, "employment_status": None},
        ):
            with self.assertRaises(ValidationError):
                PersonUpdate(**payload)

    def test_nullable_relationship_fields_can_be_cleared(self):
        department = DepartmentUpdate(row_version=1, parent_department_id=None)
        person = PersonUpdate(row_version=1, primary_department_id=None)

        self.assertIn("parent_department_id", department.model_fields_set)
        self.assertIn("primary_department_id", person.model_fields_set)

    def test_required_text_is_trimmed_and_empty_value_is_rejected(self):
        self.assertEqual(normalize_required_text("  DEP-OPS  ", "department_code"), "DEP-OPS")
        with self.assertRaisesRegex(ValueError, "must not be empty"):
            normalize_required_text("   ", "department_code")


if __name__ == "__main__":
    unittest.main()
