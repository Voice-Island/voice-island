"""Creates a local copy of a Qwen2.5 MLX model with a fixed tool-calling template.

The template of the mlx-community conversions shows the example tool call with doubled braces
({{"name": ...}}): the model copies them and mlx_lm.server can no longer parse the call. The copy links the
weights by symlink (no duplication) and only rewrites the template.

  uv run fix_qwen_template.py mlx-community/Qwen2.5-3B-Instruct-4bit
  -> web/models/Qwen2.5-3B-Instruct-4bit-tools
"""

import json
import sys
from pathlib import Path

from huggingface_hub import snapshot_download

MODELS_DIR = Path(__file__).resolve().parent.parent / "models"


def fix(template: str) -> str:
    return template.replace('{{\\"name\\"', '{\\"name\\"').replace("<args-json-object>}}", "<args-json-object>}")


def main(repo: str) -> Path:
    src = Path(snapshot_download(repo))
    dst = MODELS_DIR / f"{repo.split('/')[-1]}-tools"
    dst.mkdir(parents=True, exist_ok=True)
    for f in src.iterdir():
        target = dst / f.name
        if target.exists() or target.is_symlink():
            target.unlink()
        if f.name == "tokenizer_config.json":
            config = json.loads(f.read_text())
            if "chat_template" in config:
                config["chat_template"] = fix(config["chat_template"])
            target.write_text(json.dumps(config, indent=2, ensure_ascii=False))
        elif f.name == "chat_template.jinja":
            target.write_text(fix(f.read_text()))
        else:
            target.symlink_to(f.resolve())
    print(dst)
    return dst


if __name__ == "__main__":
    main(sys.argv[1])
