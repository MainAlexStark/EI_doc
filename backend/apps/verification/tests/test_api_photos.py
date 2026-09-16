"""Фото поверяемого СИ — apps.verification.api_photos."""

from __future__ import annotations

from django.test import TestCase
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from rest_framework.test import APIClient

from apps.core.models import Employee, Role, User
from apps.verification.models import Verification, VerificationPhoto
from apps.verification.tests import factories as f


def _tiny_jpeg() -> bytes:
    # 1x1 JPEG — Pillow достаточно, чтобы ImageField его принял.
    from io import BytesIO

    from PIL import Image

    buffer = BytesIO()
    Image.new("RGB", (1, 1), "white").save(buffer, format="JPEG")
    return buffer.getvalue()


class VerificationPhotoTestBase(TestCase):
    def setUp(self) -> None:
        self.family = f.make_family()
        user = User.objects.create_user(email="v@ei.test", password="pass12345", role=Role.VERIFIER)
        self.employee = Employee.objects.create(tab_number="11", full_name="Поверитель П. П.", user=user)
        self.verification = f.make_verification(self.family, self.employee, day=1, serial="SN-photo")
        self.api = APIClient()
        self.api.force_authenticate(user)

    def upload(self, **extra):
        photo = SimpleUploadedFile("photo.jpg", _tiny_jpeg(), content_type="image/jpeg")
        data = {"photo": photo, **extra}
        return self.api.post(
            reverse("verification_photos", args=[self.verification.pk]), data, format="multipart"
        )


class VerificationPhotosViewTestCase(VerificationPhotoTestBase):
    def test_upload_creates_photo(self):
        response = self.upload(caption="табличка")
        assert response.status_code == 201
        assert VerificationPhoto.objects.count() == 1
        photo = VerificationPhoto.objects.get()
        assert photo.verification_id == self.verification.id
        assert photo.uploaded_by_id == self.employee.id
        assert photo.caption == "табличка"
        assert response.data["image_url"]

    def test_several_photos_per_verification(self):
        self.upload()
        self.upload()
        response = self.api.get(reverse("verification_photos", args=[self.verification.pk]))
        assert response.status_code == 200
        assert len(response.data) == 2

    def test_requires_linked_employee(self):
        user = User.objects.create_user(email="obs@ei.test", password="pass12345", role=Role.OBSERVER)
        api = APIClient()
        api.force_authenticate(user)
        photo = SimpleUploadedFile("photo.jpg", _tiny_jpeg(), content_type="image/jpeg")
        response = api.post(
            reverse("verification_photos", args=[self.verification.pk]), {"photo": photo}, format="multipart"
        )
        assert response.status_code == 403

    def test_missing_verification_is_404(self):
        photo = SimpleUploadedFile("photo.jpg", _tiny_jpeg(), content_type="image/jpeg")
        response = self.api.post(
            reverse("verification_photos", args=[999999]), {"photo": photo}, format="multipart"
        )
        assert response.status_code == 404

    def test_requires_authentication(self):
        api = APIClient()
        photo = SimpleUploadedFile("photo.jpg", _tiny_jpeg(), content_type="image/jpeg")
        response = api.post(
            reverse("verification_photos", args=[self.verification.pk]), {"photo": photo}, format="multipart"
        )
        assert response.status_code == 401


class VerificationPhotoDetailAndImageTestCase(VerificationPhotoTestBase):
    def setUp(self) -> None:
        super().setUp()
        self.photo_id = self.upload().data["id"]

    def test_image_streams_the_stored_photo(self):
        response = self.api.get(reverse("verification_photo_image", args=[self.photo_id]))
        assert response.status_code == 200

    def test_delete_removes_photo(self):
        response = self.api.delete(reverse("verification_photo_detail", args=[self.photo_id]))
        assert response.status_code == 204
        assert VerificationPhoto.objects.count() == 0

    def test_delete_missing_is_404(self):
        response = self.api.delete(reverse("verification_photo_detail", args=[999999]))
        assert response.status_code == 404
