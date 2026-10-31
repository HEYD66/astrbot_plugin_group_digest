"""Build a self-contained installable ZIP. Credentials/data are never packaged."""
import base64
import hashlib
import json
import zipfile
from pathlib import Path

root = Path(__file__).parent
dest = root / "dist"
dest.mkdir(exist_ok=True)
files = [root / n for n in ("main.py", "engine.py", "settings.py", "storage.py", "backfill.py", "forwarding.py", "summary_image.py", "group_files.py", "_conf_schema.json", "metadata.yaml", "README.md", "LICENSE")]
files += [p for folder in ("views", ".astrbot-plugin") for p in (root / folder).rglob("*") if p.is_file()]
archive = dest / "astrbot_plugin_group_digest-v1.8.3.zip"
with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as z:
    for path in files:
        z.write(path, str(path.relative_to(root)).replace("\\", "/"))
with zipfile.ZipFile(archive) as z:
    assert z.testzip() is None
payload = {"archive": base64.b64encode(archive.read_bytes()).decode(), "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
           "tests": (root / "tests/test_flow.py").read_text(encoding="utf-8"),
           "backfill_tests": (root / "tests/test_backfill.py").read_text(encoding="utf-8"),
           "forward_tests": (root / "tests/test_forwarding.py").read_text(encoding="utf-8"),
           "sdk_probe": (root / "tests/sdk_probe.py").read_text(encoding="utf-8")}
(dest / "deploy-payload.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
print(json.dumps({"archive": str(archive.resolve()), "sha256": payload["sha256"], "files": len(files)}, ensure_ascii=False))
