from rest_framework_simplejwt.views import TokenObtainPairView

from apps.sync.auth import DeviceTokenSerializer


class DeviceTokenView(TokenObtainPairView):
    """POST /api/auth/device-token/ — вход с полевого устройства (refresh на 30 дней)."""

    serializer_class = DeviceTokenSerializer
