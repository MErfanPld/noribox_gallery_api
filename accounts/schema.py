from drf_spectacular.contrib.rest_framework_simplejwt import SimpleJWTScheme


class NoriboxJWTScheme(SimpleJWTScheme):
    target_class = "accounts.authentication.JWTAuthentication"
    name = "jwtAuth"
