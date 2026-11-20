from rest_framework_simplejwt.authentication import JWTAuthentication as BaseJWTAuthentication
from rest_framework_simplejwt.exceptions import InvalidToken

from accounts.serializers import token_is_revoked


class JWTAuthentication(BaseJWTAuthentication):
    """Rejects access tokens whose token version predates the user's last password change."""

    def get_user(self, validated_token):
        user = super().get_user(validated_token)
        if token_is_revoked(validated_token, user):
            raise InvalidToken("Token has been revoked.")
        return user
