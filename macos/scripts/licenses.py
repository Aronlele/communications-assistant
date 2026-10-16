import importlib.metadata
import sys
import builtins
from pathlib import Path


with Path(sys.argv[1]).open("w", encoding="utf-8") as output:
    output.write("即讯包含以下公开运行时与依赖。\nPython: https://docs.python.org/3/license.html\n")
    builtins.license._Printer__setup()
    output.write("\n".join(builtins.license._Printer__lines) + "\n")
    for name in ("Telethon", "pyaes", "rsa", "pyasn1", "PyInstaller", "cryptography", "cffi", "pycparser", "python-socks"):
        distribution = importlib.metadata.distribution(name)
        output.write(f"\n{'=' * 40}\n{name} {distribution.version}\n")
        metadata = distribution.metadata
        output.write(f"{metadata.get('Home-page', '')}\n{metadata.get('License', '')}\n")
        for file in distribution.files or []:
            if "license" in str(file).lower() or "copying" in str(file).lower():
                path = Path(distribution.locate_file(file))
                if path.is_file() and path.suffix.lower() not in (".py", ".pyc"):
                    output.write(path.read_text(encoding="utf-8", errors="replace") + "\n")
