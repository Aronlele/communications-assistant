"""Validate product API configuration and place it in the app without logging values."""
import argparse
import json
import os
import re
from pathlib import Path


def configure(source, destination, release=False):
    destination = Path(destination)
    if source is not None and Path(source).resolve() == destination.resolve():
        raise ValueError("配置来源不能与应用内的输出文件相同")
    # Reused staging directories must never retain credentials from a previous build.
    destination.unlink(missing_ok=True)
    if source is None or not Path(source).is_file():
        if release or source is not None:
            raise ValueError("产品 API 配置文件缺失；正式发布需要成功申请的配置")
        return False
    try:
        payload = json.loads(Path(source).read_text(encoding="utf-8"))
        api_id = payload["api_id"]
        api_hash = payload["api_hash"].strip()
        if type(api_id) is not int or not 0 < api_id <= 2147483647:
            raise ValueError()
        if not isinstance(api_hash, str) or not re.fullmatch(r"[0-9a-fA-F]{32}", api_hash):
            raise ValueError()
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        raise ValueError("产品 API 配置无效；需要正整数 api_id 和 32 位十六进制 api_hash") from None
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps({"api_id": api_id, "api_hash": api_hash}) + "\n", encoding="utf-8")
    # Bundled client configuration must remain readable after a root-owned PKG install.
    # The private build source and user Keychain credentials keep their own permissions.
    os.chmod(destination, 0o644)
    return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--release", action="store_true")
    args = parser.parse_args()
    try:
        configured = configure(args.source, args.destination, args.release)
    except ValueError as error:
        parser.exit(1, str(error) + "\n")
    print("产品内置 API 已配置" if configured else "开发包未配置产品 API：仅可体验演示或使用高级设置中的自定义 API")


if __name__ == "__main__":
    main()
