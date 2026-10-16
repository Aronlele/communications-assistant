import platform
import unicodedata

VERSION = "0.6.0"
DEFAULT_PROFILE = {"device_model": "即讯 macOS", "system_version": "macOS " + (platform.mac_ver()[0] or "14+"), "app_version": VERSION}


def validate_profile(values):
    result = {}
    for key, default in DEFAULT_PROFILE.items():
        value = values.get(key, default)
        if not isinstance(value, str):
            raise ValueError("设备信息必须为文本")
        value = value.strip() or default
        if len(value) > 128 or any(unicodedata.category(c).startswith("C") for c in value):
            raise ValueError("设备信息限 128 个字符，不得包含换行或控制字符")
        result[key] = value
    return result
