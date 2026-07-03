"""Built-in profile API for appv24."""

from appv24.profiles.builtins import BUILTIN_PROFILES, DEFAULT_PROFILE_ID
from appv24.profiles.resolver import (
    get_profile,
    list_profiles,
    normalize_profile_id,
    profile_coding_app_kwargs,
    validate_builtin_profiles,
)
from appv24.profiles.types import AppProfile, ProfileError

__all__ = [
    "AppProfile",
    "BUILTIN_PROFILES",
    "DEFAULT_PROFILE_ID",
    "ProfileError",
    "get_profile",
    "list_profiles",
    "normalize_profile_id",
    "profile_coding_app_kwargs",
    "validate_builtin_profiles",
]
